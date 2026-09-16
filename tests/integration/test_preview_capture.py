import asyncio
import io

import pytest

pytest.importorskip("PIL")
pytest.importorskip("playwright")

from PIL import Image

import simcord
from simcord.preview._media import MediaError, MediaWorker


def _image(fmt: str, size: tuple[int, int] = (2, 2), frames: int = 1) -> bytes:
    images = [Image.new("RGBA", size, (index, 20, 40, 255)) for index in range(frames)]
    output = io.BytesIO()
    if fmt == "GIF":
        images[0].save(output, format=fmt, save_all=True, append_images=images[1:], loop=0, duration=1)
    else:
        images[0].save(output, format=fmt)
    return output.getvalue()


@pytest.mark.asyncio
async def test_preview_media_worker_validates_limits_and_lifecycle():
    worker = MediaWorker()
    valid = await worker.validate("valid", _image("PNG"))
    assert (valid.format, valid.width, valid.height, valid.frames) == ("PNG", 2, 2, 1)
    assert valid.content_type == "image/png"
    assert valid.normalized.startswith(b"\x89PNG")

    too_wide = _image("PNG", (8193, 1))
    with pytest.raises(MediaError, match="dimensions exceed"):
        await worker.validate("wide", too_wide)
    with pytest.raises(MediaError, match="dimensions exceed"):
        await worker.validate("wide", too_wide)
    with pytest.raises(MediaError, match="valid PNG"):
        await worker.validate("broken", b"not an image")
    with pytest.raises(MediaError, match="64 MiB"):
        await worker.validate("decoded-budget", _image("GIF", (1024, 1024), 17))
    with pytest.raises(MediaError, match="unsupported inline media format"):
        await worker.validate("bmp", _image("BMP"))
    with pytest.raises(MediaError, match="10 MiB"):
        await worker.validate("huge", b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(MediaError, match="100 frames"):
        await worker.validate("animated", _image("GIF", frames=101))

    await worker.close()
    await worker.close()
    with pytest.raises(MediaError, match="worker is closed"):
        await worker.validate("after-close", _image("PNG"))


@pytest.mark.asyncio
async def test_preview_screenshot_surface_viewport_and_incomplete(tmp_path, env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice], width=640, height=360) as preview:
        surface = await preview.screenshot(tmp_path / "surface.png")
        assert surface.mode == "surface"
        assert surface.complete is True
        assert surface.ready is True
        assert surface.output_width > 0 and surface.output_height > 0
        assert (tmp_path / "surface.png").read_bytes().startswith(b"\x89PNG")

        viewport = await preview.screenshot(tmp_path / "viewport.png", mode="viewport")
        assert viewport.mode == "viewport"
        assert (viewport.output_width, viewport.output_height) == (640, 360)
        assert (tmp_path / "viewport.png").exists()
        with pytest.raises(simcord.SetupError, match="capture mode"):
            await preview.screenshot(tmp_path / "bad.png", mode="document")
    empty = env.guild.create_text_channel("empty")
    async with env.preview(empty, viewers=[alice]) as preview:
        sentinel = tmp_path / "atomic.png"
        sentinel.write_bytes(b"keep")
        with pytest.raises(simcord.SetupError, match="incomplete"):
            await preview.screenshot(sentinel)
        assert sentinel.read_bytes() == b"keep"
        incomplete = await preview.screenshot(
            tmp_path / "allowed.png", mode="viewport", allow_incomplete=True
        )
        assert incomplete.complete is False
        assert incomplete.diagnostics[0]["code"] == "target-unavailable"


@pytest.mark.asyncio
async def test_preview_screenshot_busy_cancellation_and_idempotent_close(tmp_path, env, channel, alice):
    await alice.slash(channel, "panel")
    preview = env.preview(channel, viewers=[alice])
    await preview.__aenter__()
    try:
        first = asyncio.create_task(preview.screenshot(tmp_path / "first.png"))
        await asyncio.sleep(0)
        with pytest.raises(simcord.SetupError, match="busy"):
            await preview.screenshot(tmp_path / "second.png")
        await first
    finally:
        await preview.close()
        await preview.close()
        await preview.wait_closed()
        assert env._preview is None
