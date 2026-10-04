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


ARCHIVE_SHA256 = "fcf4ad0fa3bb6afae4755f09dbd3f45087e0a29878922164fd5615c4b561786a"
VISUAL = {
    "ref-10-legacy-embed-idle.png": (
        "full legacy embed, content and attachment image surface",
        "useful-full-surface",
        "No hover/focus or narrow-width evidence; private author identity ignored.",
    ),
    "ref-20-buttons-idle.png": (
        "button row including disabled and emoji button",
        "useful-full-surface",
        "Idle only; primary premium SKU omitted.",
    ),
    "ref-20-buttons-primary-hover.png": (
        "buttons with native message hover toolbar partially at crop edge",
        "useful-full-surface-with-context",
        "Hover appearance visible, but native toolbar is incidental context; no callback evidence.",
    ),
    "ref-20-buttons-primary-focus.png": (
        "buttons with cyan primary keyboard-focus ring and partial toolbar context",
        "useful-full-surface-with-context",
        "Focus visible; no callback evidence.",
    ),
    "ref-30-string-select-idle.png": (
        "closed selected and disabled string-select controls",
        "useful-full-surface",
        "No closed-unselected or clear state.",
    ),
    "ref-30-string-select-open-recapture.png": (
        "open single-select menu with options and selected row",
        "useful-full-surface",
        "No hover/focus/callback evidence.",
    ),
    "ref-30-string-select-two-selected.png": (
        "open multi-select with two selected rows and disabled option",
        "useful-full-surface",
        "No callback receipt.",
    ),
    "ref-30-string-select-escape-outcome.png": (
        "closed control showing Moon Base after Escape",
        "useful-full-surface",
        "Visible value does not establish Escape callback or timing.",
    ),
    "ref-31-entity-selects-user-open.png": (
        "user menu, top clipped to its visible candidate area",
        "useful-menu-section",
        "Only visible candidates; no whole-list or callback claim.",
    ),
    "ref-31-entity-selects-user-selected.png": (
        "selected user control",
        "useful-full-surface",
        "Selection appearance only, not callback receipt.",
    ),
    "ref-31-entity-selects-role-open.png": (
        "open role menu with visible candidate rows",
        "useful-menu-section",
        "Viewport/list may scroll; no whole-list or callback claim.",
    ),
    "ref-31-entity-selects-mentionable-open.png": (
        "open mentionable menu with user and role candidates; extra REF-40 fixture context below",
        "useful-menu-section-with-context",
        "Variable-height menu and unrelated lower fixture context; no whole-list or callback claim.",
    ),
    "ref-31-entity-selects-channel-open-visible-section.png": (
        "open channel menu with visible candidates; extra REF-40 fixture context below",
        "useful-menu-section-with-context",
        "Variable-height/scrollable list; no whole-list or callback claim.",
    ),
    "ref-40-v2-layout-media-idle-top-expedition-container.png": (
        "top Expedition status container section",
        "useful-section",
        "Does not show full V2 message or other named sections.",
    ),
    "ref-40-v2-layout-media-idle-media-two-image-gallery.png": (
        "two-image gallery with second orange image covered by SPOILER",
        "useful-section",
        "Gallery section only, not entire V2 message.",
    ),
    "ref-40-v2-layout-media-spoiler-revealed-orange-gallery-item.png": (
        "gallery crop with second orange item revealed",
        "useful-section",
        "Only second orange gallery item; not whole message. Reveal observation does not establish callback receipt.",
    ),
}


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


