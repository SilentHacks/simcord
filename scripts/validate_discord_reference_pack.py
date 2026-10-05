"""Validate the local calibration handoff without changing any evidence bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from pathlib import Path


def png_size(data: bytes) -> list[int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        raise ValueError("Invalid PNG signature/header")
    return list(struct.unpack(">II", data[16:24]))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(payload: object) -> str:
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def plain_filename(value: object, *, suffix: str | None = None) -> bool:
    return (
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and "/" not in value
        and "\\" not in value
        and Path(value).name == value
        and (suffix is None or Path(value).suffix == suffix)
    )


def inside(root: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def validate(archive: Path, reference: Path, expected_sha256: str | None = None) -> dict:
    errors: list[str] = []
    archive_hash = digest(archive.read_bytes())
    if expected_sha256 and archive_hash != expected_sha256.lower():
        errors.append(f"Archive SHA-256 differs from supplied expected fingerprint: {archive_hash}")
    pack_path = reference / "reference-pack.json"
    if not inside(reference, pack_path):
        raise ValueError("Reference manifest resolves outside the reference root")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    references = pack.get("references")
    if pack.get("schemaVersion") != 1 or not isinstance(references, list):
        raise ValueError("Expected a version-1 reference pack with a references array")
    safe_names = {
        record.get("name")
        for record in references
        if isinstance(record, dict) and plain_filename(record.get("name"), suffix=".png")
    }
    if len(safe_names) != len(references):
        errors.append("Capture names must be unique plain PNG filenames")

    expected = {"reference-pack.json"}
    results = []
    with zipfile.ZipFile(archive) as zf:
        zip_entries = zf.namelist()
        zip_names = {name for name in zip_entries if not name.endswith("/")}
        if len(zip_names) != len([name for name in zip_entries if not name.endswith("/")]):
            errors.append("Archive contains duplicate file paths")
        for record in references:
            if not isinstance(record, dict):
                errors.append("Reference records must be objects")
                continue
            name = record.get("name")
            if not plain_filename(name, suffix=".png") or name not in safe_names:
                continue
            assets = record.get("assetHashes", {})
            if not isinstance(assets, dict) or any(
                not plain_filename(asset) or not isinstance(value, str) for asset, value in assets.items()
            ):
                errors.append(f"Asset names/hashes are invalid: {name}")
                continue
            expected_asset_files = {f"assets/{asset}" for asset in assets}
            asset_files = record.get("assetFiles", [])
            if (
                not isinstance(asset_files, list)
                or len(asset_files) != len(expected_asset_files)
                or any(not isinstance(path, str) for path in asset_files)
                or set(asset_files) != expected_asset_files
            ):
                errors.append(f"Asset file/hash inventory mismatch: {name}")
                continue
            expected.update((name, Path(name).with_suffix(".json").as_posix(), *expected_asset_files))
            png_path = reference / name
            sidecar_path = reference / Path(name).with_suffix(".json")
            if not inside(reference, png_path) or not inside(reference, sidecar_path):
                errors.append(f"Capture paths resolve outside the reference root: {name}")
                continue
            if not png_path.is_file() or not sidecar_path.is_file():
                errors.append(f"Missing PNG or sidecar: {name}")
                continue
            png = png_path.read_bytes()
            image_size = png_size(png)
            sidecar_bytes = sidecar_path.read_bytes()
            sidecar = json.loads(sidecar_bytes)
            exact_equivalence = sidecar == record
            if not exact_equivalence:
                errors.append(f"Complete sidecar/manifest record mismatch: {name}")
            fixture = sidecar.get("fixture", {})
            if digest(png) != record.get("sha256"):
                errors.append(f"PNG SHA-256 mismatch: {name}")
            if (
                not isinstance(fixture, dict)
                or sidecar.get("normalizedPayloadHash") != record.get("normalizedPayloadHash")
                or canonical_hash(fixture.get("normalizedPayload")) != record.get("normalizedPayloadHash")
            ):
                errors.append(f"Normalized payload hash mismatch: {name}")
            for asset, expected_hash in assets.items():
                asset_path = reference / "assets" / asset
                if not inside(reference, asset_path):
                    errors.append(f"Asset path resolves outside the reference root: assets/{asset}")
                    continue
                if not asset_path.is_file() or digest(asset_path.read_bytes()) != expected_hash:
                    errors.append(f"Missing or hash-mismatched asset: assets/{asset} ({name})")
            crop = record.get("crop")
            crop = crop if isinstance(crop, dict) else {}
            profile = record.get("profile")
            profile = profile if isinstance(profile, dict) else {}
            viewport = profile.get("observed")
            viewport = viewport if isinstance(viewport, dict) else {}
            values = (
                crop.get("x"),
                crop.get("y"),
                crop.get("width"),
                crop.get("height"),
                viewport.get("width"),
                viewport.get("height"),
            )
            valid_numbers = all(type(value) is int for value in values)
            bounds = {
                "imageMatchesCrop": image_size == [crop.get("width"), crop.get("height")],
                "cropWithinViewport": valid_numbers
                and crop["x"] >= 0
                and crop["y"] >= 0
                and crop["width"] > 0
                and crop["height"] > 0
                and crop["x"] + crop["width"] <= viewport["width"]
                and crop["y"] + crop["height"] <= viewport["height"],
            }
            if not all(bounds.values()):
                errors.append(f"Image dimensions/crop bounds invalid: {name}: {bounds}")
            results.append(
                {
                    "name": name,
                    "sha256": digest(png),
                    "sidecarSha256": digest(sidecar_bytes),
                    "sourceBatch": record.get("sourceBatch"),
                    "dimensions": image_size,
                    "crop": crop,
                    "bounds": bounds,
                    "normalizedPayloadHash": record.get("normalizedPayloadHash"),
                    "assetHashes": assets,
                    "sidecarManifestEquivalent": exact_equivalence,
                    "evidenceStatus": "observed-unreviewed",
                    "calibrated": False,
                }
            )
        if zip_names != expected:
            errors.append(
                f"Archive inventory mismatch: missing={sorted(expected - zip_names)}, unexpected={sorted(zip_names - expected)}"
            )
        for name in zip_names & expected:
            source = reference / name
            if not inside(reference, source):
                errors.append(f"Archive source path resolves outside the reference root: {name}")
                continue
            if not source.is_file() or zf.read(name) != source.read_bytes():
                errors.append(f"Archive/extracted byte mismatch: {name}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "archive": {
            "path": str(archive),
            "sha256": archive_hash,
            "expectedSha256": expected_sha256,
            "fingerprintMatchesExpected": expected_sha256 is None or archive_hash == expected_sha256.lower(),
            "fileCount": len(expected),
            "captureCount": len(references),
            "inventory": sorted(expected),
        },
        "manifestCaptureCount": len(references),
        "inventoryReconciliation": {
            "archiveFileCount": len(zip_names),
            "manifestCaptureCount": len(references),
            "captureSidecarCount": sum(
                Path(name).with_suffix(".json").as_posix() in zip_names for name in safe_names
            ),
        },
        "captures": results,
        "limits": [
            "Validation does not review or calibrate captures.",
            "A screenshot or comparison does not establish callback receipt or server-side values.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Original supplied calibration ZIP")
    parser.add_argument("--expected-sha256", help="optional externally supplied archive SHA-256")
    parser.add_argument(
        "--reference", type=Path, default=Path(".discord-reference-captures/calibration-batch-01/reference")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".discord-reference-captures/calibration-batch-01/acceptance-report.json"),
    )
    args = parser.parse_args()
    report = validate(args.archive, args.reference, args.expected_sha256)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    text = [
        f"Reference pack: {report['status']}",
        f"Archive SHA-256: {report['archive']['sha256']}",
        f"Inventory: {report['manifestCaptureCount']} captures; {len(report['archive']['inventory'])} files",
        "Validation does not review or calibrate captures.",
        "Per capture:",
    ]
    for item in report["captures"]:
        text.append(
            f"- {item['name']} [{item['evidenceStatus']}; {item['dimensions'][0]}x{item['dimensions'][1]}]"
        )
    args.output.with_suffix(".txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(
        f"{report['status']}: {report['manifestCaptureCount']} captures; wrote {args.output} and {args.output.with_suffix('.txt')}"
    )
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
