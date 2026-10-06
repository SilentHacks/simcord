"""Authorized local presentation of a real SimCord world."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit
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

_PREVIEW_FONT_MANIFEST = Path(__file__).with_name("static") / "fonts" / "manifest.json"
_PREVIEW_RUNTIME_MODULES = ("aiohttp", "markdown_it", "linkify_it", "regex", "PIL")


@cache
def _require_preview_runtime() -> tuple[Mapping[str, Any], ...]:
    missing = [module for module in _PREVIEW_RUNTIME_MODULES if importlib.util.find_spec(module) is None]
    if missing:
        names = ", ".join(missing)
        raise SetupError(
            f"Preview requires {names}; install the optional runtime with `pip install simcord[preview]`."
        )
    try:
        manifest = json.loads(_PREVIEW_FONT_MANIFEST.read_text(encoding="utf-8"))
        fonts = manifest["fonts"]
        for item in fonts:
            filename = item["filename"]
            path = _PREVIEW_FONT_MANIFEST.parent / filename
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError(filename)
        return tuple(
            MappingProxyType(
                {
                    "family": item["family"],
                    "style": item["style"],
                    "weight": item["weights"],
                    "filename": item["filename"],
                    "sha256": item["sha256"],
                    "scripts": tuple(item["scripts"]),
                }
            )
            for item in fonts
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SetupError(
            "Preview typography assets are missing or corrupt; reinstall with `pip install simcord[preview]`."
        ) from exc


class Preview(_PageOps, _AssetOps, _ActionOps, _CaptureOps):
    """An async context manager owning one bounded, local preview session for an Env.

    Entered via ``env.preview(...)``; while active it serves viewer-authorized
    pages and real component callbacks on loopback. ``url`` is a credential —
    anyone holding it can drive the session.
    """

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
        layout: Literal["message", "channel"],
        display: Literal["responsive", "fixed"],
        width: int,
        height: int,
        locale: str,
        timezone: str,
        presentation_time: datetime | None,
        assets: Mapping[str, tuple[str, bytes]] | None,
        sku_presentations: Mapping[str, Mapping[str, str]] | None = None,
        port: int,
    ) -> None:
        self.display = display
        self.layout = layout
        self.env = env
        self.channel = channel
        self.viewers = viewers
        self.width = width
        self.height = height
        self.locale = locale
        self.timezone = timezone
        self._explicit_assets = dict(assets or {})
        self._sku_presentations = {sku: dict(value) for sku, value in (sku_presentations or {}).items()}
        self._presentation_time_explicit = presentation_time is not None
        self.capture_time = presentation_time or datetime.fromisoformat(self.env.backend.now_iso())
        self.capability = secrets.token_urlsafe(32)
        self._server = PreviewServer(self, port)
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
        self._pending_page_closes: set[str] = set()
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
        """The capability-bearing loopback address for this session — treat it as a secret."""
        if self._server.port is None:
            raise SetupError("Preview is not entered")
        return f"http://127.0.0.1:{self._server.port}/#{self.capability}"

    @property
    def _origin(self) -> str:
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
            _require_preview_runtime()
            await self._server.start()
            if self._closed or self._closed_event.is_set():
                # A close() racing the bind already ran (or is running)
                # _cleanup; fail entry cleanly rather than surfacing a stray
                # RuntimeError from the half-bound server.
                raise SetupError("Preview was closed while starting")
            self._python = _Page(self, "python", self.viewers[0], self.channel.id)
            self._python.target_id = self._initial_target(self._python.viewer, self.channel.id)
            self._python.layout = self.layout
            self._python.display = "fixed"
            self._python.width = self.width
            self._python.height = self.height
            self._python.host_width = self.width
            self._python.host_height = self.height
            if self._python.layout == "channel":
                self._python.window_end_id = self._python.target_id
            self._pages[self._python.id] = self._python
            self._publish(self._python, reason="initial")
            self._unregister_shutdown = self.env._register_pre_shutdown(self.close)
            self._unregister_dispatch = self.env._register_dispatch_observer(self._on_dispatch)
            self._active = True
            return self
        except BaseException:
            if self.env._preview is self:
                self.env._preview = None
            self._closed = True
            if self._cleanup_task is None:
                # Entry failed before close() could start: settle
                # wait_closed() callers rather than leaving them on an
                # unset event. When cleanup is already running it sets the
                # event itself once it finishes.
                self._closed_event.set()
            await self._server.close()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _publish(
        self,
        page: _Page,
        *,
        reason: Literal[
            "initial", "refresh", "snapshot", "navigation", "query", "presentation", "action", "capture"
        ] = "action",
    ) -> None:
        self._prune_expired(keep=page)
        page.revision += 1
        page.published_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        page.publication_reason = reason
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            page.status = "access_denied"
            page.modal = None
            page.modal_handle = None
            self._redact_page_receipts(page)
            self._clear_page_assets(page)
        elif page.status != "current":
            page.status = "current"
        if page.status != "access_denied":
            self._refresh_action_receipts(page)
        if page.modal is not None and page.modal._interaction.modal_consumed:
            page.modal = None
            page.modal_handle = None
        if page.pending_receipt_revision:
            for receipt in (
                page.last_action,
                page.activity[-1] if page.activity else None,
                page.latest_action.response if page.latest_action is not None else None,
            ):
                if isinstance(receipt, dict):
                    receipt["revision"] = page.revision
                    receipt["presentation"] = page.status
            page.pending_receipt_revision = False
        from ._commands import build_catalog, unsynced_commands

        page.command_catalog = build_catalog(self, page)
        page.diagnostics = [
            item
            for item in page.diagnostics
            if not isinstance(item, Mapping) or item.get("code") != "commands-unsynced"
        ]
        if page.status != "access_denied" and unsynced_commands(self, page):
            page.diagnostics.append({"code": "commands-unsynced"})
        page.snapshot = build_snapshot(self, page)

    def _advance_presentation_time(self) -> None:
        if not self._presentation_time_explicit:
            self.capture_time = datetime.fromisoformat(self.env.backend.now_iso())

    def _assert_capture_live(self, page: _Page) -> None:
        if self._closed:
            raise SetupError("managed capture was closed")
        if page.pinned_snapshot is None:
            return
        if page.pinned_generation != self.env._generation:
            raise SetupError("managed capture was invalidated by bot restart")
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            raise SetupError("managed capture access was revoked")
        message_sources = {
            (page.channel_id, int(identity)) for identity in page.pinned_snapshot["messages"]
        } | page.referenced_messages
        if page.target_id is not None:
            message_sources.add((page.channel_id, page.target_id))
        for channel_id, message_id in message_sources:
            try:
                message = self.env.backend.get_message(channel_id, message_id)
            except BackendError as exc:
                raise SetupError("managed capture projected message is unavailable") from exc
            if not can_access_message(self.env, channel_id, message, page.viewer, history=True):
                raise SetupError("managed capture projected message access was revoked")
        for asset_id, record in page.assets.items():
            try:
                source = record.source
                if isinstance(source, tuple) and source and source[0] == "attachment":
                    self._authorize_attachment_source(page, source, record.digest)
                elif record.available:
                    self._authorize_asset(page, asset_id)
            except SetupError as exc:
                raise SetupError("managed capture source asset is unavailable or access was revoked") from exc

    async def show(self, target: Any) -> None:
        """Focus the Python presentation on a Message, ResponseMessage, or InteractionResult.

        A modal-carrying InteractionResult shows its modal to the opener. In channel layout, focusing
        a target moves the authorized history window to include it.
        """
        if not self._active or self._python is None:
            raise SetupError("Preview is not active")
        token = self.env._begin_operation("preview.show")
        try:
            page = self._python
            target_id, modal = self._resolve_target(page.viewer, target)
            page.target_id = target_id
            if page.layout == "channel":
                page.window_end_id = target_id
            if modal is not None:
                page.modal = modal
                page.modal_handle = "m_" + secrets.token_urlsafe(12)
            else:
                page.modal = None
                page.modal_handle = None
            page.generation += 1
            page.navigation_query = ""
            page.navigation_cursor = None
            page.candidate_queries.clear()
            self._publish(page, reason="navigation")
        finally:
            self.env._end_operation(token)

    async def refresh(self) -> None:
        """Settle pending bot work and republish every open page."""
        if not self._active or self._closed:
            raise SetupError("Preview is not active")
        token = self.env._begin_operation("preview.refresh")
        try:
            await self.env._settle_internal()
            self._advance_presentation_time()
            for page in tuple(self._pages.values()):
                if page.id in self._pages:  # earlier publishes prune expired pages
                    self._publish(page, reason="refresh")
        finally:
            self.env._end_operation(token)

    async def snapshot(self) -> dict[str, Any]:
        """Settle bot work, republish, and return the detached JSON projection.

        This is the structured, agent-facing read surface: the same projection
        the bundled page renders, covering ``messageIndex`` summaries, ``messages`` and ``timeline``
        for the focused target or authorized 50-message channel window, ``history`` boundaries,
        ``targetId``, ``modal``, ``candidates``, ``entities``, ``assets``, ``diagnostics``, and
        ``lastAction``. Fields evolve under ``protocolVersion``.
        """
        if not self._active or self._python is None:
            raise SetupError("Preview is not active")
        token = self.env._begin_operation("preview.snapshot")
        try:
            await self.env._settle_internal()
            self._publish(self._python, reason="snapshot")
            return self._page_payload(self._python)
        finally:
            self.env._end_operation(token)

    async def wait_closed(self) -> None:
        """Return once the session ends via End preview session, ``close()``, or env shutdown."""
        await self._closed_event.wait()

    async def close(self) -> None:
        """Tear the session down; idempotent, and also run on context exit and env shutdown."""
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
                self._redact_page_receipts(page)
                self._clear_page_assets(page)
            self._pages.clear()
            self._pending_page_closes.clear()
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
    width: int,
    height: int,
    locale: str,
    timezone: str,
    presentation_time: Any = None,
    assets: Any = None,
    sku_presentations: Any = None,
    port: Any = None,
    layout: Any = "message",
    display: Any = "responsive",
) -> tuple[
    ChannelHandle,
    tuple[Any, ...],
    Mapping[str, tuple[str, bytes]],
    Mapping[str, Mapping[str, str]],
    int,
]:
    if layout not in ("message", "channel"):
        raise SetupError("layout must be 'message' or 'channel'")
    if display not in ("responsive", "fixed"):
        raise SetupError("display must be 'responsive' or 'fixed'")
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
    ManagedCapture._validate_dimensions(width, height)
    if locale not in Preview._LOCALES:
        raise SetupError(f"unsupported locale {locale!r}")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SetupError(f"unsupported timezone {timezone!r}") from exc
    if presentation_time is not None:
        if (
            not isinstance(presentation_time, datetime)
            or presentation_time.tzinfo is None
            or presentation_time.utcoffset() is None
        ):
            raise SetupError("presentation_time must be timezone-aware")
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
    sku_presentations = _validate_sku_presentations(sku_presentations, normalized)
    if port is None:
        port = 0
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise SetupError("port must be an integer between 0 and 65535")
    return channel, selected, MappingProxyType(normalized), sku_presentations, port


def _validate_sku_presentations(
    value: Any,
    assets: Mapping[str, tuple[str, bytes]],
) -> Mapping[str, Mapping[str, str]]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise SetupError("sku_presentations must map SKU snowflake strings to presentation objects")

    required = {"name", "price_text", "locale"}
    allowed = required | {"icon_url"}
    normalized: dict[str, Mapping[str, str]] = {}
    for sku_id, presentation in value.items():
        if (
            not isinstance(sku_id, str)
            or not sku_id.isascii()
            or not sku_id.isdigit()
            or len(sku_id) > 20
            or int(sku_id) <= 0
        ):
            raise SetupError("sku_presentations keys must be positive SKU snowflake strings")
        if sku_id in normalized:
            raise SetupError(f"duplicate SKU presentation for {sku_id}")
        if not isinstance(presentation, Mapping):
            raise SetupError(f"sku_presentations[{sku_id!r}] must be a presentation object")
        fields = set(presentation)
        if not required <= fields or fields - allowed:
            raise SetupError(
                f"sku_presentations[{sku_id!r}] requires name, price_text, locale and optional icon_url only"
            )
        name = presentation["name"]
        price_text = presentation["price_text"]
        locale = presentation["locale"]
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise SetupError(f"sku_presentations[{sku_id!r}].name must be 1-100 characters")
        if not isinstance(price_text, str) or not price_text.strip() or len(price_text) > 80:
            raise SetupError(f"sku_presentations[{sku_id!r}].price_text must be 1-80 characters")
        if not isinstance(locale, str) or locale not in Preview._LOCALES:
            raise SetupError(f"sku_presentations[{sku_id!r}].locale must be a supported Discord locale")
        item = {"name": name, "price_text": price_text, "locale": locale}
        if "icon_url" in presentation:
            icon_url = presentation["icon_url"]
            if not isinstance(icon_url, str):
                raise SetupError(f"sku_presentations[{sku_id!r}].icon_url must be a safe offline asset URL")
            try:
                parsed = urlsplit(icon_url)
                safe_url = (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname is not None
                    and parsed.username is None
                    and parsed.password is None
                    and not parsed.fragment
                )
            except ValueError:
                safe_url = False
            supplied = assets.get(icon_url)
            if not safe_url or supplied is None:
                raise SetupError(
                    f"sku_presentations[{sku_id!r}].icon_url must be a safe URL supplied in assets"
                )
            if Path(supplied[0]).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                raise SetupError(f"sku_presentations[{sku_id!r}].icon_url must use a supported raster image")
            item["icon_url"] = icon_url
        normalized[sku_id] = MappingProxyType(item)
    return MappingProxyType(normalized)


def make_preview(env: Any, channel: Any, **kwargs: Any) -> Preview:
    channel, viewers, assets, sku_presentations, port = _validate_preview(env, channel, **kwargs)
    return Preview(
        env,
        channel,
        viewers,
        **{
            key: kwargs[key]
            for key in ("layout", "display", "width", "height", "locale", "timezone", "presentation_time")
        },
        assets=assets,
        sku_presentations=sku_presentations,
        port=port,
    )


__all__ = ("Preview", "PreviewCapture", "make_preview")
