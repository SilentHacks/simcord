"""Preview page registry: inactivity leases, per-viewer state, target resolution."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

import discord

from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..results import InteractionResult, ResponseMessage
from ._assets import _Asset, _content_type

if TYPE_CHECKING:
    from ..builders import ChannelHandle
    from ..env import Env
    from . import Preview
    from ._actions import _Action


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
    assets: dict[str, _Asset] = field(default_factory=dict)
    referenced_assets: set[str] = field(default_factory=set)
    snapshot: dict[str, Any] = field(default_factory=dict)
    pinned_snapshot: dict[str, Any] | None = None
    pinned_generation: int | None = None
    pinned_attachment_ids: dict[str, str] = field(default_factory=dict)
    # Resolved at call time: env patches time.monotonic while running, and this
    # module may be imported under an earlier (now dead) env's patch.
    last_activity: float = field(default_factory=lambda: time.monotonic())

    def asset_id(
        self, key: str, metadata: Mapping[str, Any], *, source: tuple[Any, ...] | None = None
    ) -> str:
        existing = next((asset for asset, item in self.assets.items() if item.key == key), None)
        if existing is not None:
            record = self.assets[existing]
            self.referenced_assets.add(existing)
            # A placeholder created by a foreign reference gains real ownership
            # when the attachment that owns the bytes is projected later.
            if record.digest is None and source is not None and source[0] == "attachment":
                self._resolve_blob(record, metadata, source)
            return existing
        asset = "a_" + secrets.token_urlsafe(12)
        filename = str(metadata.get("filename", "asset"))
        content_type = str(metadata.get("content_type") or "application/octet-stream")
        if content_type == "application/octet-stream":
            content_type = _content_type(filename if filename != "asset" else str(metadata.get("url", "")))
        record = _Asset(
            id=asset,
            filename=filename,
            contentType=content_type,
            key=key,
            source=source,
        )
        self.assets[asset] = record
        self.referenced_assets.add(asset)
        self._resolve_blob(record, metadata, source)
        return asset

    def _resolve_blob(
        self, record: _Asset, metadata: Mapping[str, Any], source: tuple[Any, ...] | None
    ) -> None:
        blob: bytes | None = None
        url = metadata.get("url")
        if isinstance(url, str):
            if source is not None and source[0] == "attachment":
                # Only the owning message's own attachments may resolve CDN
                # bytes; foreign attachment URLs never reach this branch.
                blob = self.preview.env.backend.cdn.get(url)
                if blob is not None:
                    record.source = source
            if blob is None and (supplied := self.preview._explicit_assets.get(url)) is not None:
                filename, blob = supplied
                record.filename = filename
                if source is None:
                    record.source = ("explicit", url)
        if blob is None:
            return
        digest = self.preview._retain_blob(blob)
        if digest is None:
            record.diagnostic = "session media budget exceeded"
            return
        if record.contentType == "application/octet-stream":
            record.contentType = _content_type(record.filename)
        record.diagnostic = None
        record.digest = digest
        record.available = True
        record.bytes = len(blob)


class _PageOps:
    """Page lifecycle: registration, expiry, and viewer/target resolution."""

    env: Env
    channel: ChannelHandle
    viewers: tuple[Any, ...]
    _pages: dict[str, _Page]
    _python: _Page | None
    _action_page: _Page | None
    _active: bool
    _closed: bool
    _MAX_PAGES: ClassVar[int]
    _PAGE_LEASE_SECONDS: ClassVar[float]
    _publish: Callable[[_Page], None]
    _clear_page_assets: Callable[[_Page], None]
    _assert_capture_live: Callable[[_Page], None]

    def _initial_target(self, viewer: Any, channel_id: int) -> int | None:
        if not can_access_channel(self.env, channel_id, viewer, history=True):
            return None
        messages = self.env.backend.messages.get(channel_id, {})
        visible = [
            item
            for item in messages.values()
            if can_access_message(self.env, channel_id, item, viewer, history=True)
        ]
        bot_id = self.env.backend.bot_user.id
        bot_messages = [item for item in visible if item.author_id == bot_id]
        return (
            max(bot_messages or visible, key=lambda item: item.id).id if (bot_messages or visible) else None
        )

    def _prune_expired(self, *, keep: _Page | None = None) -> None:
        """Enforce the inactivity lease: expired browser pages are released."""
        now = time.monotonic()
        for page in tuple(self._pages.values()):
            if page is keep or page is self._action_page or page is self._python:
                continue
            if now - page.last_activity > self._PAGE_LEASE_SECONDS:
                self._pages.pop(page.id, None)
                self._clear_page_assets(page)

    def _page_payload(self, page: _Page) -> dict[str, Any]:
        if page.pinned_snapshot is not None:
            self._assert_capture_live(page)
            return json.loads(json.dumps(page.pinned_snapshot))
        allowed = can_access_channel(self.env, page.channel_id, page.viewer, history=True)
        if not allowed:
            if page.status != "access_denied":
                self._clear_page_assets(page)
                page.modal = None
                page.modal_handle = None
                page.snapshot.update(
                    {"messages": [], "selected": None, "modal": None, "candidates": {}, "assets": {}}
                )
            page.status = "access_denied"
        # Reads never republish and never clear "stale": they serve the last
        # published projection with the live status overlaid, redacted on denial.
        payload = json.loads(json.dumps(page.snapshot))
        payload["status"] = page.status
        if not allowed:
            payload.update({"messages": [], "selected": None, "modal": None, "candidates": {}, "assets": {}})
        return payload

    def _get_page(self, context_id: str | None) -> _Page:
        self._prune_expired()
        if not isinstance(context_id, str) or context_id not in self._pages:
            raise SetupError("preview context is expired or unknown")
        page = self._pages[context_id]
        if self._closed:  # pragma: no cover - close clears the page registry
            raise SetupError("Preview is closed")
        page.last_activity = time.monotonic()
        return page

    def _open_page(self, viewer_id: Any = None, target_id: Any = None) -> _Page:
        if not self._active or self._closed:
            raise SetupError("Preview is not active")
        self._prune_expired()
        if len(self._pages) >= self._MAX_PAGES + 1:  # Python presentation is not interactive.
            raise SetupError("Preview page limit reached")
        source = cast(_Page, self._python)
        viewer = self.viewers[0] if viewer_id is None else self._viewer(viewer_id)
        if target_id is None:
            target = source.target_id
            if target is not None:
                try:
                    message = self.env.backend.get_message(self.channel.id, target)
                except BackendError:
                    # The inherited focus was deleted: degrade like a denied
                    # target rather than surfacing a raw backend error.
                    message = None
                if message is None or not can_access_message(
                    self.env, self.channel.id, message, viewer, history=True
                ):
                    target = self._initial_target(viewer, self.channel.id)
        else:
            # A denied explicit target degrades like an inaccessible
            # inherited one: open on this viewer's own initial target.
            target = self._target_id(target_id, viewer, denied_fallback=True)
        page = _Page(
            cast("Preview", self),
            "p_" + secrets.token_urlsafe(12),
            viewer,
            self.channel.id,
            target,
        )
        if source.modal is not None and source.modal._interaction.user_id == viewer.id:
            page.modal = source.modal
            page.modal_handle = "m_" + secrets.token_urlsafe(12)
        try:
            self._publish(page)
        except BaseException:
            # The page was never registered: drop any blobs its partial
            # snapshot retained so a failed publish leaves nothing behind.
            self._clear_page_assets(page)
            raise
        self._pages[page.id] = page
        return page

    def _close_page(self, context_id: str) -> None:
        if context_id == "python":
            raise SetupError("The Python presentation cannot be closed as a page")
        self._prune_expired()
        page = self._pages.pop(context_id, None)
        if page is not None:
            self._clear_page_assets(page)

    def _viewer(self, viewer_id: Any) -> Any:
        try:
            value = int(viewer_id)
        except (TypeError, ValueError) as exc:
            raise SetupError("unknown preview viewer") from exc
        for viewer in self.viewers:
            if viewer.id == value:
                return viewer
        raise SetupError("viewer is not authorized for this Preview")

    def _target_id(self, target_id: Any, viewer: Any = None, *, denied_fallback: bool = False) -> int | None:
        selected_viewer = cast(_Page, self._python).viewer if viewer is None else viewer
        if target_id is None:
            return self._initial_target(selected_viewer, self.channel.id)
        try:
            target = int(target_id)
        except (TypeError, ValueError) as exc:
            raise SetupError("target_id must be a snowflake string") from exc
        try:
            message = self.env.backend.get_message(self.channel.id, target)
        except BackendError as exc:
            raise SetupError("target message is unavailable") from exc
        if not can_access_message(self.env, self.channel.id, message, selected_viewer, history=True):
            if denied_fallback:
                return self._initial_target(selected_viewer, self.channel.id)
            raise SetupError("target message is not accessible")
        return target

    def _resolve_target(
        self, viewer: Any, target: Any, *, capture: bool = False
    ) -> tuple[int | None, InteractionResult | None]:
        """Resolve a show/capture target to ``(target_id, modal)`` for a viewer.

        Messages resolve to their id after a live access check; an
        InteractionResult carrying a modal short-circuits to its source
        message and returns the modal for the caller to pin. ``capture``
        selects the "capture target …" wording and enables bare snowflakes.
        """
        label = "capture target" if capture else "target"
        result = target if isinstance(target, InteractionResult) else None
        if result is not None:
            if result._env is not self.env:
                raise SetupError(f"{label} belongs to another Env")
            if result.modal is not None:
                if result._interaction.user_id != viewer.id:
                    if capture:
                        raise SetupError("capture modal opener is not the requested viewer")
                    raise SetupError("a modal can only be shown to its opener")
                if result._interaction.channel_id != self.channel.id:
                    raise SetupError(f"{label} belongs to another channel")
                return result._interaction.source_message_id, result
            target = result.response
            if target is None:
                raise SetupError("interaction has no presentable response")
        if isinstance(target, ResponseMessage):
            if target._env is not self.env or target.channel_id != self.channel.id:
                raise SetupError(f"{label} belongs to another Env or channel")
            target_id = target.id
        elif isinstance(target, discord.Message):
            if target.channel is None or target.channel.id != self.channel.id:
                raise SetupError(f"{label} belongs to another channel")
            target_id = target.id
        elif capture:
            try:
                target_id = int(cast(Any, target))
            except (TypeError, ValueError) as exc:
                raise SetupError("capture target must be a message, result, or snowflake") from exc
        else:
            raise SetupError("show expects a Message, ResponseMessage, or InteractionResult")
        try:
            message = self.env.backend.get_message(self.channel.id, target_id)
        except BackendError as exc:
            raise SetupError(f"{label} is unavailable") from exc
        if not can_access_message(self.env, self.channel.id, message, viewer, history=True):
            raise SetupError(f"{label} is not accessible")
        return target_id, None


__all__ = ["_Page", "_PageOps"]
