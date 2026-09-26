"""Content-addressed asset retention, authorization, and normalized serving."""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ._media import MediaError, MediaWorker

if TYPE_CHECKING:
    from ..env import Env
    from ._pages import _Page


def _content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


@dataclass(slots=True)
class _Blob:
    """One deduplicated media payload; ``refs`` count pages retaining it."""

    data: bytes
    refs: int = 0
    normalized_refs: int = 0
    normalized_size: int = 0


@dataclass(slots=True)
class _Asset:
    """One page's asset bookkeeping record; ``to_wire`` is the public view."""

    id: str
    filename: str
    contentType: str
    key: str
    source: tuple[Any, ...] | None = None
    digest: str | None = None
    available: bool = False
    bytes: int | None = None
    diagnostic: str | None = None
    normalizedRetained: bool = False
    validated: bool = False
    width: int | None = None
    height: int | None = None
    frames: int | None = None

    def to_wire(self) -> dict[str, Any]:
        """Public asset record — internal bookkeeping keys never reach the wire."""
        out: dict[str, Any] = {
            "id": self.id,
            "filename": self.filename,
            "contentType": self.contentType,
            "available": bool(self.available),
        }
        if self.bytes is not None:
            out["bytes"] = self.bytes
        if self.diagnostic is not None:
            out["diagnostic"] = self.diagnostic
        return out


