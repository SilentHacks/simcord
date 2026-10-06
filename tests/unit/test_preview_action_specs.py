import json
import re
from importlib import resources

from simcord.preview._actions import ACTION_SPECS


def test_action_specs_match_protocol_schema_and_browser() -> None:
    package = resources.files("simcord.preview")
    schema = json.loads(package.joinpath("protocol.schema.json").read_text())
    action_request = schema["$defs"]["actionRequest"]

    assert set(schema["$defs"]["actionKind"]["enum"]) == set(ACTION_SPECS)

    revision_clauses = [
        clause
        for clause in action_request["allOf"]
        if "published_revision" in clause["then"].get("required", [])
    ]
    assert len(revision_clauses) == 1
    assert set(revision_clauses[0]["if"]["properties"]["kind"]["enum"]) == {
        kind for kind, spec in ACTION_SPECS.items() if spec.revision_bound
    }

    target_clauses = [
        clause for clause in action_request["allOf"] if "target_id" in clause["then"].get("required", [])
    ]
    assert len(target_clauses) == 1
    assert set(target_clauses[0]["if"]["properties"]["kind"]["enum"]) == {
        kind for kind, spec in ACTION_SPECS.items() if spec.requires_target
    }

    branch_kinds = []
    for branch in action_request["oneOf"]:
        kind_schema = branch["properties"]["kind"]
        if "const" in kind_schema:
            branch_kinds.append(kind_schema["const"])
        else:
            branch_kinds.extend(kind_schema["enum"])
    assert set(branch_kinds) == set(ACTION_SPECS)
    assert len(branch_kinds) == len(ACTION_SPECS)
    assert all(branch_kinds.count(kind) == 1 for kind in ACTION_SPECS)

    app_js = package.joinpath("static", "app.js").read_text()
    page_action = re.search(
        r"function\s+pageAction\s*\(\s*kind\s*\)\s*\{\s*return\s*\[([^\]]*)\]\.includes\(kind\);\s*\}",
        app_js,
    )
    assert page_action is not None
    assert set(re.findall(r"[\"']([^\"']+)[\"']", page_action.group(1))) == {
        kind for kind, spec in ACTION_SPECS.items() if spec.page_intent
    }
