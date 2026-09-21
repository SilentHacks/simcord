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
    with pytest.raises(ValueError, match="not protocol 2"):
        capture_visual_reference.validate_snapshot(
            {"protocolVersion": 1, "messages": {}, "profile": {}, "diagnostics": []}
        )
    with pytest.raises(ValueError, match="diagnostics are malformed"):
        capture_visual_reference.validate_snapshot(
            {"protocolVersion": 2, "messages": {}, "profile": {}, "diagnostics": ["not-an-object"]}
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
