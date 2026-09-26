"""Private stdin/stdout entry point for the killable media process."""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path


def _limit_memory() -> bool:
    if sys.platform != "linux":
        return False
    try:
        import resource

        limit = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, OSError, ValueError):
        return False
    return True


def main() -> int:
    memory_limited = _limit_memory()
    module_path = Path(__file__).with_name("_media.py")
    spec = importlib.util.spec_from_file_location("_simcord_preview_media", module_path)
    if spec is None or spec.loader is None:
        return 2
    media = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = media
    spec.loader.exec_module(media)

    header = sys.stdin.buffer.read(16)
    if len(header) != 16:
        return 2
    media_time, length = struct.unpack("!dQ", header)
    if length > media.MAX_SOURCE_BYTES:
        return 2
    blob = sys.stdin.buffer.read(length)
    if len(blob) != length:
        return 2

    try:
        info = media._process_media(blob, None if media_time < 0 else media_time)
        metadata, payloads = media._worker_payload(info, blob)
        metadata["memoryLimited"] = memory_limited
    except media.MediaError as exc:
        metadata = {"error": str(exc), "memoryLimited": memory_limited}
        payloads = ()
    encoded = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    output = sys.stdout.buffer
    output.write(struct.pack("!I", len(encoded)))
    output.write(encoded)
    output.write(struct.pack("!QQQ", *(len(item) for item in (*payloads, b"", b"", b"")[:3])))
    for item in payloads:
        output.write(item)
    output.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
