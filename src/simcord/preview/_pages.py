"""Preview page registry: inactivity leases, per-viewer state, target resolution."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

import discord

from ..backend.access import can_access_channel, can_access_message
from ..backend.cdn import CDN_BASE
from ..backend.errors import BackendError, SetupError
from ..results import InteractionResult, ResponseMessage
from ._assets import _Asset, _content_type
from ._diagnostics import make_diagnostic

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
    window_end_id: int | None = None
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
    layout: str = "message"
    display: str = "responsive"
    width: int = 960
    height: int = 720
    host_width: int = 960
    host_height: int = 720
    navigation_query: str = ""
    navigation_cursor: str | None = None
    candidate_queries: dict[str, dict[str, Any]] = field(default_factory=dict)
    cursor_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    published_at: str | None = None
    publication_reason: str = "initial"
    activity: list[dict[str, Any]] = field(default_factory=list)
    pending_receipt_revision: bool = False
    activity_actions: list[_Action | None] = field(default_factory=list)
    # Resolved at call time: env patches time.monotonic while running, and this
    # module may be imported under an earlier (now dead) env's patch.
    last_activity: float = field(default_factory=lambda: time.monotonic())

    def asset_id(
        self, key: str, metadata: Mapping[str, Any], *, source: tuple[Any, ...] | None = None
    ) -> str:
        existing = next((asset for asset, item in self.assets.items() if item.key == key), None)
        if existing is not None:
            old = self.assets[existing]
            filename = str(metadata.get("filename", old.filename))
            content_type = str(metadata.get("content_type") or "application/octet-stream")
            if content_type == "application/octet-stream":
                content_type = _content_type(
                    filename if filename != "asset" else str(metadata.get("url", ""))
                )
            replacement = _Asset(
                id=existing,
                filename=filename,
                contentType=content_type,
                key=key,
                source=source,
            )
            self._resolve_blob(replacement, metadata, source)
            old_released = False
            if replacement.diagnostic == "session media budget exceeded" and old.digest is not None:
                self.preview._release_asset_record(old)
                old_released = True
                replacement = _Asset(
                    id=existing,
                    filename=filename,
                    contentType=content_type,
                    key=key,
                    source=source,
                )
                self._resolve_blob(replacement, metadata, source)
            if (
                old.digest,
                old.filename,
                old.contentType,
                old.source,
            ) == (
                replacement.digest,
                replacement.filename,
                replacement.contentType,
                replacement.source,
            ):
                if not old_released:
                    self.preview._release_asset_record(replacement)
                    self.assets[existing] = old
                else:
                    self.assets[existing] = replacement
                self.referenced_assets.add(existing)
                return existing
            # A handle binds source bytes/metadata, including unavailable transitions.
            asset = "a_" + secrets.token_urlsafe(12)
            if not old_released:
                self.preview._release_asset_record(old)
            replacement.id = asset
            self.assets.pop(existing)
            self.assets[asset] = replacement
            self.referenced_assets.add(asset)
            return asset
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
            owner = source[0] if source else None
            if owner in {
                "attachment",
                "sticker",
                "emoji",
                "user_avatar",
                "member_avatar",
                "default_avatar",
                "application_avatar",
            }:
                blob = self.preview.env.backend.cdn.get(url)
            if (
                blob is None
                and (supplied := self.preview._explicit_assets.get(url)) is not None
                and not (owner == "message" and url.startswith(f"{CDN_BASE}/"))
            ):
                filename, blob = supplied
                record.filename = filename
                if source is not None:
                    record.source = source
        if blob is None:
            if record.digest is not None:
                self.preview._release_blob(record.digest, normalized=record.normalizedRetained)
                record.digest = None
                record.normalizedRetained = False
            record.available = False
            record.bytes = None
            record.validated = False
            return
        digest = hashlib.sha256(blob).hexdigest()
        if digest == record.digest:
            record.available = True
            record.bytes = len(blob)
            record.diagnostic = None
            return
        if record.digest is not None:
            self.preview._release_blob(record.digest, normalized=record.normalizedRetained)
            record.normalizedRetained = False
        digest = self.preview._retain_blob(blob)
        if digest is None:
            record.diagnostic = "session media budget exceeded"
            record.available = False
            record.bytes = None
            record.digest = None
            return
        if record.contentType == "application/octet-stream":
            record.contentType = _content_type(record.filename)
        record.diagnostic = None
        record.digest = digest
        record.available = True
        record.bytes = len(blob)
        record.validated = False


class _PageOps:
    """Page lifecycle: registration, expiry, and viewer/target resolution."""

    env: Env
    channel: ChannelHandle
    layout: str
    display: str
    width: int
    height: int
    viewers: tuple[Any, ...]
    _pages: dict[str, _Page]
    _python: _Page | None
    _action_page: _Page | None
    _pending_page_closes: set[str]
    _active: bool
    _closed: bool
    _MAX_PAGES: ClassVar[int]
    _PAGE_LEASE_SECONDS: ClassVar[float]
    _publish: Callable[..., None]
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

    def _viewer(self, viewer_id: Any) -> Any:
        """Resolve a requested viewer from the preview's explicit allowlist."""
        if isinstance(viewer_id, bool) or not isinstance(viewer_id, (str, int)):
            raise SetupError("unknown preview viewer")
        try:
            selected_id = int(viewer_id)
        except ValueError as exc:
            raise SetupError("unknown preview viewer") from exc
        for viewer in self.viewers:
            if viewer.id == selected_id:
                return viewer
        raise SetupError("viewer is not authorized for this preview")

    def _prune_expired(self, *, keep: _Page | None = None) -> None:
        """Enforce the inactivity lease: expired browser pages are released."""
        now = time.monotonic()
        for page in tuple(self._pages.values()):
            if page is keep or page is self._action_page or page is self._python:
                continue
            if now - page.last_activity > self._PAGE_LEASE_SECONDS:
                self._pages.pop(page.id, None)
                self._redact_page_receipts(page)
                self._clear_page_assets(page)

    @staticmethod
    def _redact_receipt(receipt: dict[str, Any] | None) -> None:
        if not isinstance(receipt, dict):
            return
        receipt["target"] = None
        receipt["outcomes"] = []
        receipt["presentation"] = "access_denied"
        receipt.pop("result", None)
        for diagnostic in receipt.get("diagnostics", []):
            diagnostic.pop("subject", None)

    def _redact_page_receipts(self, page: _Page) -> None:
        self._redact_receipt(page.last_action)
        if page.latest_action is not None:
            page.latest_action.interaction = None
            page.latest_action.target = None
            page.latest_action.outcomes.clear()
            self._redact_receipt(page.latest_action.response)
        page.activity.clear()
        page.activity_actions.clear()

    def _receipt_payload(self, page: _Page, receipt: Any, *, allowed: bool) -> Any:
        if not isinstance(receipt, Mapping):
            return None
        safe = json.loads(json.dumps(receipt))
        safe.pop("result", None)
        if not allowed:
            self._redact_receipt(safe)
            return safe

        def message_is_visible(value: Any) -> bool:
            try:
                message = self.env.backend.get_message(page.channel_id, int(value))
            except (BackendError, TypeError, ValueError):
                return False
            return can_access_message(self.env, page.channel_id, message, page.viewer, history=True)

        target = safe.get("target")
        if isinstance(target, Mapping) and not message_is_visible(target.get("messageId")):
            safe["target"] = None
        outcomes = []
        for item in safe.get("outcomes", []):
            if not isinstance(item, Mapping):
                continue
            outcome = dict(item)
            if outcome.get("messageId") is not None and not message_is_visible(outcome["messageId"]):
                outcomes.append({"kind": "unavailable"})
            else:
                outcomes.append(outcome)
        safe["outcomes"] = outcomes
        return safe

    @staticmethod
    def _denied_payload(payload: dict[str, Any]) -> dict[str, Any]:
        payload.update(
            messageIndex=[],
            navigation={
                "query": "",
                "filter": "all",
                "hasPrevious": False,
                "hasNext": False,
                "previousCursor": None,
                "nextCursor": None,
            },
            messages={},
            timeline=[],
            entities={},
            modal=None,
            candidates={},
            assets={},
            targetId=None,
            history={
                "hasBefore": False,
                "hasAfter": False,
                "windowStartId": None,
                "windowEndId": None,
            },
            diagnostics=[make_diagnostic("access-denied")],
        )
        if isinstance(payload.get("channel"), dict):
            payload["channel"].update(name=None, guildId=None, type=None, topic=None, canSendMessages=False)
        payload["status"] = "access_denied"
        payload["lastAction"] = None
        payload["activity"] = []
        return payload

    def _page_payload(self, page: _Page) -> dict[str, Any]:

        if page.pinned_snapshot is not None:
            self._assert_capture_live(page)
            payload = json.loads(json.dumps(page.pinned_snapshot))
            allowed = can_access_channel(self.env, page.channel_id, page.viewer, history=True)
            if allowed:
                payload["lastAction"] = self._receipt_payload(page, page.last_action, allowed=True)
                payload["activity"] = [
                    self._receipt_payload(page, item, allowed=True) for item in page.activity
                ]
            else:
                self._denied_payload(payload)
            return payload
        allowed = can_access_channel(self.env, page.channel_id, page.viewer, history=True)
        if not allowed:
            if page.status != "access_denied":
                self._clear_page_assets(page)
                page.modal = None
                page.modal_handle = None
                self._redact_page_receipts(page)
            page.status = "access_denied"
        payload = json.loads(json.dumps(page.snapshot))
        if allowed:
            payload["assets"] = {}
            for asset_id, record in page.assets.items():
                item = record.to_wire()
                item.pop("diagnostic", None)
                payload["assets"][asset_id] = item
        else:
            self._denied_payload(payload)
        if allowed:
            payload["lastAction"] = self._receipt_payload(page, page.last_action, allowed=True)
            payload["activity"] = [self._receipt_payload(page, item, allowed=True) for item in page.activity]
        payload["status"] = page.status
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
        page.layout = self.layout
        page.display = self.display
        page.width = self.width
        page.height = self.height
        page.host_width = self.width
        page.host_height = self.height
        if page.layout == "channel":
            page.window_end_id = target if target_id is not None else source.window_end_id
        if source.modal is not None and source.modal._interaction.user_id == viewer.id:
            page.modal = source.modal
            page.modal_handle = "m_" + secrets.token_urlsafe(12)
        try:
            self._publish(page, reason="initial")
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
        page = self._pages.get(context_id)
        if page is self._action_page:
            self._pending_page_closes.add(context_id)
            return
        page = self._pages.pop(context_id, None)
        if page is not None:
            self._redact_page_receipts(page)
            self._clear_page_assets(page)

    def _target_id(self, target_id: Any, viewer: Any = None, *, denied_fallback: bool = False) -> int | None:
        selected_viewer = cast(_Page, self._python).viewer if viewer is None else viewer
        if target_id is None:
            return self._initial_target(selected_viewer, self.channel.id)
        try:
            target = int(target_id)
        except (TypeError, ValueError) as exc:
            raise SetupError("authorized target is unavailable") from exc
        try:
            message = self.env.backend.get_message(self.channel.id, target)
        except BackendError as exc:
            raise SetupError("authorized target is unavailable") from exc
        if not can_access_message(self.env, self.channel.id, message, selected_viewer, history=True):
            if denied_fallback:
                return self._initial_target(selected_viewer, self.channel.id)
            raise SetupError("authorized target is unavailable")
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
