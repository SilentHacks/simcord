"""Compare local SimCord captures with registered Discord references.

The positional two-image form remains useful for one-off checks.  Batch mode
uses the committed coverage ledger and optional private reference pack without
resizing, aligning, or trusting capability-bearing URLs.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

_REFERENCE_STATUSES = {"missing", "available", "blocked"}
_IMPLEMENTATION_STATUSES = {"missing", "partial", "implemented"}
_COMPARISON_STATUSES = {"unrun", "fail", "pass", "not_comparable"}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_RE = re.compile(r"(?i)(https?://(?:127\.0\.0\.1|localhost)(?::\d+)?)(?:[/#?][^\s\"']*)?")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_REQUIRED_ROW_KEYS = {
    "id",
    "family",
    "featurePaths",
    "placement",
    "variant",
    "profile",
    "fixtureRevision",
    "normalizedPayloadHash",
    "assetHashes",
    "aliasToId",
    "preconditions",
    "steps",
    "pointerSteps",
    "keyboardSteps",
    "expected",
    "crop",
    "sourceViewport",
    "messageColumnWidth",
    "referenceStatus",
    "reason",
    "owner",
    "implementationStatus",
    "comparisonStatus",
    "approvedDeviationIds",
    "evidenceEquivalentStateLinks",
    "referencePackKey",
    "referencePackHash",
}


def scrub(value: Any) -> Any:
    """Remove loopback capability URLs from persisted diagnostics."""
    if isinstance(value, Mapping):
        return {
            str(key): scrub(item) for key, item in value.items() if str(key).lower() not in {"capability"}
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, tuple):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return _CAPABILITY_RE.sub("[scrubbed-url]", value)
    return value


def _contains_key(value: Any, wanted: str) -> bool:
    if isinstance(value, Mapping):
        return any(str(key).lower() == wanted or _contains_key(item, wanted) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, wanted) for item in value)
    return False


def validate_coverage_manifest(manifest: Mapping[str, object]) -> dict[str, int]:
    """Validate commit-01 ledger structure without loading private images."""
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise ValueError("coverage manifest rows must be an array")
    ids: set[str] = set()
    captures: set[str] = set()
    historical_count = 0
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"coverage row {index} must be an object")
        missing = _REQUIRED_ROW_KEYS - set(row)
        if missing:
            raise ValueError(f"coverage row {index} missing fields: {', '.join(sorted(missing))}")
        fixture_id = row["id"]
        if not isinstance(fixture_id, str) or not fixture_id:
            raise ValueError(f"coverage row {index} id must be a non-empty string")
        if fixture_id in ids:
            raise ValueError(f"duplicate coverage row id: {fixture_id}")
        ids.add(fixture_id)
        for field, allowed in (
            ("referenceStatus", _REFERENCE_STATUSES),
            ("implementationStatus", _IMPLEMENTATION_STATUSES),
            ("comparisonStatus", _COMPARISON_STATUSES),
        ):
            value = row[field]
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"{fixture_id} has invalid {field}: {value!r}")
        if row["referenceStatus"] == "blocked" and (
            not isinstance(row["reason"], str)
            or not row["reason"]
            or not isinstance(row["owner"], str)
            or not row["owner"]
        ):
            raise ValueError(f"{fixture_id} blocked rows require a reason and owner")
        payload_hash = row["normalizedPayloadHash"]
        if not isinstance(payload_hash, str) or not _HASH_RE.fullmatch(payload_hash):
            raise ValueError(f"{fixture_id} normalizedPayloadHash must be a lowercase SHA-256")
        capture = row.get("historicalCapture")
        if capture is not None:
            if not isinstance(capture, Mapping):
                raise ValueError(f"{fixture_id} historicalCapture must be an object")
            historical_count += 1
            name = capture.get("name")
            capture_hash = capture.get("sha256")
            width, height = capture.get("width"), capture.get("height")
            if not isinstance(name, str) or not name or name in captures:
                raise ValueError(f"{fixture_id} historical capture name is missing or duplicated")
            if not isinstance(capture_hash, str) or not _HASH_RE.fullmatch(capture_hash):
                raise ValueError(f"{fixture_id} historical capture SHA-256 is invalid")
            if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                raise ValueError(f"{fixture_id} historical capture dimensions must be positive integers")
            captures.add(name)
    declared_count = manifest.get("historicalCaptureCount")
    if declared_count != historical_count:
        raise ValueError(
            f"historicalCaptureCount={declared_count!r} does not match registered rows={historical_count}"
        )
    historical_index = manifest.get("historicalCaptures")
    if not isinstance(historical_index, list) or len(historical_index) != historical_count:
        raise ValueError("historicalCaptures must index every registered historical row")
    indexed_names = {
        item.get("name")
        for item in historical_index
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    if len(indexed_names) != historical_count or indexed_names != captures:
        raise ValueError("historicalCaptures names do not match historical row registrations")
    references = manifest.get("references")
    if not isinstance(references, list) or len(references) != historical_count:
        raise ValueError("references must register every historical capture exactly once")
    reference_names = {
        item.get("name")
        for item in references
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    if len(reference_names) != historical_count or reference_names != captures:
        raise ValueError("references names do not match historical row registrations")
    families = manifest.get("matrixFamilies")
    if not isinstance(families, list) or not all(isinstance(family, str) for family in families):
        raise ValueError("matrixFamilies must be an array of strings")
    actual_families = {row["family"] for row in rows if isinstance(row.get("family"), str)}
    missing_families = set(families) - actual_families
    if missing_families:
        raise ValueError(f"matrix families missing rows: {', '.join(sorted(missing_families))}")
    return {
        "rows": len(rows),
        "historicalCaptures": historical_count,
        "matrixFamilies": len(families),
        "blockedRows": sum(row["referenceStatus"] == "blocked" for row in rows),
        "notComparableRows": sum(row["comparisonStatus"] == "not_comparable" for row in rows),
    }


def load_coverage_manifest(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("coverage manifest root must be an object")
    validate_coverage_manifest(value)
    return value


def diff_stats(difference: Image.Image, threshold: int) -> tuple[int, int, float]:
    raw = difference.convert("RGB").tobytes()
    return (
        sum(max(raw[index : index + 3]) > threshold for index in range(0, len(raw), 3)),
        max(raw, default=0),
        sum(raw) / max(len(raw), 1),
    )


def compare_in_memory(reference: Image.Image, actual: Image.Image, threshold: int) -> tuple[int, int]:
    if reference.size != actual.size:
        raise ValueError("image sizes differ")
    difference = ImageChops.difference(reference.convert("RGB"), actual.convert("RGB"))
    changed, maximum, _ = diff_stats(difference, threshold)
    return changed, maximum


def _best_offset(reference: Image.Image, actual: Image.Image) -> tuple[int, int, int]:
    """Return the best small translation diagnostic; inputs are never shifted."""
    if reference.size != actual.size:
        return 0, 0, 0
    best = (0, 0, reference.width * reference.height + 1)
    ref = reference.convert("RGB")
    got = actual.convert("RGB")
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            shifted = Image.new("RGB", ref.size)
            shifted.paste(got, (dx, dy))
            changed, _, _ = diff_stats(ImageChops.difference(ref, shifted), 0)
            candidate = (changed, abs(dx) + abs(dy), abs(dy), abs(dx), dy, dx)
            current = (best[2], abs(best[0]) + abs(best[1]), abs(best[1]), abs(best[0]), best[1], best[0])
            if candidate < current:
                best = (dx, dy, changed)
    return best


def _region_boxes(
    metadata: Mapping[str, Any] | None, size: tuple[int, int]
) -> dict[str, tuple[int, int, int, int]]:
    boxes: dict[str, tuple[int, int, int, int]] = {}
    if metadata:
        geometry = metadata.get("geometry", metadata.get("regions"))
        if isinstance(geometry, Mapping):
            for name, box in geometry.items():
                if isinstance(box, Mapping):
                    values = tuple(box.get(key) for key in ("x", "y", "width", "height"))
                    if all(isinstance(value, (int, float)) for value in values):
                        x, y, width, height = (int(value) for value in values)
                        boxes[str(name)] = (x, y, x + width, y + height)
                elif (
                    isinstance(box, Sequence)
                    and len(box) == 4
                    and all(isinstance(value, (int, float)) for value in box)
                ):
                    boxes[str(name)] = tuple(int(value) for value in box)  # type: ignore[assignment]
    if not boxes:
        boxes["surface"] = (0, 0, size[0], size[1])
    return boxes


def _diagnostics(
    reference: Image.Image,
    actual: Image.Image,
    difference: Image.Image,
    threshold: int,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    regions = []
    for name, box in _region_boxes(metadata, reference.size).items():
        clipped = (
            max(0, box[0]),
            max(0, box[1]),
            min(reference.width, box[2]),
            min(reference.height, box[3]),
        )
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            continue
        ref_crop, act_crop = reference.crop(clipped), actual.crop(clipped)
        delta = ImageChops.difference(ref_crop, act_crop)
        changed, maximum, mean = diff_stats(delta, threshold)
        ref_mean = tuple(round(value, 2) for value in ImageStat.Stat(ref_crop).mean)
        act_mean = tuple(round(value, 2) for value in ImageStat.Stat(act_crop).mean)
        regions.append(
            {
                "name": name,
                "box": list(clipped),
                "changedPixels": changed,
                "maximumChannelDifference": maximum,
                "meanChannelDifference": mean,
                "referenceMeanColor": ref_mean,
                "actualMeanColor": act_mean,
            }
        )
    bbox = difference.getbbox()
    return {
        "regions": regions,
        "geometry": {
            "differenceBoundingBox": list(bbox) if bbox else None,
            "bestSmallOffset": list(_best_offset(reference, actual)[:2]),
        },
        "textWrap": scrub((metadata or {}).get("wrapPoints", [])),
    }


def compare(
    reference_path: Path,
    actual_path: Path,
    output_dir: Path,
    threshold: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    if threshold < 0 or threshold > 255:
        raise ValueError("threshold must be between 0 and 255")
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "reference": str(reference_path),
        "actual": str(actual_path),
        "threshold": threshold,
        "status": "fail",
    }
    try:
        with Image.open(reference_path) as reference_image, Image.open(actual_path) as actual_image:
            reference = reference_image.convert("RGB")
            actual = actual_image.convert("RGB")
    except (OSError, ValueError) as exc:
        report.update({"status": "invalid", "reason": f"unable to read PNG: {exc}"})
        return scrub(report)
    report["referenceSize"] = list(reference.size)
    report["actualSize"] = list(actual.size)
    if reference.size != actual.size:
        report.update({"status": "dimension_mismatch", "reason": "reference and actual dimensions differ"})
        (output_dir / "report.json").write_text(json.dumps(scrub(report), indent=2) + "\n", encoding="utf-8")
        return scrub(report)
    difference = ImageChops.difference(reference, actual)
    changed, maximum, mean = diff_stats(difference, threshold)
    difference_path = output_dir / "difference.png"
    overlay_path = output_dir / "overlay.png"
    difference.save(difference_path)
    Image.blend(reference, actual, 0.5).save(overlay_path)
    report.update(
        {
            "status": "pass" if changed == 0 else "fail",
            "size": list(reference.size),
            "changedPixels": changed,
            "totalPixels": reference.width * reference.height,
            "changedRatio": changed / max(reference.width * reference.height, 1),
            "maximumChannelDifference": maximum,
            "meanChannelDifference": mean,
            "difference": str(difference_path),
            "overlay": str(overlay_path),
            "diagnostics": _diagnostics(reference, actual, difference, threshold, metadata),
        }
    )
    (output_dir / "report.json").write_text(json.dumps(scrub(report), indent=2) + "\n", encoding="utf-8")
    return scrub(report)


def _safe_stem(fixture_id: str) -> str:
    return _SAFE_NAME_RE.sub("-", fixture_id).strip("-")


def _load_json(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _reference_name(row: Mapping[str, Any]) -> str | None:
    capture = row.get("historicalCapture")
    return (
        str(capture["name"])
        if isinstance(capture, Mapping) and isinstance(capture.get("name"), str)
        else None
    )


def _pack_entries(reference_dir: Path) -> dict[str, Mapping[str, Any]]:
    pack = _load_json(reference_dir / "reference-pack.json")
    if pack is None:
        return {}
    values = pack.get("references", pack.get("rows", pack))
    if isinstance(values, Mapping):
        return {str(key): value for key, value in values.items() if isinstance(value, Mapping)}
    if isinstance(values, list):
        return {
            str(value.get("fixtureId", value.get("name"))): value
            for value in values
            if isinstance(value, Mapping) and isinstance(value.get("name", value.get("fixtureId")), str)
        }
    return {}


def _write_contact_sheet(path: Path, reports: Sequence[Mapping[str, Any]]) -> None:
    rows: list[str] = []
    for report in reports:
        title = html.escape(str(report.get("fixtureId", "unknown")))
        cells: list[str] = []
        for key in ("reference", "actual", "difference", "overlay"):
            value = scrub(report.get(key))
            if isinstance(value, str) and value:
                src = html.escape(value, quote=True)
                cells.append(f'<figure><figcaption>{html.escape(key)}</figcaption><img src="{src}"></figure>')
        reason = html.escape(str(scrub(report.get("reason", report.get("status", "")))))
        cells_html = "".join(cells)
        rows.append(f"<article><h2>{title}</h2><p>{reason}</p><div>{cells_html}</div></article>")
    body = "\n".join(rows)
    path.write_text(
        '<!doctype html><meta charset="utf-8"><title>SimCord visual comparison</title>'
        "<style>body{font:14px system-ui;background:#222;color:#eee}article{margin:1em 0;padding:1em;background:#333}"
        "article>div{display:flex;gap:1em;flex-wrap:wrap}figure{margin:0}img{max-width:45vw;max-height:30vh}</style>"
        + body,
        encoding="utf-8",
    )


def batch_compare(
    manifest_path: Path,
    reference_dir: Path,
    actual_dir: Path,
    output_dir: Path,
    *,
    threshold: int = 0,
    required: bool = False,
    family: str | None = None,
    fixture_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Compare every selected ledger row and return ``(report, exit_code)``."""
    try:
        manifest = load_coverage_manifest(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "reasons": [str(exc)], "rows": []}, 2
    rows = [row for row in manifest["rows"] if isinstance(row, Mapping)]
    selected = [
        row
        for row in rows
        if (family is None or row.get("family") == family)
        and (fixture_id is None or row.get("id") == fixture_id)
    ]
    if not selected:
        return {"status": "invalid", "reasons": ["selection matched no coverage rows"], "rows": []}, 2
    output_dir.mkdir(parents=True, exist_ok=True)
    pack_entries = _pack_entries(reference_dir)
    reports: list[dict[str, Any]] = []
    invalid = False
    failed = False
    expected_names: set[str] = set()
    for row in selected:
        rid = str(row["id"])
        report: dict[str, Any] = {"fixtureId": rid, "family": row.get("family"), "status": "blocked"}
        name = _reference_name(row)
        if name:
            expected_names.add(name)
        if row.get("comparisonStatus") == "not_comparable":
            report["status"] = "not_comparable"
            report["reason"] = str(
                row.get("reason") or "ledger marks this whole-window comparison not comparable"
            )
            reports.append(report)
            continue
        if row.get("referenceStatus") != "available":
            report["reason"] = str(row.get("reason") or "reference is blocked")
            if required:
                invalid = True
            reports.append(report)
            continue
        if not name:
            report["reason"] = "available row has no historical capture name"
            invalid = True
            reports.append(report)
            continue
        reference_path = reference_dir / name
        actual_path = actual_dir / name
        report.update({"reference": str(reference_path), "actual": str(actual_path)})
        if not reference_path.is_file() or not actual_path.is_file():
            report["reason"] = "missing reference or actual PNG"
            invalid = True
            reports.append(report)
            continue
        metadata = (
            _load_json(actual_path.with_suffix(".json")) or pack_entries.get(rid) or pack_entries.get(name)
        )
        if metadata is not None and metadata.get("fixtureId") not in {None, rid}:
            report["reason"] = "actual metadata fixture ID does not match ledger row"
            invalid = True
            reports.append(report)
            continue
        if metadata is not None and _contains_key(metadata, "capability"):
            report["reason"] = "actual metadata contains an unsanitized capability"
            invalid = True
            reports.append(report)
            continue
        if metadata is None and required:
            report["reason"] = "missing capture metadata"
            invalid = True
            reports.append(report)
            continue
        try:
            result = compare(reference_path, actual_path, output_dir / _safe_stem(rid), threshold, metadata)
        except (OSError, ValueError) as exc:
            result = {"status": "invalid", "reason": str(exc)}
        report.update(result)
        if result.get("status") in {"invalid", "dimension_mismatch"}:
            invalid = True
        elif result.get("status") == "fail":
            failed = True
        reports.append(report)
    actual_names = {path.name for path in actual_dir.glob("*.png")} if actual_dir.is_dir() else set()
    extras = sorted(actual_names - expected_names)
    if extras:
        invalid = True
        reports.append(
            {
                "fixtureId": "<unexpected-actual>",
                "status": "invalid",
                "reason": f"unexpected actual PNGs: {extras}",
            }
        )
    counts = {
        status: sum(report.get("status") == status for report in reports)
        for status in ("pass", "fail", "blocked", "not_comparable", "dimension_mismatch", "invalid")
    }
    invalid = invalid or bool(counts["blocked"] or counts["not_comparable"])
    status = "invalid" if invalid else "fail" if failed else "pass"
    report = scrub(
        {
            "status": status,
            "exitCode": 2 if invalid else 1 if status == "fail" else 0,
            "manifest": str(manifest_path),
            "referenceDir": str(reference_dir),
            "actualDir": str(actual_dir),
            "required": required,
            "counts": counts,
            "rows": reports,
        }
    )
    (output_dir / "comparison.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_contact_sheet(output_dir / "contact-sheet.html", reports)
    return report, int(report["exitCode"])


compare_batch = batch_compare


def _check() -> None:
    first = Image.new("RGB", (2, 2), "black")
    second = Image.new("RGB", (2, 2), "black")
    assert compare_in_memory(first, second, threshold=0) == (0, 0)
    second.putpixel((1, 1), (10, 20, 30))
    assert compare_in_memory(first, second, threshold=0) == (1, 30)
    assert compare_in_memory(first, second, threshold=30) == (0, 30)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare local SimCord captures without resizing them.")
    parser.add_argument("reference", type=Path, nargs="?")
    parser.add_argument("actual", type=Path, nargs="?")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--actual-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(".discord-reference-captures/diff"))
    parser.add_argument("--threshold", type=int, default=0)
    parser.add_argument("--required", action="store_true")
    parser.add_argument("--family")
    parser.add_argument("--fixture")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--check-manifest", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        _check()
        print("Visual comparison helper check passed.")
        return 0
    if args.check_manifest is not None:
        summary = validate_coverage_manifest(load_coverage_manifest(args.check_manifest))
        print(json.dumps(summary, indent=2))
        return 0
    if args.manifest is not None or args.reference_dir is not None or args.actual_dir is not None:
        if args.manifest is None or args.reference_dir is None or args.actual_dir is None:
            parser.error("--manifest, --reference-dir and --actual-dir are required together")
        report, code = batch_compare(
            args.manifest,
            args.reference_dir,
            args.actual_dir,
            args.output_dir,
            threshold=args.threshold,
            required=args.required,
            family=args.family,
            fixture_id=args.fixture,
        )
        print(json.dumps(report, indent=2))
        return code
    if args.reference is None or args.actual is None:
        parser.error("reference and actual are required unless batch or --check is used")
    report = compare(args.reference, args.actual, args.output_dir, args.threshold)
    print(json.dumps(report, indent=2))
    return (
        2
        if report.get("status") in {"invalid", "dimension_mismatch"}
        else 1
        if report.get("status") == "fail"
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
