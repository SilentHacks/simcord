import importlib.util
from pathlib import Path


def _load_replay():
    path = Path(__file__).resolve().parents[2] / "scripts" / "replay_visual_reference.py"
    spec = importlib.util.spec_from_file_location("reference_replay_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
