"""Optional Pillow-backed media validation for authorized preview assets."""

from __future__ import annotations

import asyncio
import io
import warnings
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

MAX_DIMENSION = 8192
MAX_PIXELS = 16 * 1024 * 1024
MAX_FRAMES = 100
MAX_DECODED_BYTES = 64 * 1024 * 1024
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF"}


@dataclass(frozen=True, slots=True)
class MediaInfo:
    format: str
    width: int
    height: int
    frames: int
    decoded_bytes: int
    normalized: bytes
    content_type: str


class MediaError(ValueError):
    """Media is unsupported or exceeds a preview resource limit."""


def _inspect(blob: bytes) -> MediaInfo:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - optional extra
        raise MediaError("Preview media requires Pillow; install simcord[preview]") from exc
    if len(blob) > 10 * 1024 * 1024:
        raise MediaError("media exceeds the 10 MiB preview file limit")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            image = Image.open(io.BytesIO(blob))
            image.verify()
            image = Image.open(io.BytesIO(blob))
        except Exception as exc:
            # Every decoder failure mode produces the same cached rejection.
            raise MediaError("media is not a valid PNG, JPEG, WebP, or GIF") from exc
        if any("decompression bomb" in str(item.message).lower() for item in caught):
            raise MediaError("media rejected as a decompression bomb")
        fmt = str(image.format or "").upper()
        if fmt not in SUPPORTED_FORMATS:
            raise MediaError(f"unsupported inline media format {fmt or 'unknown'}")
        width, height = image.size
        if width < 1 or height < 1 or width > MAX_DIMENSION or height > MAX_DIMENSION:
            raise MediaError("media dimensions exceed 8192 pixels per axis")
        if width * height > MAX_PIXELS:
            raise MediaError("media exceeds 16 megapixels per frame")
        try:
            frames = int(getattr(image, "n_frames", 1))
        except (TypeError, ValueError):  # pragma: no cover - Pillow exposes an integer
            frames = 1
        if frames > MAX_FRAMES:
            raise MediaError("media animation exceeds 100 frames")
        decoded = width * height * 4 * frames
        if decoded > MAX_DECODED_BYTES:
            raise MediaError("media animation exceeds 64 MiB decoded RGBA budget")
        # Normalize once, without source metadata. Display always uses the
        # deterministic first frame re-encoded as PNG; the browser fetches the
        # original bytes only on the explicit download path.
        image.seek(0)
        rgba = image.convert("RGBA")
        output = io.BytesIO()
        rgba.save(output, format="PNG", optimize=False)
        return MediaInfo(fmt, width, height, frames, decoded, output.getvalue(), "image/png")


class MediaWorker:
    """One lazily created bounded decoder, shared by a Preview session.

    Decoded results are retained only while an owning asset retains the blob;
    failures and successes are also capped so abandoned keys cannot grow the
    worker without bound.
    """

    _MAX_INFLIGHT = 8
    _MAX_CACHE = 64

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simcord-preview-media")
        self._cache: OrderedDict[str, MediaInfo | MediaError] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[MediaInfo]] = {}
        self._closed = False

    def _remember(self, key: str, value: MediaInfo | MediaError) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._MAX_CACHE:
            self._cache.popitem(last=False)

    async def _decode(self, key: str, blob: bytes) -> MediaInfo:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(self._executor, _inspect, blob)
        except MediaError as exc:
            self._remember(key, exc)
            raise
        self._remember(key, result)
        return result

    async def validate(self, key: str, blob: bytes) -> MediaInfo:
        if self._closed:
            raise MediaError("preview media worker is closed")
        cached = self._cache.get(key)
        if isinstance(cached, MediaError):
            self._cache.move_to_end(key)
            raise cached
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= self._MAX_INFLIGHT:
                raise MediaError("preview media worker queue is full")
            task = asyncio.ensure_future(self._decode(key, blob))
            self._inflight[key] = task
            task.add_done_callback(lambda _task: self._inflight.pop(key, None))
        return await asyncio.shield(task)

    def release(self, key: str) -> None:
        """Forget a result after the last legitimate asset owner disappears."""
        if key not in self._inflight:
            self._cache.pop(key, None)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
        self._cache.clear()
        self._inflight.clear()


__all__ = [
    "MAX_DECODED_BYTES",
    "MAX_DIMENSION",
    "MAX_FRAMES",
    "MAX_PIXELS",
    "MediaError",
    "MediaInfo",
    "MediaWorker",
]
