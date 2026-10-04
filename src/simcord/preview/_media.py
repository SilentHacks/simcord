"""Optional media validation in a killable, resource-bounded child process."""

from __future__ import annotations

import asyncio
import io
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 16 * 1024 * 1024
MAX_DURATION_SECONDS = 10 * 60
WORKER_DEADLINE_SECONDS = 30.0
MAX_QUEUE = 8
MAX_ANIMATION_FRAMES = 18_000
MAX_VIDEO_FRAMES = 18_000
MAX_WORKER_RESULT_BYTES = MAX_OUTPUT_BYTES + MAX_CAPTURE_BYTES + 1024 * 1024


class MediaError(ValueError):
    """Media is unsupported, unsafe, or exceeds a Preview resource limit."""


class _TransientMediaError(MediaError):
    """Worker failures are retryable and must not poison the content verdict cache."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    format: str
    width: int
    height: int
    frames: int
    decoded_bytes: int
    normalized: bytes
    content_type: str
    kind: str = "image"
    duration: float = 0.0
    capture: bytes | None = None
    poster: bytes | None = None
    source_codecs: dict[str, str] | None = None
    display_codecs: dict[str, str] | None = None
    transformation: str = "normalized"
    quality_differences: tuple[str, ...] = ()
    effective_media_time: float = 0.0
    waveform: tuple[int, ...] = ()
    memory_limited: bool = False

    def as_wire(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "decodedBytes": self.decoded_bytes,
            "contentType": self.content_type,
            "kind": self.kind,
            "duration": self.duration,
            "sourceCodecs": self.source_codecs or {},
            "displayCodecs": self.display_codecs or {},
            "transformation": self.transformation,
            "qualityDifferences": self.quality_differences,
            "effectiveMediaTime": self.effective_media_time,
            "waveform": self.waveform,
        }


class _LimitedBuffer(io.BytesIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def write(self, value: bytes) -> int:
        if self.tell() + len(value) > self.limit:
            raise MediaError("media output exceeds the 10 MiB display or 64 MiB capture limit")
        return super().write(value)


def _png(image: Any, limit: int = MAX_CAPTURE_BYTES) -> bytes:
    output = _LimitedBuffer(limit)
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _process_media(blob: bytes, media_time: float | None) -> MediaInfo:
    if len(blob) > MAX_SOURCE_BYTES:
        raise MediaError("media exceeds the 10 MiB preview file limit")
    if media_time is not None and (
        isinstance(media_time, bool)
        or not isinstance(media_time, (int, float))
        or not math.isfinite(media_time)
        or media_time < 0
    ):
        raise MediaError("media_time must be a finite non-negative number")
    if blob.startswith(b"{") or blob.lstrip().startswith(b"{"):
        return _inspect_lottie(blob, media_time)
    if blob.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (
        blob.startswith(b"RIFF") and blob[8:12] == b"WEBP"
    ):
        return _inspect_raster(blob, media_time)
    return _inspect_av(blob, media_time)


def _inspect_raster(blob: bytes, media_time: float | None) -> MediaInfo:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - optional extra
        raise MediaError("Preview media requires Pillow; install simcord[preview]") from exc

    try:
        image = Image.open(io.BytesIO(blob))
        fmt = str(image.format or "").upper()
        if fmt not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise MediaError(f"unsupported inline media format {fmt or 'unknown'}")
        frames = int(getattr(image, "n_frames", 1))
        width, height = image.size
        if frames < 1 or frames > MAX_ANIMATION_FRAMES:
            raise MediaError("media animation exceeds the 18,000 frame worker limit")
        if width < 1 or height < 1 or width > MAX_DIMENSION or height > MAX_DIMENSION:
            raise MediaError("media dimensions exceed 8192 pixels per axis")
        if width * height > MAX_PIXELS:
            raise MediaError("media exceeds 16 megapixels per frame")

        if frames == 1:
            image.seek(0)
            image.load()
            oriented = ImageOps.exif_transpose(image).convert("RGBA")
            width, height = oriented.size
            if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
                raise MediaError("oriented media exceeds 8192 pixels per axis or 16 megapixels")
            normalized = _png(oriented, MAX_OUTPUT_BYTES)
            return MediaInfo(
                fmt,
                width,
                height,
                1,
                width * height * 4,
                normalized,
                "image/png",
                capture=normalized,
                poster=normalized,
            )

        effective_durations: list[int] = []
        total_ms = 0
        for frame_index in range(frames):
            image.seek(frame_index)
            raw_duration = image.info.get("duration", 100)
            if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
                raise MediaError("media contains invalid animation timing")
            if not math.isfinite(raw_duration) or raw_duration < 0:
                raise MediaError("media contains invalid animation timing")
            duration = int(raw_duration)
            active_duration = max(10, duration)
            effective_durations.append(active_duration)
            total_ms += active_duration
            if total_ms > MAX_DURATION_SECONDS * 1000:
                raise MediaError("media animation exceeds the 10 minute duration limit")

        requested_seconds = media_time or 0.0
        loop_count = image.info.get("loop")
        cycle_seconds = total_ms / 1000
        if loop_count == 0:
            selected_ms = int((requested_seconds % cycle_seconds) * 1000)
        elif loop_count is not None:
            selected_ms = int(min(requested_seconds, cycle_seconds * (int(loop_count) + 1) - 0.001) * 1000)
        else:
            selected_ms = int(min(requested_seconds, cycle_seconds - 0.001) * 1000)
        position_ms = selected_ms % total_ms
        elapsed = 0
        selected = frames - 1
        for index, duration in enumerate(effective_durations):
            if position_ms < elapsed + duration:
                selected = index
                break
            elapsed += duration

        capture_image = None
        for frame_index in range(frames):
            image.seek(frame_index)
            image.load()
            if frame_index == selected:
                capture_image = image.convert("RGBA").copy()
        if capture_image is None:  # pragma: no cover - Pillow reports empty animations earlier
            raise MediaError("media animation contains no decodable frames")
        capture = _png(capture_image, MAX_CAPTURE_BYTES)
        actual_ms = selected_ms - position_ms + sum(effective_durations[:selected])
        content_type = {"GIF": "image/gif", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
        return MediaInfo(
            fmt,
            width,
            height,
            frames,
            width * height * 4,
            blob,
            content_type,
            duration=total_ms / 1000,
            capture=capture,
            poster=capture,
            transformation="validated-animation-preserved",
            effective_media_time=actual_ms / 1000,
        )
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("media is not a valid PNG, JPEG, WebP, GIF, APNG, or animated WebP") from exc


def _inspect_lottie(blob: bytes, media_time: float | None = None) -> MediaInfo:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in values:
            if key in out:
                raise MediaError("Lottie JSON contains duplicate object keys")
            out[key] = value
        return out

    try:
        value = json.loads(
            blob,
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                MediaError(f"Lottie JSON contains invalid number {item}")
            ),
        )
    except MediaError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MediaError("Lottie sticker is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MediaError("Lottie sticker must contain a JSON object")

    complexity = {"nodes": 0, "keyframes": 0}

    def inspect(item: Any, depth: int = 0) -> None:
        if depth > 64:
            raise MediaError("Lottie exceeds the nesting limit of 64")
        if isinstance(item, dict):
            if "x" in item and isinstance(item["x"], str) and item["x"].strip():
                raise MediaError("Lottie expressions are not supported")
            if "fonts" in item or "chars" in item:
                raise MediaError("Lottie fonts and text glyph assets are not supported")
            for key in ("layers", "shapes"):
                if isinstance(item.get(key), list):
                    complexity["nodes"] += len(item[key])
            keyframes = item.get("k")
            if isinstance(keyframes, list) and any(
                isinstance(frame, dict) and "t" in frame for frame in keyframes
            ):
                complexity["keyframes"] += len(keyframes)
            for key, child in item.items():
                if key.lower() in {"url", "uri", "src"} and isinstance(child, str):
                    raise MediaError("Lottie external resources are not supported")
                if key in {"p", "u"} and isinstance(child, str) and child:
                    raise MediaError("Lottie external images are not supported")
                if isinstance(child, str) and child.lower().startswith(("http:", "https:", "data:")):
                    raise MediaError("Lottie external and data URLs are not supported")
                inspect(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                inspect(child, depth + 1)
        if complexity["nodes"] > 10_000:
            raise MediaError("Lottie exceeds the 10,000 shape/layer limit")
        if complexity["keyframes"] > 100_000:
            raise MediaError("Lottie exceeds the 100,000 keyframe limit")

    inspect(value)
    try:
        fps = float(value["fr"])
        start = float(value["ip"])
        end = float(value["op"])
        width = int(value["w"])
        height = int(value["h"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MediaError("Lottie composition metadata is incomplete") from exc
    if (
        not all(math.isfinite(number) for number in (fps, start, end))
        or fps <= 0
        or end <= start
        or width < 1
        or height < 1
        or width > MAX_DIMENSION
        or height > MAX_DIMENSION
        or width * height > MAX_PIXELS
    ):
        raise MediaError("Lottie composition dimensions or timing are invalid")
    duration = (end - start) / fps
    if duration > MAX_DURATION_SECONDS:
        raise MediaError("Lottie animation exceeds the 10 minute duration limit")
    for asset in value.get("assets", []):
        if not isinstance(asset, dict):
            raise MediaError("Lottie assets must be embedded shape precompositions")
        if "p" in asset or "u" in asset:
            raise MediaError("Lottie external images are not supported")
    requested = min(max(media_time or 0.0, 0.0), duration)
    first_frame = math.floor(start)
    last_frame = math.ceil(end) - 1
    selected_frame = min(last_frame, max(first_frame, math.floor(start + requested * fps)))
    return MediaInfo(
        "LOTTIE",
        width,
        height,
        max(1, last_frame - first_frame + 1),
        0,
        blob,
        "application/json",
        kind="lottie",
        duration=duration,
        transformation="validated-expression-free-lottie",
        effective_media_time=max(0.0, (selected_frame - start) / fps),
    )


def _inspect_av(blob: bytes, media_time: float | None) -> MediaInfo:
    try:
        import av
    except ImportError as exc:  # pragma: no cover - optional extra
        raise MediaError("Audio/video validation requires PyAV; install simcord[preview]") from exc

    try:
        source = av.open(io.BytesIO(blob), mode="r")
        videos = list(source.streams.video)
        audios = list(source.streams.audio)
        if len(videos) > 1 or len(audios) > 1 or not (videos or audios):
            raise MediaError("media must contain one audio and/or one video stream")
        if len(videos) + len(audios) != len(source.streams):
            raise MediaError("subtitle, data, and attachment tracks are not supported")
        duration = (
            float(source.duration / av.time_base)
            if source.duration is not None
            else max(
                (
                    float(stream.duration * stream.time_base)
                    for stream in (*videos, *audios)
                    if stream.duration is not None and stream.time_base is not None
                ),
                default=0.0,
            )
        )
        if not math.isfinite(duration) or duration <= 0:
            raise MediaError("audio/video duration is missing or invalid")
        if duration > MAX_DURATION_SECONDS:
            raise MediaError("audio/video exceeds the 10 minute duration limit")
        video = videos[0] if videos else None
        audio = audios[0] if audios else None
        source_codecs = {
            **({"video": str(video.codec_context.name)} if video is not None else {}),
            **({"audio": str(audio.codec_context.name)} if audio is not None else {}),
        }
        if video is not None:
            width, height = int(video.codec_context.width), int(video.codec_context.height)
            if (
                width < 1
                or height < 1
                or width > MAX_DIMENSION
                or height > MAX_DIMENSION
                or width * height > MAX_PIXELS
            ):
                raise MediaError("video dimensions exceed 8192 pixels per axis or 16 megapixels")
        else:
            width = height = 0
        source_format = str(source.format.name or "").lower()
        codecs_supported = (
            (video is None or source_codecs.get("video") in {"vp8", "vp9"})
            and (audio is None or source_codecs.get("audio") in {"opus", "vorbis"})
            and (
                (video is not None and "webm" in source_format)
                or (video is None and "ogg" in source_format and source_codecs.get("audio") == "opus")
            )
        )
        has_metadata = bool(source.metadata) or any(stream.metadata for stream in (*videos, *audios))
        transformation = (
            "transcode"
            if not codecs_supported
            else "metadata-stripped-remux"
            if has_metadata
            else "preserved"
        )
        target_format = "webm" if video is not None else "ogg"
        content_type = "video/webm" if video is not None else "audio/ogg"
        display_codecs = (
            {
                **({"video": "vp9"} if video is not None else {}),
                **({"audio": "opus"} if audio is not None else {}),
            }
            if transformation == "transcode"
            else source_codecs
        )

        rotation = 0
        if video is not None:
            try:
                rotation = int(float(video.metadata.get("rotate", "0"))) % 360
            except (TypeError, ValueError):
                rotation = 0
            if rotation not in {0, 90, 180, 270}:
                raise MediaError("video uses an unsupported non-right-angle rotation")
        oriented_width, oriented_height = (height, width) if rotation in {90, 270} else (width, height)
        requested = min(max(media_time or 0.0, 0.0), duration)
        capture_frame = None
        capture_timestamp = 0.0
        video_frames = 0
        audio_frames = 0
        waveform_totals = [0.0] * 64
        waveform_counts = [0] * 64
        waveform_resampler = (
            av.AudioResampler(
                format="s16",
                layout="mono",
                rate=int(audio.codec_context.sample_rate or 48_000),
            )
            if audio is not None
            else None
        )
        output_buffer: Any = _LimitedBuffer(MAX_OUTPUT_BYTES) if transformation != "preserved" else None
        output_container: Any = None
        video_encoder: Any = None
        audio_encoder: Any = None
        audio_encoder_resampler: Any = None
        remux_streams: dict[int, Any] = {}
        if output_buffer is not None:
            try:
                output_container = av.open(output_buffer, mode="w", format=target_format)
                if transformation == "metadata-stripped-remux":
                    for stream in (*videos, *audios):
                        output_stream = output_container.add_stream_from_template(stream)
                        output_stream.metadata.clear()
                        if stream is video and rotation:
                            output_stream.metadata["rotate"] = str(rotation)
                        remux_streams[stream.index] = output_stream
                else:
                    if video is not None:
                        rate = video.average_rate or video.guessed_rate or 30
                        video_encoder = output_container.add_stream("libvpx-vp9", rate=rate)
                        video_encoder.width = oriented_width
                        video_encoder.height = oriented_height
                        video_encoder.pix_fmt = "yuv420p"
                        for name in ("color_range", "colorspace", "color_primaries", "color_trc"):
                            color = getattr(video.codec_context, name, None)
                            if color is not None:
                                setattr(video_encoder.codec_context, name, color)
                    if audio is not None:
                        audio_encoder = output_container.add_stream("libopus", rate=48_000)
                        layout = str(audio.codec_context.layout.name or "stereo")
                        audio_encoder.layout = layout
                        audio_encoder_resampler = av.AudioResampler(format="fltp", layout=layout, rate=48_000)
            except Exception as exc:
                raise MediaError(f"required canonical {target_format} encoder is unavailable") from exc

        try:
            for frame in source.decode(*(*videos, *audios)):
                if isinstance(frame, av.VideoFrame):
                    video_frames += 1
                    if video_frames > MAX_VIDEO_FRAMES:
                        raise MediaError("video exceeds the 18,000 frame worker limit")
                    frame_width, frame_height = int(frame.width), int(frame.height)
                    if (
                        frame_width < 1
                        or frame_height < 1
                        or frame_width > MAX_DIMENSION
                        or frame_height > MAX_DIMENSION
                        or frame_width * frame_height > MAX_PIXELS
                    ):
                        raise MediaError("video frame exceeds 8192 pixels per axis or 16 megapixels")
                    timestamp = (
                        float(frame.pts * frame.time_base)
                        if frame.pts is not None and frame.time_base is not None
                        else 0.0
                    )
                    if timestamp <= requested:
                        capture_frame = frame.to_image().copy()
                        if rotation:
                            capture_frame = capture_frame.rotate(-rotation, expand=True)
                        capture_timestamp = max(0.0, timestamp)
                    if transformation == "transcode" and video_encoder is not None:
                        source_image = frame.to_image()
                        if rotation:
                            source_image = source_image.rotate(-rotation, expand=True)
                        converted = av.VideoFrame.from_image(source_image)
                        converted.pts = frame.pts
                        converted.time_base = frame.time_base
                        for packet in video_encoder.encode(converted):
                            output_container.mux(packet)
                        if output_buffer is not None and output_buffer.tell() > MAX_OUTPUT_BYTES:
                            raise MediaError("media output exceeds the 10 MiB display limit")
                elif isinstance(frame, av.AudioFrame):
                    audio_frames += 1
                    if waveform_resampler is not None:
                        for mono in waveform_resampler.resample(frame):
                            raw = memoryview(mono.planes[0])
                            step = max(2, (mono.samples // 16) * 2)
                            sample_rate = int(
                                mono.sample_rate
                                or (audio.codec_context.sample_rate if audio is not None else 48_000)
                                or 48_000
                            )
                            frame_time = (
                                float(mono.pts * mono.time_base)
                                if mono.pts is not None and mono.time_base is not None
                                else 0.0
                            )
                            for offset in range(0, len(raw) - 1, step):
                                sample = abs(int.from_bytes(raw[offset : offset + 2], "little", signed=True))
                                sample_time = max(0.0, frame_time + (offset // 2) / sample_rate)
                                bucket = min(63, int(sample_time / duration * 64))
                                waveform_totals[bucket] += sample / 32768
                                waveform_counts[bucket] += 1
                    if transformation == "transcode" and audio_encoder is not None:
                        for converted in audio_encoder_resampler.resample(frame):
                            for packet in audio_encoder.encode(converted):
                                output_container.mux(packet)
                            if output_buffer is not None and output_buffer.tell() > MAX_OUTPUT_BYTES:
                                raise MediaError("media output exceeds the 10 MiB display limit")
            if remux_streams:
                source.seek(0)
                for packet in source.demux(*(*videos, *audios)):
                    if packet.dts is None:
                        continue
                    packet.stream = remux_streams[packet.stream.index]
                    output_container.mux(packet)
                    if output_buffer is not None and output_buffer.tell() > MAX_OUTPUT_BYTES:
                        raise MediaError("media output exceeds the 10 MiB display limit")
            if video_encoder is not None:
                for packet in video_encoder.encode(None):
                    output_container.mux(packet)
            if audio_encoder is not None:
                for converted in audio_encoder_resampler.resample(None):
                    for packet in audio_encoder.encode(converted):
                        output_container.mux(packet)
                for packet in audio_encoder.encode(None):
                    output_container.mux(packet)
            if output_container is not None:
                output_container.close()
        except MediaError:
            raise
        except Exception as exc:
            raise MediaError("audio/video stream is truncated, corrupt, or cannot be decoded") from exc

        if video is not None and (video_frames == 0 or capture_frame is None):
            raise MediaError("video contains no decodable frame at media_time")
        if audio is not None and audio_frames == 0:
            raise MediaError("audio contains no decodable frames")
        capture = _png(capture_frame) if capture_frame is not None else None
        normalized = output_buffer.getvalue() if output_buffer is not None else blob
        if normalized is not blob and len(normalized) > MAX_OUTPUT_BYTES:
            raise MediaError("media output exceeds the 10 MiB display limit")
        waveform = (
            tuple(
                max(1, min(100, round(100 * waveform_totals[index] / waveform_counts[index])))
                if waveform_counts[index]
                else 1
                for index in range(64)
            )
            if audio is not None
            else ()
        )
        quality_differences = (
            ("video/audio codec converted to canonical VP9/Opus",)
            if transformation == "transcode" and video is not None
            else ("audio codec converted to canonical Opus",)
            if transformation == "transcode"
            else ()
        )
        return MediaInfo(
            source_format,
            oriented_width,
            oriented_height,
            max(1, video_frames),
            oriented_width * oriented_height * 4 if video is not None else 0,
            normalized,
            content_type,
            kind="video" if video is not None else "audio",
            duration=duration,
            capture=capture,
            poster=capture,
            source_codecs=source_codecs,
            display_codecs=display_codecs,
            transformation=transformation,
            quality_differences=quality_differences,
            effective_media_time=capture_timestamp if video is not None else requested,
            waveform=waveform,
        )
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("audio/video media is unsupported or malformed") from exc


def _read_result(stdout: bytes, original: bytes) -> MediaInfo:
    try:
        if len(stdout) > MAX_WORKER_RESULT_BYTES:
            raise ValueError("media worker result exceeds the 75 MiB wire limit")
        metadata_length = struct.unpack_from("!I", stdout, 0)[0]
        if metadata_length > 1024 * 1024 or 4 + metadata_length + 24 > len(stdout):
            raise ValueError("invalid media worker metadata length")
        offset = 4
        metadata = json.loads(stdout[offset : offset + metadata_length])
        offset += metadata_length
        lengths = struct.unpack_from("!QQQ", stdout, offset)
        offset += 24
        payloads: list[bytes] = []
        total_payload = 0
        for length in lengths:
            total_payload += length
            if (
                length > MAX_CAPTURE_BYTES + MAX_OUTPUT_BYTES
                or total_payload > MAX_CAPTURE_BYTES + MAX_OUTPUT_BYTES
                or offset + length > len(stdout)
            ):
                raise ValueError("invalid media worker output length")
            payloads.append(stdout[offset : offset + length])
            offset += length
        if offset != len(stdout):
            raise ValueError("trailing media worker output")
        if "error" in metadata:
            raise MediaError(str(metadata["error"]))
        references = metadata.pop("_payloadRefs")
        values = [
            original if reference == "source" else payloads[reference] if reference is not None else None
            for reference in references
        ]
        return MediaInfo(
            str(metadata["format"]),
            int(metadata["width"]),
            int(metadata["height"]),
            int(metadata["frames"]),
            int(metadata["decodedBytes"]),
            values[0] or original,
            str(metadata["contentType"]),
            kind=str(metadata["kind"]),
            duration=float(metadata["duration"]),
            capture=values[1],
            poster=values[2],
            source_codecs=dict(metadata.get("sourceCodecs", {})),
            display_codecs=dict(metadata.get("displayCodecs", {})),
            transformation=str(metadata["transformation"]),
            quality_differences=tuple(metadata.get("qualityDifferences", ())),
            effective_media_time=float(metadata.get("effectiveMediaTime", 0)),
            waveform=tuple(int(item) for item in metadata.get("waveform", ())),
            memory_limited=bool(metadata.get("memoryLimited")),
        )
    except MediaError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError, struct.error) as exc:
        raise _TransientMediaError("media worker returned an invalid result") from exc


def _worker_payload(info: MediaInfo, original: bytes) -> tuple[dict[str, Any], tuple[bytes, ...]]:
    values = (info.normalized, info.capture, info.poster)
    unique: list[bytes] = []
    references: list[int | str | None] = []
    for value in values:
        if value is None:
            references.append(None)
        elif value is original:
            references.append("source")
        else:
            try:
                references.append(unique.index(value))
            except ValueError:
                references.append(len(unique))
                unique.append(value)
    metadata = info.as_wire()
    metadata["_payloadRefs"] = references
    metadata["memoryLimited"] = info.memory_limited
    return metadata, tuple(unique)


class MediaWorker:
    """One serial, killable subprocess queue shared by a Preview session."""

    def __init__(self) -> None:
        self._cache: dict[str, MediaError] = {}
        self._inflight: dict[tuple[str, float | None], asyncio.Task[MediaInfo]] = {}
        self._released: set[str] = set()
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._closed = False

    async def _decode(self, key: str, blob: bytes, media_time: float | None) -> MediaInfo:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WORKER_DEADLINE_SECONDS
        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout_at(deadline):
                async with self._lock:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(Path(__file__).with_name("_media_worker.py")),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    self._process = process
                    request = struct.pack("!dQ", -1.0 if media_time is None else media_time, len(blob)) + blob
                    stdout, _ = await process.communicate(request)
                    if process.returncode != 0:
                        raise _TransientMediaError("media worker exited before producing a validated result")
                    info = _read_result(stdout, blob)
                    if key not in self._released:
                        self._cache.pop(key, None)
                    return info
        except TimeoutError as exc:
            error = _TransientMediaError("media worker exceeded the 30 second deadline and was terminated")
            raise error from exc
        except asyncio.CancelledError:
            raise
        except _TransientMediaError:
            raise
        except MediaError as exc:
            if key not in self._released:
                self._cache[key] = exc
            raise
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1)
                except (TimeoutError, ProcessLookupError):
                    pass
            if self._process is process:
                self._process = None

    def _finish(self, task_key: tuple[str, float | None], task: asyncio.Task[MediaInfo]) -> None:
        if self._inflight.get(task_key) is task:
            self._inflight.pop(task_key, None)
        if task_key[0] in self._released and not any(key[0] == task_key[0] for key in self._inflight):
            self._cache.pop(task_key[0], None)
            self._released.discard(task_key[0])

    async def validate(self, key: str, blob: bytes, *, media_time: float | None = None) -> MediaInfo:
        if self._closed:
            raise MediaError("preview media worker is closed")
        if len(blob) > MAX_SOURCE_BYTES:
            raise MediaError("media exceeds the 10 MiB preview file limit")
        if media_time is not None and (
            isinstance(media_time, bool)
            or not isinstance(media_time, (int, float))
            or not math.isfinite(media_time)
            or media_time < 0
        ):
            raise MediaError("media_time must be a finite non-negative number")
        media_time = 0.0 if media_time is None else float(media_time)
        self._released.discard(key)
        cached = self._cache.get(key)
        if cached is not None:
            raise cached
        task_key = (key, media_time)
        task = self._inflight.get(task_key)
        if task is None:
            if len(self._inflight) >= MAX_QUEUE:
                raise MediaError("preview media worker queue is full (maximum 8 jobs)")
            task = asyncio.create_task(self._decode(key, blob, media_time))
            self._inflight[task_key] = task
            task.add_done_callback(lambda done: self._finish(task_key, done))
        return await asyncio.shield(task)

    def release(self, key: str) -> None:
        self._cache.pop(key, None)
        tasks = [task for (asset, _), task in self._inflight.items() if asset == key]
        if tasks:
            self._released.add(key)
            for task in tasks:
                task.cancel()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        tasks = tuple(self._inflight.values())
        for task in tasks:
            task.cancel()
        if process is not None and process.returncode is None:
            process.kill()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except (TimeoutError, ProcessLookupError):
                pass
        self._cache.clear()
        self._inflight.clear()
        self._released.clear()
        self._process = None


__all__ = [
    "MAX_CAPTURE_BYTES",
    "MAX_DIMENSION",
    "MAX_DURATION_SECONDS",
    "MAX_PIXELS",
    "MAX_QUEUE",
    "MAX_SOURCE_BYTES",
    "MediaError",
    "MediaInfo",
    "MediaWorker",
]
