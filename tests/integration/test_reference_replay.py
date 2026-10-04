import importlib.util
from pathlib import Path


def _load_replay():
    path = Path(__file__).resolve().parents[2] / "scripts" / "replay_visual_reference.py"
    spec = importlib.util.spec_from_file_location("reference_replay_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_validator():
    path = Path(__file__).resolve().parents[2] / "scripts" / "validate_discord_reference_pack.py"
    spec = importlib.util.spec_from_file_location("reference_pack_validator_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_single_capture_export_pack_validates_without_historical_fingerprint(tmp_path):
    import hashlib
    import json
    import struct
    import zipfile
    import zlib

    validator = _load_validator()
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1, 1)
    png += bytes((8, 2, 0, 0, 0))
    png += struct.pack(">I", zlib.crc32(png[12:29]) & 0xFFFFFFFF)
    scanline = zlib.compress(b"\x00\x00\x00\x00")

    def chunk(name, data):
        checksum = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", checksum)

    png += chunk(b"IDAT", scanline) + chunk(b"IEND", b"")
    normalized = {"content": "captured", "attachments": {}, "components": []}
    payload_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    record = {
        "fixtureId": "local.ref-30-string-select.open",
        "name": "capture.png",
        "state": "open",
        "sha256": hashlib.sha256(png).hexdigest(),
        "assetHashes": {},
        "assetFiles": [],
        "normalizedPayloadHash": payload_hash,
        "fixture": {"normalizedPayload": normalized, "normalizedPayloadHash": payload_hash},
        "crop": {"x": 0, "y": 0, "width": 1, "height": 1},
        "profile": {"observed": {"width": 1, "height": 1}},
        "evidenceStatus": "observed-unreviewed",
        "calibrated": False,
    }
    pack = {"schemaVersion": 1, "evidenceStatus": "observed-unreviewed", "references": [record]}
    root = tmp_path / "reference"
    root.mkdir()
    manifest = json.dumps(pack, ensure_ascii=False, indent=2).encode() + b"\n"
    sidecar = json.dumps(record, ensure_ascii=False, indent=2).encode() + b"\n"
    (root / "reference-pack.json").write_bytes(manifest)
    (root / "capture.png").write_bytes(png)
    (root / "capture.json").write_bytes(sidecar)
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("reference-pack.json", manifest)
        output.writestr("capture.png", png)
        output.writestr("capture.json", sidecar)

    report = validator.validate(archive, root)
    assert report["status"] == "PASS"
    assert report["manifestCaptureCount"] == 1
    assert report["inventoryReconciliation"]["captureSidecarCount"] == 1
    changed_png = bytearray(png)
    changed_png[-1] ^= 1
    (root / "capture.png").write_bytes(changed_png)
    assert validator.validate(archive, root)["status"] == "FAIL"
    (root / "capture.json").unlink()
    assert validator.validate(archive, root)["status"] == "FAIL"


def test_replay_refuses_payload_when_recorded_hash_is_stale(tmp_path):
    replay = _load_replay()

    fixture = {
        "referenceId": "REF-30-STRING-SELECT",
        "normalizedPayload": {"content": "captured exact content", "components": []},
        "normalizedPayloadHash": "0" * 64,
    }
    catalog = replay._catalog()
    payload, _reason = replay._payload_matches(fixture, catalog, tmp_path)

    assert payload == {}