def validate(archive: Path, reference: Path) -> dict:
    errors: list[str] = []
    archive_bytes = archive.read_bytes()
    archive_hash = digest(archive_bytes)
    if archive_hash != ARCHIVE_SHA256:
        errors.append(f"Archive SHA-256 differs from handoff fingerprint: {archive_hash}")
    pack_path = reference / "reference-pack.json"
    if not inside(reference, pack_path):
        raise ValueError("Reference manifest resolves outside the reference root")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    references = pack["references"]
    safe_names = {
        record.get("name") for record in references if plain_filename(record.get("name"), suffix=".png")
    }
    if len(safe_names) != sum(plain_filename(record.get("name"), suffix=".png") for record in references):
        errors.append("Duplicate capture names in reference pack")
    for record in references:
        if not plain_filename(record.get("name"), suffix=".png"):
            errors.append("Capture names must be plain PNG filenames")
    batches = pack.get("sourceCaptureBatches", {})
    if set(batches) != safe_names:
        errors.append(
            f"Source-batch inventory mismatch: missing={sorted(safe_names - set(batches))}, extra={sorted(set(batches) - safe_names)}"
        )

    expected = {"reference-pack.json"}
    results = []
    with zipfile.ZipFile(archive) as zf:
        zip_names = {name for name in zf.namelist() if not name.endswith("/")}
        for record in references:
            name = record.get("name")
            if not plain_filename(name, suffix=".png") or name not in safe_names:
                continue
            assets = record.get("assetHashes", {})
            if not isinstance(assets, dict) or any(not plain_filename(asset) for asset in assets):
                errors.append(f"Asset names must be plain filenames: {name}")
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
            sidecar_bytes = sidecar_path.read_bytes()
            sidecar = json.loads(sidecar_bytes)
            exact_equivalence = sidecar == record
            if not exact_equivalence:
                errors.append(f"Complete sidecar/manifest record mismatch: {name}")
            fixture = sidecar.get("fixture", {})
            if digest(png) != record.get("sha256"):
                errors.append(f"PNG SHA-256 mismatch: {name}")
            if sidecar.get("normalizedPayloadHash") != record.get("normalizedPayloadHash") or canonical_hash(
                fixture.get("normalizedPayload")
            ) != record.get("normalizedPayloadHash"):
                errors.append(f"Normalized payload hash mismatch: {name}")
            for asset, expected_hash in assets.items():
                asset_path = reference / "assets" / asset
                if not inside(reference, asset_path):
                    errors.append(f"Asset path resolves outside the reference root: assets/{asset}")
                    continue
                if not asset_path.is_file() or digest(asset_path.read_bytes()) != expected_hash:
                    errors.append(f"Missing or hash-mismatched asset: assets/{asset} ({name})")
            image_size = png_size(png)
            crop = record.get("crop", {})
            profile = record.get("profile", {})
            viewport = profile.get("observed", {})
            bounds = {
                "imageMatchesCrop": image_size == [crop.get("width"), crop.get("height")],
                "cropWithinViewport": (
                    crop.get("x", -1) >= 0
                    and crop.get("y", -1) >= 0
                    and crop.get("width", 0) > 0
                    and crop.get("height", 0) > 0
                    and crop.get("x", 0) + crop.get("width", 0) <= viewport.get("width", 0)
                    and crop.get("y", 0) + crop.get("height", 0) <= viewport.get("height", 0)
                ),
            }
            if not all(bounds.values()):
                errors.append(
                    f"Image dimensions/crop bounds invalid: {name}: {bounds}, image={image_size}, crop={crop}, viewport={viewport}"
                )
            description, classification, gap = VISUAL.get(
                name, ("not visually classified", "unreviewed", "Visual review required.")
            )
            results.append(
                {
                    "name": name,
                    "sha256": digest(png),
                    "sidecarSha256": digest(sidecar_bytes),
                    "sourceBatch": batches.get(name),
                    "dimensions": image_size,
                    "crop": crop,
                    "bounds": bounds,
                    "normalizedPayloadHash": record.get("normalizedPayloadHash"),
                    "assetHashes": assets,
                    "sidecarManifestEquivalent": exact_equivalence,
                    "visibleState": description,
                    "classification": classification,
                    "gap": gap,
                    "evidenceStatus": record.get("evidenceStatus"),
                    "calibrated": record.get("calibrated"),
                }
            )
        expected.update(
            {
                f"assets/{asset}"
                for r in references
                if isinstance(r.get("assetHashes"), dict)
                for asset in r["assetHashes"]
                if plain_filename(asset)
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
            if not source.is_file() or digest(zf.read(name)) != digest(source.read_bytes()):
                errors.append(f"Archive/extracted byte mismatch: {name}")
    batch_counts = {batch: list(batches.values()).count(batch) for batch in sorted(set(batches.values()))}
    excluded = [
        {"batch": x.get("batch"), "image": x.get("image"), "reason": x.get("reason")}
        for x in pack.get("excludedCaptures", [])
    ]
    supplemental = {
        batch: {
            "reviewed": len(data.get("captures", [])),
            "captureNames": [c.get("image") for c in data.get("captures", [])],
        }
        for batch, data in pack.get("supplementalReviews", {}).items()
    }
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "archive": {
            "path": str(archive),
            "sha256": archive_hash,
            "fingerprintMatchesHandoff": archive_hash == ARCHIVE_SHA256,
            "fileCount": len(expected),
            "captureCount": len(references),
            "inventory": sorted(expected),
        },
        "manifestCaptureCount": len(references),
        "inventoryReconciliation": "actual handoff ZIP, manifest, and stated image/sidecar inventory each contain 16 captures",
        "captureBatches": batch_counts,
        "excludedCapturesSeparate": excluded,
        "supplementalReviewsSeparate": supplemental,
        "captures": results,
        "modalInvestigation": {
            "fixtureDefect": "TextModal feedback label exceeded the 45-character Label limit",
            "offlineBefore": "HTTP 400 / 50035; modal interaction unacknowledged",
            "fix": "Shorten label; retain optional minimum length in description",
            "offlineAfter": "Actual actor dispatch opens REF-51 and its text fields",
            "liveCause": "unknown without official bot error log",
            "reference": "https://docs.discord.com/developers/components/reference#label",
        },
        "limits": [
            "Visual acceptance is limited to visible pixels and crop; no callback receipt or server-side value is inferred.",
            "All evidence remains observed-unreviewed and uncalibrated.",
            "Private usernames, IDs, URLs, channel names and labels are intentionally omitted from this report.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Original supplied calibration ZIP")
    parser.add_argument(
        "--reference", type=Path, default=Path(".discord-reference-captures/calibration-batch-01/reference")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".discord-reference-captures/calibration-batch-01/acceptance-report.json"),
    )
    args = parser.parse_args()
    report = validate(args.archive, args.reference)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    text = [
        f"Calibration pack: {report['status']}",
        f"Archive SHA-256: {report['archive']['sha256']}",
        f"Inventory: {report['manifestCaptureCount']} captures; batches {report['captureBatches']}",
        "Inventory note: archive, manifest, and stated 16-PNG/16-sidecar inventory agree.",
        "Re-run command: python3 scripts/validate_discord_reference_pack.py --archive /path/to/calibration-batch-01.zip",
        "Per capture:",
    ]
    for item in report["captures"]:
        text.append(
            f"- {item['name']} [{item['sourceBatch']}; {item['classification']}; {item['dimensions'][0]}x{item['dimensions'][1]}]: {item['visibleState']}. Gap: {item['gap']}"
        )
    text += [
        "",
        "Targeted follow-up:",
        "- Modal timeout: start `DISCORD_GUILD_ID=<private-test-guild-id> python3 scripts/discord_reference_bot.py --prompt-token` in a terminal, enter the token only at the hidden prompt, and leave the process running. In Discord, invoke `/visual_references` to post a fresh gallery (views from a previous process are not registered after restart), then click Open text modal once. Record click time, bot stdout from startup through click, process status, and a screenshot of any user-visible error.",
        "- If opened: capture empty form, focus, valid/invalid values and field validation; submit each state and record server callback receipt plus submitted values with private values redacted. Retain correlation timestamp only.",
        "- If no modal: preserve bot-side interaction errors/redacted stack and current-process/gateway status; provide a screenshot of the user's visible error/acknowledgement. Do not inspect client network, private APIs or private interaction payloads. Offline success is not a live-cause diagnosis.",
        "- Only if the comparison shows a missing menu state: recapture that named menu at current rendered bounds, including the visible viewport and scrollbar at top and after scrolling. Record bounds immediately before capture; don't reuse old menu-height measurements.",
        "- Only if the comparison shows missing V2 evidence: capture the specific missing named section; do not request a broad recapture or stitch/normalize sections.",
        "",
        "Modal investigation: the producing TextModal feedback label exceeded Discord's 45-character Label limit. The real offline actor reproduced HTTP 400 / 50035 before a modal acknowledgement. Shortening the label to Optional feedback restores the modal response; the three-character optional-input constraint remains in its description. This is a confirmed fixture defect and a plausible live-timeout cause, not proof of the live cause without the bot log. Restart with updated code and manually post a fresh gallery before recapturing the four blocked text-modal states.",
        "Offline scenario: run python3 scripts/check_reference_modal_offline.py. It starts local SimCord, sends the production REF-50 gallery payload, clicks the actual Open text modal button through the SimCord actor API and asserts InteractionResult acknowledgment, modal title and actual field component IDs. This proves offline callback dispatch only—not Discord gateway delivery, client behavior, callback submission/server values or live timeout cause. Also run python3 scripts/discord_reference_bot.py --check.",
        "",
        "All evidence remains observed-unreviewed; visible appearance is not callback evidence.",
    ]
    args.output.with_suffix(".txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(
        f"{report['status']}: {report['manifestCaptureCount']} captures; wrote {args.output} and {args.output.with_suffix('.txt')}"
    )
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
