"""Compare a local SimCord capture with a local Discord reference.

This is a development aid only. It does not change CI, package reference
images, or resize either input. Keep all outputs in the ignored local capture
workspace.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops


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
    args = parser.parse_args()
    if args.check:
        _check()
        print("Visual comparison helper check passed.")
        return
    if args.reference is None or args.actual is None:
        parser.error("reference and actual are required unless --check is used")
    print(json.dumps(compare(args.reference, args.actual, args.output_dir, args.threshold), indent=2))


if __name__ == "__main__":
    main()
