"""Compare a local SimCord capture with a local Discord reference.

This is a development aid only. It does not change CI, package reference
images, or resize either input. Keep all outputs in the ignored local capture
workspace.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path

from PIL import Image, ImageChops

_REFERENCE_STATUSES = {"missing", "available", "blocked"}
_IMPLEMENTATION_STATUSES = {"missing", "partial", "implemented"}
_COMPARISON_STATUSES = {"unrun", "fail", "pass", "not_comparable"}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
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
    """Load and integrity-check a committed coverage ledger."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("coverage manifest root must be an object")
    validate_coverage_manifest(value)
    return value


def compare(
    reference_path: Path, actual_path: Path, output_dir: Path, threshold: int = 0
) -> dict[str, object]:
    if threshold < 0 or threshold > 255:
        raise ValueError("threshold must be between 0 and 255")
    with Image.open(reference_path) as reference_image, Image.open(actual_path) as actual_image:
        reference = reference_image.convert("RGB")
        actual = actual_image.convert("RGB")
    if reference.size != actual.size:
        raise ValueError(f"image sizes differ: reference={reference.size}, actual={actual.size}")
    difference = ImageChops.difference(reference, actual)
    changed, maximum, mean = diff_stats(difference, threshold)
    output_dir.mkdir(parents=True, exist_ok=True)
    difference_path = output_dir / "difference.png"
    overlay_path = output_dir / "overlay.png"
    report_path = output_dir / "report.json"
    difference.save(difference_path)
    Image.blend(reference, actual, 0.5).save(overlay_path)
    total_pixels = reference.width * reference.height
    report = {
        "reference": str(reference_path),
        "actual": str(actual_path),
        "size": list(reference.size),
        "threshold": threshold,
        "changedPixels": changed,
        "totalPixels": total_pixels,
        "changedRatio": changed / max(total_pixels, 1),
        "maximumChannelDifference": maximum,
        "meanChannelDifference": mean,
        "difference": str(difference_path),
        "overlay": str(overlay_path),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def diff_stats(difference: Image.Image, threshold: int) -> tuple[int, int, float]:
    raw = difference.tobytes()
    return (
        sum(max(raw[index : index + 3]) > threshold for index in range(0, len(raw), 3)),
        max(raw, default=0),
        sum(raw) / max(len(raw), 1),
    )


def _check() -> None:
    first = Image.new("RGB", (2, 2), "black")
    second = Image.new("RGB", (2, 2), "black")
    assert compare_in_memory(first, second, threshold=0) == (0, 0)
    second.putpixel((1, 1), (10, 20, 30))
    assert compare_in_memory(first, second, threshold=0) == (1, 30)
    assert compare_in_memory(first, second, threshold=30) == (0, 30)


def compare_in_memory(reference: Image.Image, actual: Image.Image, threshold: int) -> tuple[int, int]:
    if reference.size != actual.size:
        raise ValueError("image sizes differ")
    difference = ImageChops.difference(reference.convert("RGB"), actual.convert("RGB"))
    changed, maximum, _ = diff_stats(difference, threshold)
    return changed, maximum


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two same-sized local PNGs without resizing them.")
    parser.add_argument("reference", type=Path, nargs="?")
    parser.add_argument("actual", type=Path, nargs="?")
    parser.add_argument("--output-dir", type=Path, default=Path(".discord-reference-captures/diff"))
    parser.add_argument("--threshold", type=int, default=0)
    parser.add_argument("--check", action="store_true", help="run the in-memory helper check")
    parser.add_argument("--check-manifest", type=Path, help="validate a commit-01 coverage ledger")
    args = parser.parse_args()
    if args.check:
        _check()
        print("Visual comparison helper check passed.")
        return
    if args.check_manifest is not None:
        summary = validate_coverage_manifest(load_coverage_manifest(args.check_manifest))
        print(json.dumps(summary, indent=2))
        return
    if args.reference is None or args.actual is None:
        parser.error("reference and actual are required unless --check or --check-manifest is used")
    print(json.dumps(compare(args.reference, args.actual, args.output_dir, args.threshold), indent=2))


if __name__ == "__main__":
    main()
