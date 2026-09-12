"""Authorized local presentation of a real SimCord world."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import secrets
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from ..actors import MemberActor
from ..backend.errors import BackendError, SetupError
from ..backend.models import Interaction, Message
from ..builders import ChannelHandle, UserHandle
from ..components import walk_components
from ..enums import ComponentType
from ..results import InteractionResult, ResponseMessage
from ._capture import CapturePin, ManagedCapture
from ._media import MediaError, MediaWorker
from ._server import PreviewServer
from ._snapshot import build_snapshot, can_access_channel, can_access_message


def _freeze_capture(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_capture(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_capture(item) for item in value)
    return value


def _capture_destination(path: Any) -> Path:
    try:
        destination = Path(path)
    except TypeError as exc:
        raise SetupError("capture path must be filesystem-like") from exc
    if os.path.isdir(destination):
        raise SetupError("capture path must be a file")
    if not os.path.isdir(destination.parent):
        raise SetupError("capture destination directory does not exist")
    return destination


def _content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


@dataclass(frozen=True, slots=True)
class PreviewCapture:
    """Immutable metadata returned by the optional screenshot extra."""

    path: str
    published_revision: int
    render_generation: int
    viewer_id: str
    channel_id: str
    target_id: str | None
    mode: str = "surface"
    complete: bool = False
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    modal_id: str | None = None
    profile: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    geometry: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    output_width: int = 0
    output_height: int = 0
    ready: bool = False
    calibrated: bool = False
    calibration: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    action: Mapping[str, Any] | None = None


@dataclass(slots=True)
class _Action:
    sequence: int
    request_id: str
    fingerprint: str
    response: dict[str, Any] | None = None
    interaction: Interaction | None = None


@dataclass(slots=True)
class _Page:
    preview: Preview
    id: str
    viewer: Any
    channel_id: int
    target_id: int | None = None
    generation: int = 1
    revision: int = 0
    status: str = "current"
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    last_action: dict[str, Any] | None = None
    modal: InteractionResult | None = None
    modal_handle: str | None = None
    last_sequence: int = 0
    latest_action: _Action | None = None
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    asset_blobs: dict[str, tuple[str, bytes, str]] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    pinned_snapshot: dict[str, Any] | None = None
    pinned_generation: int | None = None
    pinned_attachment_ids: dict[str, str] = field(default_factory=dict)

    def asset_id(self, key: str, metadata: Mapping[str, Any]) -> str:
        existing = next((asset for asset, item in self.assets.items() if item.get("key") == key), None)
        if existing is not None:
            return existing
        asset = "a_" + secrets.token_urlsafe(12)
        filename = str(metadata.get("filename", "asset"))
        content_type = str(metadata.get("content_type") or "application/octet-stream")
        if content_type == "application/octet-stream":
            content_type = _content_type(filename if filename != "asset" else str(metadata.get("url", "")))
        self.assets[asset] = {
            "id": asset,
            "filename": filename,
            "contentType": content_type,
            "key": key,
            "available": False,
        }
        blob: bytes | None = None
        url = metadata.get("url")
        if isinstance(url, str):
            blob = self.preview.env.backend.cdn.get(url)
            if blob is None and (supplied := self.preview.explicit_assets.get(url)) is not None:
                filename, blob = supplied
        if blob is not None:
            if self.preview._retained_media_bytes + len(blob) > Preview._MAX_MEDIA_BYTES:
                self.assets[asset]["diagnostic"] = "session media budget exceeded"
            else:
                self.preview._retained_media_bytes += len(blob)
                if content_type == "application/octet-stream":
                    content_type = _content_type(filename)
                self.asset_blobs[asset] = (filename, blob, content_type)
                self.assets[asset].update(
                    {"filename": filename, "contentType": content_type, "available": True, "bytes": len(blob)}
                )
        return asset


class Preview:
    """An async context manager owning one bounded, local preview session."""

    _MAX_PAGES = 16
    _MAX_MEDIA_BYTES = 128 * 1024 * 1024
    _LOCALES: ClassVar[set[str]] = {"en-US", "en-GB", "de", "de-DE", "es-ES", "fr", "fr-FR", "ja", "ja-JP"}

    def __init__(
        self,
        env: Any,
        channel: ChannelHandle,
        viewers: tuple[Any, ...],
        *,
        theme: str,
        width: int,
        height: int,
        locale: str,
        timezone: str,
        assets: Mapping[str, tuple[str, bytes]] | None,
    ) -> None:
        self.env = env
        self.channel = channel
        self.viewers = viewers
        self.theme = theme
        self.width = width
        self.height = height
        self.locale = locale
        self.timezone = timezone
        self.explicit_assets = dict(assets or {})
        self.capability = secrets.token_urlsafe(32)
        self._server = PreviewServer(self)
        self._pages: dict[str, _Page] = {}
        self._python: _Page | None = None
        self._active = False
        self._closed = False
        self._closed_event = asyncio.Event()
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._unregister_shutdown: Any = None
        self._unregister_dispatch: Any = None
        self._active_action: _Action | None = None
        self._media_worker: MediaWorker | None = None
        self._retained_media_bytes = 0
        self._active_task: asyncio.Task[Any] | None = None
        self._capture_manager = ManagedCapture(self)
        self._capture_task: asyncio.Task[Any] | None = None
        self._capture_page: _Page | None = None
        self._capture_generation = 0

    @property
    def url(self) -> str:
        if self._server.port is None:
            raise SetupError("Preview is not entered")
        return f"http://127.0.0.1:{self._server.port}/#{self.capability}"

    @property
    def origin(self) -> str:
        if self._server.port is None:
            return ""
        return f"http://127.0.0.1:{self._server.port}"

    async def __aenter__(self) -> Preview:
        if self._active or self._closed:
            raise SetupError("Preview is already entered or closed")
        self.env._preview = self
        try:
            await self._server.start()
            self._python = _Page(self, "python", self.viewers[0], self.channel.id)
            self._python.target_id = self._initial_target(self._python)
            self._pages[self._python.id] = self._python
            self._publish(self._python)
            self._unregister_shutdown = self.env._register_pre_shutdown(self.close)
            self._unregister_dispatch = self.env._register_dispatch_observer(self._on_dispatch)
            self._active = True
            return self
        except BaseException:
            self.env._preview = None
            await self._server.close()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _initial_target(self, page: _Page) -> int | None:
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            return None
        messages = self.env.backend.messages.get(page.channel_id, {})
        visible = [
            item
            for item in messages.values()
            if can_access_message(self.env, page.channel_id, item, page.viewer, history=True)
        ]
        bot_id = self.env.backend.bot_user.id
        bot_messages = [item for item in visible if item.author_id == bot_id]
        return (
            max(bot_messages or visible, key=lambda item: item.id).id if (bot_messages or visible) else None
        )

    def _publish(self, page: _Page) -> None:
        page.revision += 1
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            page.status = "access_denied"
        elif page.status == "access_denied":
            page.status = "current"
        page.snapshot = build_snapshot(self, page)

    def _clear_page_assets(self, page: _Page) -> None:
        released = sum(len(body) for _, body, _ in page.asset_blobs.values())
        released += sum(int(item.get("normalizedBytes", 0)) for item in page.assets.values())
        self._retained_media_bytes = max(0, self._retained_media_bytes - released)
        page.assets.clear()
        page.asset_blobs.clear()

    def _assert_capture_live(self, page: _Page) -> None:
        if self._closed:
            raise SetupError("managed capture was closed")
        if page.pinned_snapshot is None:
            return
        if page.pinned_generation != self.env._generation:
            raise SetupError("managed capture was invalidated by bot restart")
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            raise SetupError("managed capture access was revoked")
        if page.target_id is not None:
            try:
                message = self.env.backend.get_message(page.channel_id, page.target_id)
            except BackendError as exc:
                raise SetupError("managed capture target is unavailable") from exc
            if not can_access_message(self.env, page.channel_id, message, page.viewer, history=True):
                raise SetupError("managed capture target access was revoked")
        for asset_id, attachment_id in page.pinned_attachment_ids.items():
            try:
                message = self.env.backend.get_message(page.channel_id, page.target_id or 0)
            except BackendError as exc:
                raise SetupError("managed capture asset is unavailable") from exc
            if not any(str(item.get("id", "")) == attachment_id for item in message.attachments):
                raise SetupError(f"managed capture asset {asset_id} is unavailable")

    def page_payload(self, page: _Page) -> dict[str, Any]:
        if page.pinned_snapshot is not None:
            self._assert_capture_live(page)
            return json.loads(json.dumps(page.pinned_snapshot))
        allowed = can_access_channel(self.env, page.channel_id, page.viewer, history=True)
        status = "current" if allowed else "access_denied"
        if page.status != status:
            page.status = status
            self._clear_page_assets(page)
            self._publish(page)
        return json.loads(json.dumps(page.snapshot))

    def get_page(self, context_id: str | None) -> _Page:
        if not isinstance(context_id, str) or context_id not in self._pages:
            raise SetupError("preview context is expired or unknown")
        page = self._pages[context_id]
        if self._closed:  # pragma: no cover - close clears the page registry
            raise SetupError("Preview is closed")
        return page

    def open_page(self, viewer_id: Any = None, target_id: Any = None) -> _Page:
        if not self._active or self._closed:
            raise SetupError("Preview is not active")
        if len(self._pages) >= self._MAX_PAGES + 1:  # Python presentation is not interactive.
            raise SetupError("Preview page limit reached")
        source = cast(_Page, self._python)
        viewer = self.viewers[0] if viewer_id is None else self._viewer(viewer_id)
        if target_id is None:
            target = source.target_id
            if target is not None:
                message = self.env.backend.get_message(self.channel.id, target)
                if not can_access_message(self.env, self.channel.id, message, viewer, history=True):
                    target = self._initial_target(_Page(self, "tmp", viewer, self.channel.id))
        else:
            target = self._target_id(target_id)
        page = _Page(self, "p_" + secrets.token_urlsafe(12), viewer, self.channel.id, target)
        if source.modal is not None and source.modal._interaction.user_id == viewer.id:
            page.modal = source.modal
            page.modal_handle = "m_" + secrets.token_urlsafe(12)
        self._pages[page.id] = page
        self._publish(page)
        return page

    def close_page(self, context_id: str) -> None:
        if context_id == "python":
            raise SetupError("The Python presentation cannot be closed as a page")
        self._pages.pop(context_id, None)

    def _viewer(self, viewer_id: Any) -> Any:
        try:
            value = int(viewer_id)
        except (TypeError, ValueError) as exc:
            raise SetupError("unknown preview viewer") from exc
        for viewer in self.viewers:
            if viewer.id == value:
                return viewer
        raise SetupError("viewer is not authorized for this Preview")

    def _target_id(self, target_id: Any, viewer: Any = None) -> int | None:
        selected_viewer = cast(_Page, self._python).viewer if viewer is None else viewer
        if target_id is None:
            return self._initial_target(_Page(self, "tmp", selected_viewer, self.channel.id))
        try:
            target = int(target_id)
        except (TypeError, ValueError) as exc:
            raise SetupError("target_id must be a snowflake string") from exc
        try:
            message = self.env.backend.get_message(self.channel.id, target)
        except BackendError as exc:
            raise SetupError("target message is unavailable") from exc
        if not can_access_message(self.env, self.channel.id, message, selected_viewer, history=True):
            raise SetupError("target message is not accessible")
        return target

    async def show(self, target: Any) -> None:
        if not self._active or self._python is None:
            raise SetupError("Preview is not active")
        token = self.env._begin_operation("preview.show")
        try:
            page = self._python
            result: InteractionResult | None = target if isinstance(target, InteractionResult) else None
            if result is not None:
                if result._env is not self.env:
                    raise SetupError("target belongs to another Env")
                if result.modal is not None:
                    if result._interaction.user_id != page.viewer.id:
                        raise SetupError("a modal can only be shown to its opener")
                    if result._interaction.channel_id != page.channel_id:
                        raise SetupError("target belongs to another channel")
                    page.modal = result
                    page.modal_handle = "m_" + secrets.token_urlsafe(12)
                    page.target_id = result._interaction.source_message_id
                elif result.response is not None:
                    target = result.response
                else:
                    raise SetupError("interaction has no presentable response")
            if isinstance(target, ResponseMessage):
                if target._env is not self.env or target.channel_id != page.channel_id:
                    raise SetupError("target belongs to another Env or channel")
                stored = self.env.backend.get_message(target.channel_id, target.id)
                if not can_access_message(self.env, page.channel_id, stored, page.viewer, history=True):
                    raise SetupError("target message is not accessible")
                page.target_id = stored.id
            elif isinstance(target, discord.Message):
                if target.channel is None or target.channel.id != page.channel_id:
                    raise SetupError("target belongs to another channel")
                stored = self.env.backend.get_message(page.channel_id, target.id)
                if not can_access_message(self.env, page.channel_id, stored, page.viewer, history=True):
                    raise SetupError("target message is not accessible")
                page.target_id = stored.id
            elif result is None:
                raise SetupError("show expects a Message, ResponseMessage, or InteractionResult")
            self._publish(page)
        finally:
            self.env._end_operation(token)

    async def refresh(self) -> None:
        if not self._active or self._closed:
            raise SetupError("Preview is not active")
        token = self.env._begin_operation("preview.refresh")
        try:
            await self.env._settle_internal()
            for page in tuple(self._pages.values()):
                self._publish(page)
        finally:
            self.env._end_operation(token)

    def _capture_viewer(self, viewer: Any) -> Any:
        if viewer is None:
            return cast(_Page, self._python).viewer
        if isinstance(viewer, (MemberActor, UserHandle)):
            if viewer._env is not self.env:
                raise SetupError("capture viewer belongs to another Env")
            return self._viewer(viewer.id)
        return self._viewer(viewer)

    def _capture_target(self, viewer: Any, target: Any) -> tuple[int | None, InteractionResult | None]:
        if target is None:
            return cast(_Page, self._python).target_id, None
        result = target if isinstance(target, InteractionResult) else None
        if result is not None:
            if result._env is not self.env:
                raise SetupError("capture target belongs to another Env")
            if result.modal is not None:
                if result._interaction.user_id != viewer.id:
                    raise SetupError("capture modal opener is not the requested viewer")
                if result._interaction.channel_id != self.channel.id:
                    raise SetupError("capture target belongs to another channel")
                target_id = result._interaction.source_message_id
                return target_id, result
            target = result.response
            if target is None:
                raise SetupError("interaction has no presentable response")
        if isinstance(target, ResponseMessage):
            if target._env is not self.env or target.channel_id != self.channel.id:
                raise SetupError("capture target belongs to another Env or channel")
            target_id = target.id
        elif isinstance(target, discord.Message):
            if target.channel is None or target.channel.id != self.channel.id:
                raise SetupError("capture target belongs to another channel")
            target_id = target.id
        else:
            try:
                target_id = int(cast(Any, target))
            except (TypeError, ValueError) as exc:
                raise SetupError("capture target must be a message, result, or snowflake") from exc
        try:
            message = self.env.backend.get_message(self.channel.id, target_id)
        except BackendError as exc:
            raise SetupError("capture target is unavailable") from exc
        if not can_access_message(self.env, self.channel.id, message, viewer, history=True):
            raise SetupError("capture target is not accessible")
        return target_id, None

    @staticmethod
    def _capture_attachment_ids(snapshot: Mapping[str, Any]) -> dict[str, str]:
        found: dict[str, str] = {}

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                asset_id = value.get("asset_id")
                attachment_id = value.get("attachment_id")
                if isinstance(asset_id, str) and attachment_id is not None:
                    found[asset_id] = str(attachment_id)
                for item in value.get("attachments", ()):
                    found[item["asset_id"]] = str(item.get("id", ""))
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(snapshot.get("selected"))
        return {key: value for key, value in found.items() if value}

    def _pin_capture(self, viewer: Any, target: Any) -> CapturePin:
        if not can_access_channel(self.env, self.channel.id, viewer, history=True):
            raise SetupError("capture viewer cannot access this channel")
        source = cast(_Page, self._python)
        target_id, modal = self._capture_target(viewer, target)
        if modal is None and target is None and source.modal is not None:
            if source.modal._interaction.user_id != viewer.id:
                raise SetupError("capture modal opener is not the requested viewer")
            modal = source.modal
        capture_page = _Page(
            self,
            "capture_" + secrets.token_urlsafe(12),
            viewer,
            self.channel.id,
            target_id,
            generation=source.generation,
            revision=source.revision,
            status=source.status,
        )
        if modal is not None:
            capture_page.modal = modal
            capture_page.modal_handle = "m_" + secrets.token_urlsafe(12)
        snapshot = build_snapshot(self, capture_page)
        if target_id is None and modal is None:
            snapshot["diagnostics"] = [
                *snapshot.get("diagnostics", []),
                {
                    "code": "target-unavailable",
                    "severity": "warning",
                    "message": "No focused message is available for this capture",
                    "complete": False,
                },
            ]
        capture_page.snapshot = snapshot
        capture_page.pinned_snapshot = deepcopy(snapshot)
        capture_page.pinned_generation = self.env._generation
        capture_page.pinned_attachment_ids = self._capture_attachment_ids(snapshot)
        self._pages[capture_page.id] = capture_page
        self._capture_generation += 1
        profile = dict(snapshot.get("profile", {}))
        profile.update({"deviceScale": 1, "reducedMotion": True})
        return CapturePin(
            capture_page,
            capture_page.pinned_snapshot,
            self.env._generation,
            int(snapshot.get("publishedRevision", source.revision)),
            str(snapshot.get("viewerId", viewer.id)),
            str(snapshot.get("channelId", self.channel.id)),
            str(snapshot["targetId"]) if snapshot.get("targetId") is not None else None,
            capture_page.modal_handle,
            profile,
        )

    async def screenshot(
        self,
        path: Any,
        *,
        viewer: Any = None,
        target: Any = None,
        mode: str = "surface",
        allow_incomplete: bool = False,
    ) -> PreviewCapture:
        if mode not in {"surface", "viewport"}:
            raise SetupError("capture mode must be 'surface' or 'viewport'")
        if not isinstance(allow_incomplete, bool):
            raise SetupError("allow_incomplete must be a boolean")
        if not self._active or self._closed:
            raise SetupError("Preview is not active")
        destination = _capture_destination(path)
        if self._capture_task is not None and not self._capture_task.done():
            raise SetupError("Preview is busy with another capture")
        self._capture_task = asyncio.current_task()
        pin: CapturePin | None = None
        try:
            token = self.env._begin_operation("preview.screenshot")
            try:
                await self.env._settle_internal()
                if self._closed or not self._active:
                    raise SetupError("Preview is closing")
                for page in tuple(self._pages.values()):  # pragma: no branch - bounded snapshot pass
                    if page.pinned_snapshot is None:
                        self._publish(page)
                pin = self._pin_capture(self._capture_viewer(viewer), target)
            finally:
                self.env._end_operation(token)
            self._capture_page = pin.page
            data = await self._capture_manager.render(
                pin,
                destination,
                mode=mode,
                allow_incomplete=allow_incomplete,
            )
            return PreviewCapture(
                path=data["path"],
                published_revision=data["published_revision"],
                render_generation=data["render_generation"],
                viewer_id=data["viewer_id"],
                channel_id=data["channel_id"],
                target_id=data["target_id"],
                mode=data["mode"],
                complete=data["complete"],
                diagnostics=_freeze_capture(data["diagnostics"]),
                modal_id=data["modal_id"],
                profile=_freeze_capture(data["profile"]),
                geometry=_freeze_capture(data["geometry"]),
                output_width=data["output_width"],
                output_height=data["output_height"],
                ready=data["ready"],
                calibrated=data["calibrated"],
                calibration=_freeze_capture(data["calibration"]),
                action=_freeze_capture(data["action"]) if data["action"] is not None else None,
            )
        finally:
            if pin is not None:
                self._pages.pop(pin.page.id, None)
            self._capture_page = None
            self._capture_task = None

    async def wait_closed(self) -> None:
        await self._closed_event.wait()

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._active = False
            if self._unregister_dispatch is not None:
                self._unregister_dispatch()
                self._unregister_dispatch = None
            current = asyncio.current_task()
            capture_task = self._capture_task
            if capture_task is not None and capture_task is not current and not capture_task.done():
                capture_task.cancel()
                await asyncio.gather(capture_task, return_exceptions=True)
            await self._capture_manager.close()
            await self._server.close()
            if self._media_worker is not None:
                await self._media_worker.close()
                self._media_worker = None
            if self._unregister_shutdown is not None:
                self._unregister_shutdown()
                self._unregister_shutdown = None
            self._pages.clear()
            self._capture_page = None
            if self.env._preview is self:
                self.env._preview = None
            self._closed_event.set()

    def _on_dispatch(self, interaction: Interaction) -> None:
        task = asyncio.current_task()
        if self._active_action is not None and task is self._active_task:
            self._active_action.interaction = interaction

    async def action(self, context_id: str | None, body: Mapping[str, Any]) -> dict[str, Any]:
        page = self.get_page(context_id)
        if not isinstance(body, Mapping):
            raise SetupError("action must be an object")
        sequence = body.get("sequence")
        request_id = body.get("request_id")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise SetupError("sequence must be a positive integer")
        if not isinstance(request_id, str) or not request_id:
            raise SetupError("request_id must be a non-empty string")
        fingerprint = hashlib.sha256(
            json.dumps(dict(body), sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        latest = page.latest_action
        if sequence <= page.last_sequence:
            if sequence == page.last_sequence and latest is not None and latest.request_id == request_id:
                if latest.fingerprint != fingerprint:
                    raise SetupError("request conflicts with the admitted sequence")
                if latest.response is None:
                    return {
                        "requestId": latest.request_id,
                        "sequence": latest.sequence,
                        "dispatched": False,
                        "dispatch": "pending",
                        "acknowledgement": "pending",
                        "settlement": "pending",
                        "presentation": page.status,
                        "revision": page.revision,
                        "diagnostics": [],
                    }
                return dict(latest.response)
            raise SetupError("action sequence is stale")
        if sequence != page.last_sequence + 1:
            raise SetupError("action sequence has a gap")
        if self._active_action is not None:
            raise SetupError("Preview is busy with another action")
        if body.get("generation") != page.generation:
            raise SetupError("preview context generation is stale")
        if body.get("bot_generation", self.env._generation) != self.env._generation:
            raise SetupError("bot generation is stale")
        kind = body.get("kind")
        if kind not in {"click", "select", "modal_submit", "viewer", "focus", "refresh", "close"}:
            raise SetupError("unknown preview action")
        token = self.env._begin_operation("preview.action")
        action = _Action(sequence, request_id, fingerprint)
        page.last_sequence = sequence  # consumed before callback dispatch
        page.latest_action = action
        self._active_action = action
        self._active_task = asyncio.current_task()
        cursor = self.env.error_cursor
        try:
            try:
                result = await self._dispatch_action(page, kind, body, action, cursor)
            except asyncio.CancelledError:
                page.status = "stale"
                result = self._finish_action(page, action, "cancelled", cursor)
                raise
            except TimeoutError:
                page.status = "stale"
                result = self._finish_action(page, action, "timeout", cursor)
            except BaseException as exc:
                result = self._finish_action(page, action, "settled", cursor, exc)
        finally:
            self.env._end_operation(token)
            self._active_action = None
            self._active_task = None
        action.response = result
        return dict(result)

    async def _dispatch_action(
        self, page: _Page, kind: str, body: Mapping[str, Any], action: _Action, cursor: int
    ) -> dict[str, Any]:
        if kind == "close":
            result = self._finish_action(page, action, "settled", cursor)
            self._close_task = asyncio.create_task(self.close())
            return result
        if kind == "viewer":
            viewer = self._viewer(body.get("viewer_id"))
            self._clear_page_assets(page)
            page.viewer = viewer
            page.generation += 1
            page.target_id = self._initial_target(page)
            page.modal = None
            page.modal_handle = None
            page.status = "current"
            self._publish(page)
            return self._finish_action(page, action, "settled", cursor)
        if kind == "focus":
            target = self._target_id(body.get("target_id"), page.viewer)
            if target is None:
                raise SetupError("target message is unavailable")
            self._clear_page_assets(page)
            page.target_id = target
            page.generation += 1
            page.modal = None
            page.modal_handle = None
            self._publish(page)
            return self._finish_action(page, action, "settled", cursor)
        if kind == "refresh":
            await self.env._settle_internal()
            self._publish(page)
            return self._finish_action(page, action, "settled", cursor)
        actor = page.viewer
        if not can_access_channel(self.env, page.channel_id, actor, history=True):
            raise SetupError("viewer cannot access this channel")
        if kind == "modal_submit":
            if page.modal is None or body.get("modal_handle") != page.modal_handle:
                raise SetupError("modal is stale or unavailable")
            values = self._modal_values(page, body.get("values"))
            result = await actor.submit_modal(page.modal, values)
            page.modal = None
            page.modal_handle = None
        else:
            message = self._target_message(page)
            if kind == "click":
                custom_id = body.get("custom_id")
                if not isinstance(custom_id, str):
                    raise SetupError("click requires custom_id")
                result = await actor.click(ResponseMessage(self.env, message), custom_id=custom_id)
            else:
                values = self._select_values(page, body.get("values"), body.get("custom_id"))
                result = await actor.select(
                    ResponseMessage(self.env, message), values, custom_id=body.get("custom_id")
                )
            if result.modal is not None:
                if result._interaction.user_id != actor.id:  # pragma: no cover - actor dispatch invariant
                    raise SetupError("modal opener mismatch")
                page.modal = result
                page.modal_handle = "m_" + secrets.token_urlsafe(12)
        self._publish(page)
        return self._finish_action(page, action, "settled", cursor, interaction=result._interaction)

    def _target_message(self, page: _Page) -> Message:
        if page.target_id is None:
            raise SetupError("no focused message")
        try:
            message = self.env.backend.get_message(page.channel_id, page.target_id)
        except BackendError as exc:
            raise SetupError("focused message is unavailable") from exc
        if not can_access_message(self.env, page.channel_id, message, page.viewer, history=True):
            raise SetupError("focused message is inaccessible")
        return message

    def _select_values(self, page: _Page, values: Any, custom_id: Any) -> list[Any]:
        if not isinstance(values, list):
            raise SetupError("select values must be a list")
        try:
            if len(values) != len(set(values)):
                raise SetupError("select values must be unique")
        except TypeError as exc:
            raise SetupError("select values must be scalar") from exc
        message = self._target_message(page)
        component = next(
            (item for item in walk_components(message.components) if item.get("custom_id") == custom_id), None
        )
        if component is None:
            raise SetupError("select is unavailable")
        kind = ComponentType(component["type"])
        minimum = component.get("min_values", 1)
        maximum = component.get("max_values", 1)
        if not minimum <= len(values) <= maximum:
            raise SetupError(f"Select expects between {minimum} and {maximum} value(s), got {len(values)}")
        if any(not isinstance(value, str) for value in values):
            raise SetupError("select values must be strings")
        if kind == ComponentType.STRING_SELECT:
            valid = {str(item.get("value")) for item in component.get("options") or []}
            if any(value not in valid for value in values):
                raise SetupError("select option is unavailable")
            return list(values)
        if kind == ComponentType.CHANNEL_SELECT and isinstance(component.get("channel_types"), list):
            allowed = set(component["channel_types"])
            for value in values:  # pragma: no branch - empty selections are validated above
                try:
                    candidate = self.env.backend.channels.get(int(value))
                except (TypeError, ValueError):
                    candidate = None
                if candidate is None or (allowed and candidate.type not in allowed):
                    raise SetupError("select channel is unavailable")
        handles = [self._resolve_entity(page, value, kind) for value in values]
        if any(handle is None for handle in handles):
            raise SetupError("select entity is unavailable")
        return handles  # type: ignore[return-value]

    def _resolve_entity(self, page: _Page, value: str, kind: ComponentType | None = None) -> Any | None:
        try:
            entity_id = int(value)
        except (TypeError, ValueError):
            return None
        channel = self.env.backend.get_channel(page.channel_id)
        if channel.guild_id is None:
            if kind in {ComponentType.USER_SELECT, ComponentType.MENTIONABLE_SELECT}:
                if entity_id in channel.recipient_ids and can_access_channel(
                    self.env, channel.id, page.viewer
                ):
                    return UserHandle(self.env, self.env.backend.get_user(entity_id))
            return None
        guild = self.env.backend.guilds[channel.guild_id]
        if (
            kind in {ComponentType.USER_SELECT, ComponentType.MENTIONABLE_SELECT}
            and entity_id in guild.members
        ):
            return self._member_handle(page, entity_id)
        if (
            kind in {ComponentType.ROLE_SELECT, ComponentType.MENTIONABLE_SELECT}
            and entity_id in guild.roles
            and entity_id != guild.id
        ):
            from ..builders import GuildHandle, RoleHandle

            return RoleHandle(self.env, GuildHandle(self.env, guild), guild.roles[entity_id])
        if kind == ComponentType.CHANNEL_SELECT:
            candidate = self.env.backend.channels.get(entity_id)
            if (
                candidate is not None
                and candidate.guild_id == channel.guild_id
                and can_access_channel(self.env, candidate.id, page.viewer)
            ):
                from ..builders import GuildHandle

                return ChannelHandle(self.env, GuildHandle(self.env, guild), candidate)
        return None

    def _member_handle(self, page: _Page, user_id: int) -> MemberActor:
        from ..builders import GuildHandle

        channel = self.env.backend.get_channel(page.channel_id)
        guild = self.env.backend.get_guild(channel.guild_id)
        return MemberActor(
            self.env, GuildHandle(self.env, guild), UserHandle(self.env, self.env.backend.get_user(user_id))
        )

    def _modal_values(self, page: _Page, values: Any) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise SetupError("modal values must be an object")
        kinds: dict[str, ComponentType] = {}
        stack: list[Any] = [page.modal.modal if page.modal is not None else {}]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                custom_id = node.get("custom_id")
                try:
                    if isinstance(custom_id, str):
                        kinds[custom_id] = ComponentType(node["type"])
                except (KeyError, TypeError, ValueError):
                    pass
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        converted: dict[str, Any] = {}
        entity_types = {
            ComponentType.USER_SELECT,
            ComponentType.ROLE_SELECT,
            ComponentType.CHANNEL_SELECT,
            ComponentType.MENTIONABLE_SELECT,
        }
        for key, value in values.items():
            kind = kinds.get(key)
            if kind is None:
                raise SetupError(f"unknown modal control {key!r}")
            if kind in entity_types:
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise SetupError(f"modal control {key!r} expects entity IDs")
                resolved = [self._resolve_entity(page, item, kind) for item in value]
                if any(item is None for item in resolved):
                    raise SetupError(f"modal entity selection {key!r} is unavailable")
                converted[key] = resolved
            elif kind == ComponentType.FILE_UPLOAD:
                if not isinstance(value, list) or any(
                    not isinstance(item, (list, tuple))
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or not isinstance(item[1], bytes)
                    for item in value
                ):
                    raise SetupError(f"modal file upload {key!r} is invalid")
                converted[key] = [(item[0], item[1]) for item in value]
            else:
                converted[key] = value
        return converted

    def _finish_action(
        self,
        page: _Page,
        action: _Action,
        settlement: str,
        cursor: int,
        error: BaseException | None = None,
        *,
        interaction: Interaction | None = None,
    ) -> dict[str, Any]:
        interaction = interaction or action.interaction
        diagnostics = [
            {"type": type(item).__name__, "message": str(item)} for item in self.env.errors_since(cursor)
        ]
        if error is not None and not isinstance(error, SetupError):
            diagnostics.append({"type": type(error).__name__, "message": str(error)})
        dispatch = "dispatched" if interaction is not None else "not_dispatched"
        ack = "pending"
        if interaction is not None:
            ack = "acknowledged" if interaction.responded else "unacknowledged"
            if interaction.deferred:
                ack = "deferred"
        page.last_action = {
            "requestId": action.request_id,
            "sequence": action.sequence,
            "dispatch": dispatch,
            "acknowledgement": ack,
            "settlement": settlement,
        }
        if error is not None and not isinstance(error, SetupError):
            page.status = "stale"
        return {
            "requestId": action.request_id,
            "sequence": action.sequence,
            "dispatched": interaction is not None,
            "dispatch": dispatch,
            "acknowledgement": ack,
            "settlement": settlement,
            "presentation": page.status,
            "revision": page.revision,
            "diagnostics": diagnostics,
        }

    def asset(self, context_id: str | None, asset_id: str) -> tuple[str, bytes, str]:
        page = self.get_page(context_id)
        self._assert_capture_live(page)
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            raise SetupError("asset access denied")
        try:
            filename, body, content_type = page.asset_blobs[asset_id]
        except KeyError as exc:
            raise SetupError("asset is unavailable") from exc
        return content_type, body, filename

    async def prepare_asset(self, context_id: str | None, asset_id: str) -> tuple[str, bytes, str]:
        content_type, body, filename = self.asset(context_id, asset_id)
        page = self.get_page(context_id)
        if not content_type.startswith("image/"):
            return content_type, body, filename
        if self._media_worker is None:
            self._media_worker = MediaWorker()
        metadata = page.assets.get(asset_id, {})
        try:
            info = await self._media_worker.validate(metadata.get("key", asset_id), body)
        except MediaError as exc:
            metadata["available"] = False
            metadata["diagnostic"] = str(exc)
            raise SetupError(str(exc)) from exc
        if "normalizedBytes" not in metadata:
            if self._retained_media_bytes + len(info.normalized) > self._MAX_MEDIA_BYTES:
                metadata["available"] = False
                metadata["diagnostic"] = "session media budget exceeded after normalization"
                raise SetupError(metadata["diagnostic"])
            self._retained_media_bytes += len(info.normalized)
            metadata["normalizedBytes"] = len(info.normalized)
        metadata.update(
            {"width": info.width, "height": info.height, "frames": info.frames, "validated": True}
        )
        self._assert_capture_live(page)
        return info.content_type, info.normalized, filename


def _validate_preview(
    env: Any,
    channel: Any,
    viewers: Any,
    theme: str,
    width: int,
    height: int,
    locale: str,
    timezone: str,
    assets: Any,
) -> tuple[ChannelHandle, tuple[Any, ...], Mapping[str, tuple[str, bytes]]]:
    if not isinstance(channel, ChannelHandle) or channel._env is not env:
        raise SetupError("preview channel must belong to this Env")
    try:
        selected = tuple(viewers)
    except TypeError as exc:
        raise SetupError("viewers must be a non-empty sequence") from exc
    if not selected:
        raise SetupError("viewers must be a non-empty sequence")
    if len({getattr(item, "id", None) for item in selected}) != len(selected):
        raise SetupError("viewers must be unique")
    for viewer in selected:
        if getattr(viewer, "_env", None) is not env or not isinstance(viewer, (MemberActor, UserHandle)):
            raise SetupError("viewers must be same-Env UserHandle or MemberActor handles")
        if channel.guild is None:
            if not isinstance(viewer, UserHandle) or env.backend.dm_channels.get(viewer.id) != channel.id:
                raise SetupError("a DM preview requires its owning UserHandle")
        elif not isinstance(viewer, MemberActor) or viewer.guild.id != channel.guild.id:
            raise SetupError("guild previews require members of the selected guild")
        if not can_access_channel(env, channel.id, viewer, history=True):
            raise SetupError("viewer lacks channel and history access")
    if theme not in {"dark", "light"}:
        raise SetupError("theme must be 'dark' or 'light'")
    for value, name in ((width, "width"), (height, "height")):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SetupError(f"{name} must be a positive integer")
    if locale not in Preview._LOCALES:
        raise SetupError(f"unsupported locale {locale!r}")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SetupError(f"unsupported timezone {timezone!r}") from exc
    if assets is None:
        assets = {}
    if not isinstance(assets, Mapping):
        raise SetupError("assets must map URLs to (filename, bytes) tuples")
    normalized: dict[str, tuple[str, bytes]] = {}
    for url, value in assets.items():
        if not isinstance(url, str) or not isinstance(value, tuple) or len(value) != 2:
            raise SetupError("assets must map URLs to (filename, bytes) tuples")
        filename, blob = value
        if not isinstance(filename, str) or not isinstance(blob, bytes):
            raise SetupError("assets must map URLs to (filename, bytes) tuples")
        normalized[url] = (filename, blob)
    return channel, selected, MappingProxyType(normalized)


def make_preview(env: Any, channel: Any, **kwargs: Any) -> Preview:
    channel, viewers, assets = _validate_preview(env, channel, **kwargs)
    return Preview(
        env,
        channel,
        viewers,
        assets=assets,
        **{key: kwargs[key] for key in ("theme", "width", "height", "locale", "timezone")},
    )


__all__ = ("Preview", "PreviewCapture", "make_preview")
