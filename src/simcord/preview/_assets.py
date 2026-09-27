"""Content-addressed asset retention, authorization, and normalized serving."""

from __future__ import annotations

import hashlib
import math
import mimetypes
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from ..backend.access import can_access_channel, can_access_message
from ..backend.cdn import CDN_BASE, sticker_url
from ..backend.errors import BackendError, SetupError
from ..components import walk_components
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
    normalized: bytes | None = None


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
    mediaKind: str | None = None
    duration: float | None = None
    sourceCodecs: dict[str, str] | None = None
    displayCodecs: dict[str, str] | None = None
    transformation: str | None = None
    qualityDifferences: tuple[str, ...] = ()
    effectiveMediaTime: float | None = None
    waveform: tuple[int, ...] = ()
    workerMemoryLimited: bool | None = None
    displayContentType: str | None = None
    capture: bytes | None = None
    poster: bytes | None = None
    captureTime: float | None = None
    captureBytes: int = 0

    def to_wire(self) -> dict[str, Any]:
        """Public asset record — internal bookkeeping keys never reach the wire."""
        out: dict[str, Any] = {
            "id": self.id,
            "filename": self.filename,
            "contentType": self.contentType,
            "available": bool(self.available),
            "displayReady": bool(self.available and self.validated),
        }
        if self.bytes is not None:
            out["bytes"] = self.bytes
        if self.available and self.validated and self.width and self.height:
            out["displayWidth"] = self.width
            out["displayHeight"] = self.height
        if self.available and self.validated:
            out.update(
                mediaKind=self.mediaKind,
                duration=self.duration,
                frames=self.frames,
                sourceCodecs=self.sourceCodecs or {},
                displayCodecs=self.displayCodecs or {},
                transformation=self.transformation,
                qualityDifferences=self.qualityDifferences,
                effectiveMediaTime=self.effectiveMediaTime,
                waveform=self.waveform,
                workerMemoryLimited=self.workerMemoryLimited,
            )
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

    @staticmethod
    def _capture_size(
        entry: _Blob, capture: bytes | None, poster: bytes | None, normalized: bytes | None = None
    ) -> int:
        retained = [entry.data]
        display = entry.normalized if normalized is None else normalized
        if display is not None:
            retained.append(display)
        size = 0
        for value in (capture, poster):
            if value is not None and value not in retained:
                retained.append(value)
                size += len(value)
        return size

    def _release_blob(self, digest: str, *, normalized: bool = False) -> None:
        entry = self._blobs.get(digest)
        if entry is None:
            return
        if normalized and entry.normalized_refs > 0:
            entry.normalized_refs -= 1
            if entry.normalized_refs == 0:
                self._retained_media_bytes -= entry.normalized_size
                entry.normalized_size = 0
                entry.normalized = None
                if self._media_worker is not None:
                    self._media_worker.release(digest)
        entry.refs -= 1
        if entry.refs <= 0:
            self._retained_media_bytes -= len(entry.data) + entry.normalized_size
            if self._media_worker is not None:
                self._media_worker.release(digest)
            del self._blobs[digest]
        self._retained_media_bytes = max(0, self._retained_media_bytes)

    def _release_asset_record(self, record: _Asset) -> None:
        self._retained_media_bytes = max(0, self._retained_media_bytes - record.captureBytes)
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
        if not record.available or record.digest is None:
            raise SetupError("asset is unavailable")
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
            if record.key.startswith("url:"):
                url_key, separator, message_ref = record.key[4:].rpartition(":message:")
                if not separator or message_ref != f"{channel_id}:{message_id}":
                    raise SetupError("asset is unavailable")
                url = url_key
                referenced = any(
                    isinstance(media, dict) and media.get("url") == url
                    for embed in message.embeds
                    if isinstance(embed, dict)
                    for media in (embed.get("image"), embed.get("thumbnail"), embed.get("video"))
                ) or any(
                    isinstance(embed.get(field), dict)
                    and (embed[field].get("icon_url") or embed[field].get("icon_proxy_url")) == url
                    for embed in message.embeds
                    if isinstance(embed, dict)
                    for field in ("author", "footer")
                )
                if not referenced:
                    for component in walk_components(message.components):
                        media = (component.get("media"), component.get("file"))
                        if any(isinstance(item, dict) and item.get("url") == url for item in media):
                            referenced = True
                            break
                        if any(
                            isinstance(item, dict)
                            and isinstance(item.get("media"), dict)
                            and item["media"].get("url") == url
                            for item in component.get("items", [])
                        ):
                            referenced = True
                            break
                        if component.get("style") == 6:
                            presentation = page.preview._sku_presentations.get(
                                str(component.get("sku_id", ""))
                            )
                            if presentation is not None and presentation.get("icon_url") == url:
                                referenced = True
                                break
            elif record.key.startswith("webhook-avatar:"):
                _, source_channel, source_message, url = record.key.split(":", 3)
                referenced = (
                    source_channel == str(channel_id)
                    and source_message == str(message_id)
                    and message.author_avatar == url
                )
            else:
                raise SetupError("asset is unavailable")
            if not referenced:
                raise SetupError("asset is unavailable")
            current = self.env.backend.cdn.get(url)
            if current is None:
                supplied = self._explicit_assets.get(url)
                current = supplied[1] if supplied is not None else None
            if current is None:
                raise SetupError("asset is unavailable")
            if hashlib.sha256(current).hexdigest() != record.digest:
                raise SetupError("asset was replaced")
        elif owner == "emoji":
            _, emoji_id = source
            try:
                identity = int(emoji_id)
            except (TypeError, ValueError) as exc:
                raise SetupError("asset is unavailable") from exc
            backend = self.env.backend
            emoji = backend.application_emojis.get(identity)
            if emoji is None:
                channel = backend.channels.get(page.channel_id)
                guild = (
                    backend.guilds.get(channel.guild_id)
                    if channel is not None and channel.guild_id is not None
                    else None
                )
                emoji = guild.emojis.get(identity) if guild is not None else None
                if guild is not None and emoji is not None and emoji.role_ids:
                    member = guild.members.get(page.viewer.id)
                    if member is None or not set(emoji.role_ids).intersection(member.role_ids):
                        raise SetupError("asset access denied")
            if emoji is None or not emoji.available:
                raise SetupError("asset is unavailable")
            extension = "gif" if emoji.animated else "png"
            url = f"{CDN_BASE}/emojis/{identity}.{extension}"
            current = backend.cdn.get(url)
            if current is None and (supplied := self._explicit_assets.get(url)) is not None:
                current = supplied[1]
            if current is None:
                raise SetupError("asset is unavailable")
            if hashlib.sha256(current).hexdigest() != record.digest:
                raise SetupError("asset was replaced")
        elif owner == "sticker":
            _, guild_id, sticker_id = source
            guild = self.env.backend.guilds.get(guild_id)
            sticker = guild.stickers.get(sticker_id) if guild is not None else None
            if sticker is None or not sticker.available:
                raise SetupError("asset is unavailable")
            url = sticker.url or sticker_url(sticker.id, sticker.format_type)
            current = self.env.backend.cdn.get(url)
            if current is not None and hashlib.sha256(current).hexdigest() != record.digest:
                raise SetupError("asset was replaced")
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

    @staticmethod
    def _active_document(record: _Asset, body: bytes) -> bool:
        content_type = record.contentType.lower()
        filename = record.filename.lower()
        prefix = body[:512].lstrip().lower()
        return (
            filename.endswith((".svg", ".html", ".htm", ".xhtml"))
            or content_type in {"image/svg+xml", "text/html", "application/xhtml+xml"}
            or prefix.startswith((b"<!doctype html", b"<html", b"<svg", b"<?xml"))
        )

    def _authorized_asset(self, context_id: str | None, asset_id: str) -> tuple[_Page, _Asset, bytes]:
        page = self._get_page(context_id)
        record = self._authorize_asset(page, asset_id)
        entry = self._blobs.get(record.digest) if record.digest is not None else None
        if entry is None:
            raise SetupError("asset is unavailable")
        return page, record, entry.data

    async def _prepare_asset(
        self,
        context_id: str | None,
        asset_id: str,
        *,
        download: bool = False,
        capture: bool = False,
        poster: bool = False,
        media_time: float | None = None,
    ) -> tuple[str, bytes, str]:
        _, record, body = self._authorized_asset(context_id, asset_id)
        content_type, filename = record.contentType, record.filename
        if download:
            return content_type, body, filename
        if self._active_document(record, body):
            raise SetupError("active documents are download-only")
        media_type = content_type.lower()
        if not (media_type.startswith(("image/", "audio/", "video/")) or media_type == "application/json"):
            raise SetupError("unsupported inline media type")
        if media_time is not None and (
            isinstance(media_time, bool)
            or not isinstance(media_time, (int, float))
            or not math.isfinite(media_time)
            or media_time < 0
        ):
            raise SetupError("media_time must be a finite non-negative number")
        if capture and poster:
            raise SetupError("media capture and poster are mutually exclusive")

        digest = record.digest
        entry = self._blobs.get(digest) if digest is not None else None
        if entry is None:
            raise SetupError("asset is unavailable")
        if poster and record.poster is not None:
            return "image/png", record.poster, filename
        if capture and record.capture is not None and record.captureTime == media_time:
            return "image/png", record.capture, filename
        if record.validated and not capture and not poster and entry.normalized is not None:
            return record.displayContentType or record.contentType, entry.normalized, filename
        if self._media_worker is None:
            self._media_worker = MediaWorker()
        try:
            info = await self._media_worker.validate(
                digest if digest is not None else asset_id,
                body,
                media_time=0 if poster else media_time if capture else None,
            )
        except MediaError as exc:
            record.diagnostic = str(exc)
            raise SetupError(str(exc)) from exc
        _, record, current_body = self._authorized_asset(context_id, asset_id)
        if record.digest != digest or current_body != body:
            raise SetupError("asset was replaced while decoding")
        entry = self._blobs.get(digest) if digest is not None else None
        if entry is None:
            raise SetupError("asset is unavailable")

        new_normalized_size = 0
        if not record.normalizedRetained and entry.normalized_refs == 0 and info.normalized != entry.data:
            new_normalized_size = len(info.normalized)
        next_capture = info.capture if capture else record.capture
        next_poster = (
            (info.poster or info.capture)
            if (poster or (not record.validated and not capture))
            else record.poster
        )
        new_capture_size = self._capture_size(entry, next_capture, next_poster, info.normalized)
        additional = new_normalized_size + new_capture_size - record.captureBytes
        if self._retained_media_bytes + additional > self._MAX_MEDIA_BYTES:
            record.diagnostic = "session media budget exceeded after media validation"
            raise SetupError(record.diagnostic)
        self._retained_media_bytes += additional
        if not record.normalizedRetained:
            if entry.normalized_refs == 0:
                entry.normalized_size = new_normalized_size
                entry.normalized = info.normalized
            entry.normalized_refs += 1
            record.normalizedRetained = True
        record.capture = next_capture
        record.poster = next_poster
        record.captureBytes = new_capture_size
        if capture:
            record.captureTime = media_time or 0.0
        record.width = info.width
        record.height = info.height
        record.frames = info.frames
        record.mediaKind = info.kind
        record.duration = info.duration
        record.sourceCodecs = info.source_codecs
        record.displayCodecs = info.display_codecs
        record.displayContentType = info.content_type
        record.transformation = info.transformation
        record.qualityDifferences = info.quality_differences
        record.effectiveMediaTime = info.effective_media_time
        record.waveform = info.waveform
        record.workerMemoryLimited = info.memory_limited
        record.diagnostic = None
        record.validated = True
        if capture and info.capture is not None:
            return "image/png", info.capture, filename
        if poster and info.poster is not None:
            return "image/png", info.poster, filename
        return info.content_type, info.normalized, filename


__all__ = ["_Asset", "_AssetOps", "_Blob", "_content_type"]
