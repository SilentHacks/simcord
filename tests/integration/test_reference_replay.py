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


def _replay_input(replay, root, created_at):
    import json

    catalog = replay._catalog()
    payload = catalog.gallery_payload("REF-20-BUTTONS")
    normalized = {
        "content": payload["content"],
        "components": payload["view"].to_components(),
        "flags": 0,
        "attachments": {},
    }
    fixture = {
        "referenceId": "REF-20-BUTTONS",
        "normalizedPayload": normalized,
        "normalizedPayloadHash": replay._canonical_hash(normalized),
        "messageId": "100",
        "previousMessageId": "99",
        "channelId": "200",
        "viewerMention": "<@300>",
        "createdAt": created_at,
        "author": {"id": "400", "username": "source-bot", "displayName": "Source Bot"},
    }
    sidecar = {
        "name": "capture.png",
        "state": "primary-focus",
        "fixture": fixture,
        "profile": {
            "observed": {
                "width": 900,
                "height": 700,
                "deviceScaleFactor": 1,
                "reducedMotion": True,
                "locale": "en-GB",
                "timezone": "UTC",
            }
        },
    }
    # Specification loading checks existence; no screenshot/PNG validity is claimed here.
    (root / "capture.png").touch()
    path = root / "capture.json"
    path.write_text(json.dumps(sidecar))
    scene = {"viewerLabel": "Observed Viewer", "presentation": {"width": 600, "height": 400}}
    return catalog, payload, path, sidecar, scene


async def test_replay_builds_only_known_history_and_keeps_source_mapping(env, tmp_path):
    from datetime import datetime, timedelta

    replay = _load_replay()
    source_time = (
        datetime.fromisoformat(env.backend.now_iso()).replace(microsecond=0) + timedelta(seconds=100)
    ).isoformat()
    catalog, payload, path, sidecar, scene = _replay_input(replay, tmp_path, source_time)
    try:
        spec = replay._replay_spec(path, tmp_path, [sidecar], scene)
        world = await replay._build_world(env, spec, scene, payload, catalog, tmp_path)
        assert spec["missingPredecessor"] is True
        assert world["predecessorCount"] == 0
        assert world["missingContext"] == 0
        assert set(world["idMapping"]) == {"100", "200", "300", "400"}
        assert world["idMapping"]["100"] == str(world["message"].id)
        assert world["message"].created_at == datetime.fromisoformat(source_time)
        history = [message async for message in env.bot.get_channel(world["channel"].id).history(limit=10)]
        assert len(history) == 1
        assert spec["fixture"]["messageId"] == "100"
        assert spec["fixture"]["createdAt"] == source_time
        assert replay._scrub({"originalToLocalIds": world["idMapping"]}) == {
            "originalToLocalIds": world["idMapping"]
        }
    finally:
        catalog.close_payload(payload)


def test_replay_spec_blocks_unobserved_context_instead_of_substituting(tmp_path):
    import json

    import pytest

    replay = _load_replay()
    catalog, payload, path, sidecar, scene = _replay_input(replay, tmp_path, "2026-01-01T00:00:00+00:00")
    try:
        for key in ("viewerLabel", "presentation"):
            incomplete = {name: value for name, value in scene.items() if name != key}
            with pytest.raises(ValueError):
                replay._replay_spec(path, tmp_path, [sidecar], incomplete)
        sidecar["fixture"]["createdAt"] = "2026-01-01T00:00:00"
        path.write_text(json.dumps(sidecar))
        with pytest.raises(ValueError, match="timezone"):
            replay._replay_spec(path, tmp_path, [sidecar], scene)
    finally:
        catalog.close_payload(payload)


def test_replay_history_uses_instants_and_reports_each_unknown_predecessor():
    replay = _load_replay()
    first = {
        "fixture": {
            "channelId": "channel",
            "messageId": "1",
            "previousMessageId": None,
            "createdAt": "2026-01-01T10:00:00+02:00",
        }
    }
    second = {
        "fixture": {
            "channelId": "channel",
            "messageId": "3",
            "previousMessageId": "2",
            "createdAt": "2026-01-01T09:00:00+00:00",
        }
    }
    chain, missing = replay._history_chain(second, [second, first])
    assert chain == [first, second]
    assert missing is True
    chain, missing = replay._history_chain(first, [first])
    assert chain == [first]
    assert missing is False
