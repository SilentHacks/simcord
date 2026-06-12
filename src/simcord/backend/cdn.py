"""In-memory fake CDN for attachments and other binary assets."""

from __future__ import annotations

from typing import Any

CDN_BASE = "https://cdn.simcord.invalid"


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
            "content_type": None,
        }

    def get(self, url: str) -> bytes | None:
        return self._blobs.get(url)