class _AssetOps:
    """Blob retention budgets plus per-asset authorization and media serving."""

    env: Env
    _blobs: dict[str, _Blob]
    _explicit_assets: dict[str, tuple[str, bytes]]
    _retained_media_bytes: int
    _media_worker: MediaWorker | None
    _MAX_MEDIA_BYTES: ClassVar[int]
    _get_page: Callable[[str | None], _Page]
    _assert_capture_live: Callable[[_Page], None]

    def _retain_blob(self, blob: bytes) -> str | None:
        """Retain one content-addressed blob; identical payloads share one copy."""
        digest = hashlib.sha256(blob).hexdigest()
        entry = self._blobs.get(digest)
        if entry is None:
            if self._retained_media_bytes + len(blob) > self._MAX_MEDIA_BYTES:
                return None
            entry = _Blob(blob)
            self._blobs[digest] = entry
            self._retained_media_bytes += len(blob)
        entry.refs += 1
        return digest

    def _release_blob(self, digest: str, *, normalized: bool = False) -> None:
        entry = self._blobs.get(digest)
        if entry is None:
            return
        if normalized and entry.normalized_refs > 0:
            entry.normalized_refs -= 1
            if entry.normalized_refs == 0 and entry.normalized_size:
                self._retained_media_bytes -= entry.normalized_size
                entry.normalized_size = 0
                if self._media_worker is not None:
                    self._media_worker.release(digest)
        entry.refs -= 1
        if entry.refs <= 0:
            self._retained_media_bytes -= len(entry.data)
            if entry.normalized_size:
                self._retained_media_bytes -= entry.normalized_size
            if self._media_worker is not None:
                self._media_worker.release(digest)
            del self._blobs[digest]
        self._retained_media_bytes = max(0, self._retained_media_bytes)

    def _release_asset_record(self, record: _Asset) -> None:
        if record.digest is not None:
            self._release_blob(record.digest, normalized=record.normalizedRetained)

    def _reconcile_assets(self, page: _Page) -> None:
        """Drop records the latest projection no longer references."""
        for asset_id in [asset for asset in page.assets if asset not in page.referenced_assets]:
            self._release_asset_record(page.assets.pop(asset_id))
        page.referenced_assets.clear()

    def _clear_page_assets(self, page: _Page) -> None:
        for record in page.assets.values():
            self._release_asset_record(record)
        page.assets.clear()
        page.referenced_assets.clear()

    def _authorize_asset(self, page: _Page, asset_id: str) -> _Asset:
        """Reauthorize one asset at serve time against live backend state."""
        record = page.assets.get(asset_id)
        if record is None:
            raise SetupError("asset is unavailable")
        if not can_access_channel(self.env, page.channel_id, page.viewer, history=True):
            raise SetupError("asset access denied")
        source = record.source
        if not isinstance(source, tuple) or not source:
            return record
        owner = source[0]
        if owner == "attachment":
            _, channel_id, message_id, attachment_id = source
            try:
                message = self.env.backend.get_message(channel_id, message_id)
            except BackendError as exc:
                raise SetupError("asset is unavailable") from exc
            if not can_access_message(self.env, channel_id, message, page.viewer, history=True):
                raise SetupError("asset access denied")
            item = next(
                (item for item in message.attachments if str(item.get("id", "")) == attachment_id),
                None,
            )
            if item is None:
                raise SetupError("asset is unavailable")
            url = item.get("url")
            current = self.env.backend.cdn.get(url) if isinstance(url, str) else None
            if current is None and isinstance(url, str):
                supplied = self._explicit_assets.get(url)
                current = supplied[1] if supplied is not None else None
            if current is not None and hashlib.sha256(current).hexdigest() != record.digest:
                raise SetupError("asset was replaced")
        elif owner == "message":
            _, channel_id, message_id = source
            try:
                message = self.env.backend.get_message(channel_id, message_id)
            except BackendError as exc:
                raise SetupError("asset is unavailable") from exc
            if not can_access_message(self.env, channel_id, message, page.viewer, history=True):
                raise SetupError("asset access denied")
        elif owner in {"user_avatar", "default_avatar"}:
            _, user_id, avatar_key = source
            try:
                user = self.env.backend.get_user(user_id)
                channel = self.env.backend.get_channel(page.channel_id)
            except BackendError as exc:
                raise SetupError("asset is unavailable") from exc
            if channel.guild_id is None:
                member_allowed = user_id in channel.recipient_ids
            else:
                guild = self.env.backend.guilds.get(channel.guild_id)
                member_allowed = guild is not None and user_id in guild.members
            if not member_allowed and user.bot:
                member_allowed = any(
                    item.author_id == user_id
                    and can_access_message(self.env, page.channel_id, item, page.viewer, history=True)
                    for item in self.env.backend.messages.get(page.channel_id, {}).values()
                )
            if not member_allowed:
                raise SetupError("asset access denied")
            if owner == "user_avatar":
                if user.avatar != avatar_key:
                    raise SetupError("asset is unavailable")
            elif user.avatar is not None:
                raise SetupError("asset is unavailable")
        elif owner == "member_avatar":
            _, guild_id, user_id, avatar_key = source
            try:
                channel = self.env.backend.get_channel(page.channel_id)
            except BackendError as exc:
                raise SetupError("asset is unavailable") from exc
            if channel.guild_id != guild_id:
                raise SetupError("asset access denied")
            guild = self.env.backend.guilds.get(guild_id)
            member = guild.members.get(user_id) if guild is not None else None
            if member is None or member.avatar != avatar_key:
                raise SetupError("asset is unavailable")
        elif owner == "explicit":
            _, url = source
            supplied = self._explicit_assets.get(url)
            if supplied is None or hashlib.sha256(supplied[1]).hexdigest() != record.digest:
                raise SetupError("asset is unavailable")
        elif owner == "application_avatar":
            _, user_id, avatar_key = source
            try:
                user = self.env.backend.get_user(user_id)
            except BackendError as exc:
                raise SetupError("asset is unavailable") from exc
            if not user.bot or user.avatar != avatar_key:
                raise SetupError("asset is unavailable")
        return record

    def _asset_blob(self, record: _Asset) -> bytes:
        entry = self._blobs.get(record.digest) if record.digest is not None else None
        if entry is None:
            raise SetupError("asset is unavailable")
        return entry.data

    def _authorized_asset(self, context_id: str | None, asset_id: str) -> tuple[_Page, _Asset, bytes]:
        """Resolve, liveness-check, and authorize one asset; return its blob."""
        page = self._get_page(context_id)
        self._assert_capture_live(page)
        record = self._authorize_asset(page, asset_id)
        return page, record, self._asset_blob(record)

    def _asset(self, context_id: str | None, asset_id: str) -> tuple[str, bytes, str]:
        _, record, body = self._authorized_asset(context_id, asset_id)
        return record.contentType, body, record.filename

    async def _prepare_asset(
        self, context_id: str | None, asset_id: str, *, download: bool = False
    ) -> tuple[str, bytes, str]:
        _, record, body = self._authorized_asset(context_id, asset_id)
        content_type, filename = record.contentType, record.filename
        if download or not content_type.startswith("image/"):
            # Downloads and non-image media are served the original bytes;
            # only the display path returns the normalized PNG first frame.
            return content_type, body, filename
        if self._media_worker is None:
            self._media_worker = MediaWorker()
        digest = record.digest
        try:
            info = await self._media_worker.validate(digest if digest is not None else asset_id, body)
        except MediaError as exc:
            record.available = False
            record.diagnostic = str(exc)
            raise SetupError(str(exc)) from exc
        # Reauthorize after the awaited decode: access or membership may have
        # changed while validation was in flight.
        _, record, current_body = self._authorized_asset(context_id, asset_id)
        if record.digest != digest or current_body != body:
            raise SetupError("asset was replaced while decoding")
        entry = self._blobs.get(digest) if digest is not None else None
        if entry is None:
            raise SetupError("asset is unavailable")
        if not record.normalizedRetained:
            # The normalized copy is shared per blob: charge it only when the
            # first record retains it; later records just take a ref.
            if entry.normalized_refs == 0:
                if self._retained_media_bytes + len(info.normalized) > self._MAX_MEDIA_BYTES:
                    record.available = False
                    record.diagnostic = "session media budget exceeded after normalization"
                    raise SetupError(record.diagnostic)
                entry.normalized_size = len(info.normalized)
                self._retained_media_bytes += len(info.normalized)
            entry.normalized_refs += 1
            record.normalizedRetained = True
        record.width = info.width
        record.height = info.height
        record.frames = info.frames
        record.validated = True
        return info.content_type, info.normalized, filename


__all__ = ["_Asset", "_AssetOps", "_Blob", "_content_type"]
