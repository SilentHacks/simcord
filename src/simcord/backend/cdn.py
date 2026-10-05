"""In-memory fake CDN for attachments and other binary assets."""

from __future__ import annotations

import mimetypes
from typing import Any

CDN_BASE = "https://cdn.simcord.invalid"
_STICKER_EXTENSIONS = {1: "png", 2: "png", 3: "json", 4: "gif"}


def sticker_url(sticker_id: int, format_type: int) -> str:
    return f"{CDN_BASE}/stickers/{sticker_id}.{_STICKER_EXTENSIONS[format_type]}"


class CdnStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def store_attachment(
        self, attachment_id: int, channel_id: int, filename: str, data: bytes, description: str | None
    ) -> dict[str, Any]:
        url = f"{CDN_BASE}/attachments/{channel_id}/{attachment_id}/{filename}"
        self._blobs[url] = data
        return {
            "id": str(attachment_id),
            "filename": filename,
            "description": description,
            "size": len(data),
            "url": url,
            "proxy_url": url,
            "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
        }

    def store_sticker(self, sticker_id: int, format_type: int, data: bytes) -> str:
        url = sticker_url(sticker_id, format_type)
        self._blobs[url] = data
        return url

    def get(self, url: str) -> bytes | None:
        return self._blobs.get(url)
