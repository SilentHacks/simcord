import asyncio
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PIL")

from PIL import Image

import simcord
from simcord.preview._media import MediaError, MediaWorker


def _load_script(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compare_visual_reference = _load_script("compare_visual_reference", "compare_visual_reference.py")
capture_visual_reference = _load_script("capture_visual_reference", "capture_visual_reference.py")


def _save_image(
    path: Path, size: tuple[int, int], pixels: dict[tuple[int, int], tuple[int, int, int]]
) -> None:
    image = Image.new("RGB", size, "black")
    for position, color in pixels.items():
        image.putpixel(position, color)
    image.save(path)


def _coverage_row(
    fixture_id: str,
    *,
    reference_status: str = "available",
    comparison_status: str = "unrun",
    capture_name: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": fixture_id,
        "family": "tests",
        "featurePaths": [],
        "placement": "message",
        "variant": "idle",
        "profile": "test",
        "fixtureRevision": "test",
        "normalizedPayloadHash": "0" * 64,
        "assetHashes": {},
        "aliasToId": {},
        "preconditions": [],
        "steps": [],
        "pointerSteps": [],
        "keyboardSteps": [],
        "expected": {},
        "crop": {},
        "sourceViewport": {"width": 4, "height": 4},
        "messageColumnWidth": 4,
        "referenceStatus": reference_status,
        "reason": reason,
        "owner": "tests",
        "implementationStatus": "implemented",
        "comparisonStatus": comparison_status,
        "approvedDeviationIds": [],
        "evidenceEquivalentStateLinks": [],
        "referencePackKey": "test",
        "referencePackHash": None,
    }
    if capture_name is not None:
        row["historicalCapture"] = {
            "name": capture_name,
            "sha256": "1" * 64,
            "width": 4,
            "height": 4,
        }
    return row


def _coverage_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    captures = [row["historicalCapture"] for row in rows if isinstance(row.get("historicalCapture"), dict)]
    names = [{"name": capture["name"]} for capture in captures]
    return {
        "historicalCaptureCount": len(captures),
        "historicalCaptures": names,
        "references": names,
        "matrixFamilies": ["tests"],
        "rows": rows,
    }


def _image(fmt: str, size: tuple[int, int] = (2, 2), frames: int = 1) -> bytes:
    images = [Image.new("RGBA", size, (index, 20, 40, 255)) for index in range(frames)]
    output = io.BytesIO()
    if fmt == "GIF":
        images[0].save(output, format=fmt, save_all=True, append_images=images[1:], loop=0, duration=100)
    else:
        images[0].save(output, format=fmt)
    return output.getvalue()


@pytest.mark.asyncio
async def test_preview_media_worker_validates_limits_and_lifecycle(monkeypatch):
    worker = MediaWorker()
    valid = await worker.validate("valid", _image("PNG"))
    assert (valid.format, valid.width, valid.height, valid.frames) == ("PNG", 2, 2, 1)
    assert valid.content_type == "image/png"
    assert valid.normalized.startswith(b"\x89PNG")
    oriented_source = Image.new("RGB", (3, 2), (20, 40, 60))
    exif = Image.Exif()
    exif[274] = 6
    oriented_bytes = io.BytesIO()
    oriented_source.save(oriented_bytes, format="JPEG", exif=exif)
    oriented = await worker.validate("oriented", oriented_bytes.getvalue())
    assert (oriented.width, oriented.height) == (2, 3)
    with Image.open(io.BytesIO(oriented.normalized)) as display:
        assert display.size == (2, 3)
        assert not display.getexif()

    too_wide = _image("PNG", (8193, 1))
    with pytest.raises(MediaError, match="dimensions exceed"):
        await worker.validate("wide", too_wide)
    with pytest.raises(MediaError, match="dimensions exceed"):
        await worker.validate("wide", too_wide)
    with pytest.raises(MediaError):
        await worker.validate("broken", b"not an image")
    with pytest.raises(MediaError):
        await worker.validate("bmp", _image("BMP"))
    with pytest.raises(MediaError, match="10 MiB"):
        await worker.validate("huge", b"x" * (10 * 1024 * 1024 + 1))

    worker._lock = asyncio.Lock()
    await worker._lock.acquire()
    try:
        with monkeypatch.context() as deadline_patch:
            deadline_patch.setattr("simcord.preview._media.WORKER_DEADLINE_SECONDS", 0.01)
            with pytest.raises(MediaError, match="deadline"):
                await worker.validate("retry", _image("PNG"))
    finally:
        worker._lock.release()
    assert (await worker.validate("retry", _image("PNG"))).format == "PNG"
    animation = _image("GIF", frames=101)
    info = await worker.validate("animated", animation, media_time=0)
    second = await worker.validate("animated", animation, media_time=0.1)
    repeated = await worker.validate("animated", animation, media_time=0.1)
    assert info.frames == 101
    assert second.capture != info.capture
    assert repeated.capture == second.capture
    assert second.effective_media_time == 0.1

    await worker.close()
    await worker.close()
    with pytest.raises(MediaError, match="worker is closed"):
        await worker.validate("after-close", _image("PNG"))


@pytest.mark.asyncio
async def test_preview_screenshot_surface_viewport_and_incomplete(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
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
    pytest.importorskip("playwright")
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


def test_visual_comparator_reports_color_offset_regions_wrap_and_dimensions(tmp_path):
    reference = tmp_path / "reference.png"
    actual = tmp_path / "actual.png"
    _save_image(reference, (8, 8), {(2, 2): (255, 0, 0)})
    _save_image(actual, (8, 8), {(2, 2): (0, 0, 255)})
    report = compare_visual_reference.compare(
        reference,
        actual,
        tmp_path / "wrong-color",
        metadata={
            "geometry": {"emoji-like": [1, 1, 3, 3]},
            "wrapPoints": [{"line": 2, "note": "http://localhost:9000/capability"}],
        },
    )
    assert report["status"] == "fail"
    assert report["maximumChannelDifference"] == 255
    region = report["diagnostics"]["regions"][0]
    assert (region["name"], region["changedPixels"]) == ("emoji-like", 1)
    assert report["diagnostics"]["textWrap"] == [{"line": 2, "note": "[scrubbed-url]"}]
    assert "http://localhost:9000" not in (tmp_path / "wrong-color" / "report.json").read_text()

    missing = tmp_path / "missing.png"
    _save_image(
        missing,
        (8, 8),
        {(2, 2): (255, 255, 255), (3, 2): (255, 255, 255), (2, 3): (255, 255, 255), (3, 3): (255, 255, 255)},
    )
    _save_image(tmp_path / "missing-actual.png", (8, 8), {})
    missing_report = compare_visual_reference.compare(
        missing,
        tmp_path / "missing-actual.png",
        tmp_path / "missing-region",
        metadata={"geometry": {"emoji-like": {"x": 2, "y": 2, "width": 2, "height": 2}}},
    )
    assert missing_report["status"] == "fail"
    assert missing_report["diagnostics"]["regions"][0]["changedPixels"] == 4

    offset_reference = tmp_path / "offset-reference.png"
    offset_actual = tmp_path / "offset-actual.png"
    _save_image(offset_reference, (8, 8), {(2, 2): (255, 255, 255)})
    _save_image(offset_actual, (8, 8), {(4, 2): (255, 255, 255)})
    offset_report = compare_visual_reference.compare(
        offset_reference,
        offset_actual,
        tmp_path / "offset",
        metadata={"wrapPoints": [{"line": 4, "text": "extra line"}]},
    )
    assert offset_report["diagnostics"]["geometry"]["bestSmallOffset"] == [-2, 0]
    assert offset_report["diagnostics"]["geometry"]["differenceBoundingBox"] == [2, 2, 5, 3]
    assert offset_report["diagnostics"]["textWrap"] == [{"line": 4, "text": "extra line"}]

    dimension_actual = tmp_path / "dimension-actual.png"
    _save_image(dimension_actual, (7, 8), {})
    dimension_report = compare_visual_reference.compare(
        offset_reference,
        dimension_actual,
        tmp_path / "dimension",
    )
    assert dimension_report["status"] == "dimension_mismatch"
    assert (
        compare_visual_reference.main(
            [str(reference), str(actual), "--output-dir", str(tmp_path / "cli-diff")]
        )
        == 1
    )


def test_batch_comparison_requires_both_assets_and_escapes_contact_sheet_markup(tmp_path):
    missing_reference = _coverage_row("missing-reference", capture_name="missing-reference.png")
    missing_actual = _coverage_row("missing-actual", capture_name="missing-actual.png")
    blocked = _coverage_row(
        "fixture.blocked.<b>",
        reference_status="blocked",
        reason="blocked <script>alert(1)</script>",
    )
    not_comparable = _coverage_row(
        "<img src=x onerror=alert(1)>",
        comparison_status="not_comparable",
        reason='diagnostic <img src=x onerror="alert(1)">',
    )
    manifest_path = tmp_path / "coverage.json"
    manifest_path.write_text(
        json.dumps(_coverage_manifest([missing_reference, missing_actual, blocked, not_comparable])),
        encoding="utf-8",
    )
    reference_dir = tmp_path / "references"
    actual_dir = tmp_path / "actual"
    reference_dir.mkdir()
    actual_dir.mkdir()
    _save_image(actual_dir / "missing-reference.png", (4, 4), {})
    _save_image(reference_dir / "missing-actual.png", (4, 4), {})

    report, code = compare_visual_reference.batch_compare(
        manifest_path,
        reference_dir,
        actual_dir,
        tmp_path / "batch",
        required=True,
    )
    assert code == 2
    assert report["status"] == "invalid"
    assert report["counts"]["blocked"] == 3
    assert report["counts"]["not_comparable"] == 1
    rows = {row["fixtureId"]: row for row in report["rows"]}
    assert rows["missing-reference"]["reason"] == "missing reference or actual PNG"
    assert rows["missing-actual"]["reason"] == "missing reference or actual PNG"
    contact_sheet = (tmp_path / "batch" / "contact-sheet.html").read_text()
    assert "<script>alert(1)</script>" not in contact_sheet
    assert '<img src=x onerror="alert(1)">' not in contact_sheet
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in contact_sheet
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in contact_sheet


@pytest.mark.asyncio
async def test_capture_scrubs_capabilities_and_rejects_malformed_snapshots(tmp_path):
    with pytest.raises(ValueError):
        capture_visual_reference.validate_snapshot(
            {"protocolVersion": 2, "messages": {}, "profile": {}, "diagnostics": []}
        )
    with pytest.raises(ValueError):
        capture_visual_reference.validate_snapshot(
            {"protocolVersion": 3, "messages": {}, "profile": {}, "diagnostics": ["not-an-object"]}
        )

    results, code = await capture_visual_reference.capture(
        [{"id": "blocked", "referenceStatus": "blocked", "reason": "http://127.0.0.1:9000/secret"}],
        tmp_path,
    )
    assert (results, code) == (
        [{"fixtureId": "blocked", "status": "blocked", "reason": "http://127.0.0.1:9000/secret"}],
        2,
    )
    saved_report = (tmp_path / "capture-report.json").read_text()
    assert "http://127.0.0.1:9000" not in saved_report
    assert "[scrubbed-url]" in saved_report


@pytest.mark.asyncio
async def test_capture_blocks_unexercised_recipe_and_cleans_partial_files(tmp_path):
    row = _coverage_row("historical.ref.30.string.select.string.select.two.message")
    row.update(
        variant="two",
        steps=[{"input": "none", "action": "capture historical image without mutating state"}],
        expected={"backend": "unchanged", "visible": "historical two surface"},
    )
    path = tmp_path / capture_visual_reference._capture_name(row)
    metadata_path = path.with_suffix(".json")
    path.write_bytes(b"partial png")
    metadata_path.write_text("partial metadata")

    result = await capture_visual_reference._capture_row(row, tmp_path, object(), {})

    assert result["status"] == "blocked"
    assert "no supported capture recipe" in result["reason"]
    assert not path.exists()
    assert not metadata_path.exists()

    modal = _coverage_row("historical.ref.50.modals.modal.text.empty.dialog")
    modal.update(
        variant="empty",
        steps=[{"input": "none", "action": "capture historical image without mutating state"}],
        expected={"backend": "unchanged", "visible": "historical empty surface"},
    )
    modal_path = tmp_path / capture_visual_reference._capture_name(modal)
    modal_metadata = modal_path.with_suffix(".json")
    modal_path.write_bytes(b"partial png")
    modal_metadata.write_text("partial metadata")
    result = await capture_visual_reference._capture_row(modal, tmp_path, object(), {})
    assert result["status"] == "blocked"
    assert not modal_path.exists()
    assert not modal_metadata.exists()

    active = _coverage_row("historical.ref.20.buttons.button.primary.active")
    active.update(
        variant="active",
        steps=[{"input": "none", "action": "capture historical image without mutating state"}],
        expected={"backend": "unchanged", "visible": "historical active surface"},
    )
    assert capture_visual_reference._recipe(active) == ("active-button", "Primary")

    idle = _coverage_row("historical.ref.30.string.select.idle")
    idle.update(
        variant="idle",
        steps=[{"input": "none", "action": "capture historical image without mutating state"}],
        expected={"backend": "unchanged", "visible": "historical idle surface"},
    )
    assert capture_visual_reference._recipe(idle) == ("idle", None)


def test_capture_cli_fixture_selection_and_exit_status_are_deterministic(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    async def fake_capture(rows, output_dir):
        calls.append([str(row["id"]) for row in rows])
        return [{"fixtureId": rows[0]["id"], "status": "blocked"}], 2

    monkeypatch.setattr(capture_visual_reference, "capture", fake_capture)
    assert (
        capture_visual_reference.main(
            ["--fixture", "historical.ref.00.index.idle", "--output-dir", str(tmp_path / "selected")]
        )
        == 2
    )
    assert calls == [["historical.ref.00.index.idle"]]
    assert (
        capture_visual_reference.main(
            ["--fixture", "does-not-exist", "--output-dir", str(tmp_path / "missing")]
        )
        == 2
    )
    assert calls == [["historical.ref.00.index.idle"]]


@pytest.mark.asyncio
async def test_export_preserves_uncached_bot_guild_nickname(tmp_path):
    import discord
    from discord.ext import commands

    post_gallery = _load_script("discord_reference_bot", "discord_reference_bot.py").post_gallery
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    async with simcord.run(bot) as env:
        guild = env.create_guild("Reference calibration")
        channel = guild.create_text_channel("own-fixtures")
        viewer = guild.add_member(env.create_user("reference-viewer"))
        remote_guild = await bot.fetch_guild(guild.id)
        assert bot.user is not None
        member = await remote_guild.fetch_member(bot.user.id)
        await member.edit(nick="Calibration Guild Nick")
        target = await bot.fetch_channel(channel.id)
        assert isinstance(target, discord.TextChannel)
        remote_viewer = await remote_guild.fetch_member(viewer.id)
        output = tmp_path / "bot-batch"

        await post_gallery(target, remote_viewer, None, output_dir=output)

        manifest = json.loads((output / "bot-fixtures.json").read_text(encoding="utf-8"))
        embed = next(
            record for record in manifest["fixtures"] if record["referenceId"] == "REF-10-LEGACY-EMBED"
        )
        assert embed["author"]["displayName"] == "Calibration Guild Nick"


@pytest.mark.asyncio
async def test_embed_media_preserves_aspect_and_gallery_spoiler_retains_obscured_pixels(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    catalog = capture_visual_reference._load_catalog()
    bot_channel = env.bot.get_channel(channel.id)
    embed_payload = catalog.gallery_payload("REF-10-LEGACY-EMBED")
    gallery_payload = catalog.gallery_payload("REF-40-V2-LAYOUT-MEDIA")
    try:
        embed_message = await bot_channel.send(**embed_payload)
        gallery_message = await bot_channel.send(**gallery_payload)
        async with env.preview(channel, viewers=[alice], width=960, height=900) as preview:
            await preview.show(embed_message)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page(viewport={"width": 1200, "height": 1050})
                    await page.goto(preview.url)
                    await capture_visual_reference._ready(page)
                    dimensions = await page.locator("img.embed-thumbnail, img.embed-image").evaluate_all(
                        "images => images.map(image => ({width: image.getBoundingClientRect().width,"
                        "height: image.getBoundingClientRect().height,"
                        "ratio: image.naturalWidth / image.naturalHeight}))"
                    )
                    for image in dimensions:
                        assert abs(image["width"] / image["height"] - image["ratio"]) < 0.01
                    thumbnail = await page.locator("img.embed-thumbnail").bounding_box()
                    fields = await page.locator(".embed-fields").bounding_box()
                    assert thumbnail is not None and fields is not None
                    assert fields["x"] + fields["width"] <= thumbnail["x"]

                    await preview.show(gallery_message)
                    await page.reload()
                    await capture_visual_reference._ready(page)
                    hidden = page.locator(".component-gallery .spoiler-media").first
                    pixels = await hidden.screenshot()
                    with Image.open(io.BytesIO(pixels)) as image:
                        red, green, blue = image.convert("RGB").getpixel(
                            (image.width // 4, image.height // 4)
                        )
                    assert red > green > blue  # Obscured orange pixels, not an opaque grey placeholder.
                    assert (
                        await hidden.locator(".media-lightbox-trigger").get_attribute("aria-hidden") == "true"
                    )
                finally:
                    await browser.close()
    finally:
        catalog.close_payload(embed_payload)
        catalog.close_payload(gallery_payload)


@pytest.mark.parametrize("layout", ["channel", "message"])
@pytest.mark.asyncio
async def test_entity_menu_finishes_loading_and_shows_search_results(env, channel, alice, layout):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    catalog = capture_visual_reference._load_catalog()
    role = env.guild.create_role("Calibration Operator")
    payload = catalog.gallery_payload("REF-31-ENTITY-SELECTS")
    try:
        message = await env.bot.get_channel(channel.id).send(**payload)
        async with env.preview(channel, viewers=[alice], layout=layout, width=889, height=780) as preview:
            await preview.show(message)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page(viewport={"width": 1280, "height": 900})
                    await page.goto(preview.url)
                    await capture_visual_reference._ready(page)
                    await page.get_by_role("combobox", name="Choose a role", exact=True).click()
                    menu = page.locator('.select-list:popover-open [role="listbox"]')
                    await page.wait_for_function(
                        "() => document.querySelector('.select-list:popover-open [role=listbox]')"
                        "?.getAttribute('aria-busy') === 'false'",
                        timeout=5000,
                    )
                    await (
                        page.locator(".select-candidate-search")
                        .filter(visible=True)
                        .fill("Calibration Operator")
                    )
                    await page.wait_for_function(
                        "() => document.querySelector('.select-list:popover-open [role=listbox]')"
                        "?.querySelectorAll('[role=option]').length === 1"
                    )
                    assert await menu.get_by_role("option").get_attribute("data-value") == str(role.id)
                    assert await menu.get_attribute("aria-busy") == "false"
                finally:
                    await browser.close()
    finally:
        catalog.close_payload(payload)


@pytest.mark.asyncio
async def test_raster_capture_uses_animation_frames_format_loops_and_decoded_durations():
    red, blue, green = (255, 0, 0), (0, 0, 255), (0, 255, 0)
    cases = [
        ("PNG", True, 1, [100, 100], [(0, blue, 0), (0.15, green, 0.1), (0.25, green, 0.1)]),
        ("PNG", False, 1, [100, 100], [(0, red, 0), (0.25, blue, 0.1)]),
        ("WEBP", False, 1, [500, 500], [(0.15, red, 0), (0.65, blue, 0.5), (1.25, blue, 0.5)]),
        ("GIF", False, 1, [100, 100], [(0.25, red, 0.2), (0.3, blue, 0.3), (0.45, blue, 0.3)]),
        ("WEBP", False, 0, [100, 100], [(0.25, red, 0.2), (0.3, blue, 0.3)]),
        ("PNG", False, 1, [125.5, 124.5], [(0.126, blue, 0.1255), (0.3, blue, 0.1255)]),
        ("GIF", False, None, [100, 100], [(0.25, blue, 0.1)]),
    ]
    worker = MediaWorker()
    try:
        for case, (fmt, default_image, loop, durations, samples) in enumerate(cases):
            colors = [red, blue, green] if default_image else [red, blue]
            frames = [Image.new("RGB", (2, 2), color) for color in colors]
            source = io.BytesIO()
            options = {"loop": loop} if loop is not None else {}
            frames[0].save(
                source,
                format=fmt,
                save_all=True,
                append_images=frames[1:],
                default_image=default_image,
                duration=durations,
                lossless=True,
                **options,
            )
            for requested, expected_color, effective_time in samples:
                result = await worker.validate(str(case), source.getvalue(), media_time=requested)
                assert result.frames == 2
                assert result.duration == pytest.approx(sum(durations) / 1000)
                assert result.effective_media_time == pytest.approx(effective_time)
                with Image.open(io.BytesIO(result.capture)).convert("RGB") as captured:
                    assert captured.getpixel((0, 0)) == expected_color

        for fmt in ("PNG", "WEBP"):
            source = io.BytesIO()
            Image.new("RGB", (2, 2), red).save(
                source,
                format=fmt,
                save_all=True,
                append_images=[Image.new("RGB", (2, 2), blue)],
                duration=[400_000, 400_000],
                loop=1,
                lossless=True,
            )
            with pytest.raises(MediaError, match="10 minute duration limit"):
                await worker.validate(f"too-long-{fmt}", source.getvalue())
    finally:
        await worker.close()
