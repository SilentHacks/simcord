"""Authorized local presentation of a real SimCord world."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from ..actors import MemberActor
from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..builders import ChannelHandle, UserHandle
from ._actions import _Action, _ActionOps
from ._assets import _AssetOps, _Blob
from ._capture import ManagedCapture, PreviewCapture, _CaptureOps
from ._media import MediaWorker
from ._pages import _Page, _PageOps
from ._server import PreviewServer
from ._snapshot import build_snapshot


class Preview(_PageOps, _AssetOps, _ActionOps, _CaptureOps):
    """An async context manager owning one bounded, local preview session."""

    _MAX_PAGES = 16
    _MAX_MEDIA_BYTES = 128 * 1024 * 1024
    _PAGE_LEASE_SECONDS = 600.0
    _LOCALES: ClassVar[frozenset[str]] = frozenset(loc.value for loc in discord.Locale)

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
        self._cleanup_task: asyncio.Task[None] | None = None
        self._unregister_shutdown: Any = None
        self._unregister_dispatch: Any = None
        self._active_action: _Action | None = None
        self._action_page: _Page | None = None
        self._media_worker: MediaWorker | None = None
        self._blobs: dict[str, _Blob] = {}
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
        if self.env._preview is not None and not self.env._preview._closed:
            raise SetupError("Only one active Preview is allowed per Env")
        self.env._preview = self
        try:
            await self._server.start()
            if self._closed or self._closed_event.is_set():
                # A close() racing the bind already ran (or is running)
                # _cleanup; fail entry cleanly rather than surfacing a stray
                # RuntimeError from the half-bound server.
                raise SetupError("Preview was closed while starting")
            self._python = _Page(self, "python", self.viewers[0], self.channel.id)
            self._python.target_id = self._initial_target(self._python.viewer, self.channel.id)
            self._pages[self._python.id] = self._python
            self._publish(self._python)
            self._unregister_shutdown = self.env._register_pre_shutdown(self.close)
            self._unregister_dispatch = self.env._register_dispatch_observer(self._on_dispatch)
            self._active = True
            return self
        except BaseException:
            if self.env._preview is self:
                self.env._preview = None
            await self._server.close()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _publish(self, page: _Page) -> None:
        self._prune_expired(keep=page)
        page.revision += 1
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            page.status = "access_denied"
        elif page.status != "current":
            # Every publish is a fresh settled projection: it is the only thing
            # that clears "stale" and restores revoked access.
            page.status = "current"
        if page.modal is not None and page.modal._interaction.modal_consumed:
            page.modal = None
            page.modal_handle = None
        page.snapshot = build_snapshot(self, page)

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

    async def show(self, target: Any) -> None:
        if not self._active or self._python is None:
            raise SetupError("Preview is not active")
        token = self.env._begin_operation("preview.show")
        try:
            page = self._python
            target_id, modal = self._resolve_target(page.viewer, target)
            page.target_id = target_id
            if modal is not None:
                page.modal = modal
                page.modal_handle = "m_" + secrets.token_urlsafe(12)
            else:
                page.modal = None
                page.modal_handle = None
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
                if page.id in self._pages:  # earlier publishes prune expired pages
                    self._publish(page)
        finally:
            self.env._end_operation(token)

    async def wait_closed(self) -> None:
        await self._closed_event.wait()

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed_event.is_set():
                return
            if self._cleanup_task is None:
                # "closed" marks the start of teardown; the event marks its end.
                # The shielded task finishes even if this caller is cancelled,
                # so a cancelled close() cannot strand the running server.
                self._closed = True
                self._active = False
                self._cleanup_task = asyncio.ensure_future(self._cleanup())
        try:
            await asyncio.shield(self._cleanup_task)
        except asyncio.CancelledError:
            raise

    async def _cleanup(self) -> None:
        try:
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
            for page in tuple(self._pages.values()):
                self._clear_page_assets(page)
            self._pages.clear()
            self._blobs.clear()
            self._retained_media_bytes = 0
            self._capture_page = None
            if self.env._preview is self:
                self.env._preview = None
        finally:
            self._closed_event.set()


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
