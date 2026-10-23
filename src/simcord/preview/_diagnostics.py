"""Catalog-owned diagnostics safe for authorized preview wire data."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from importlib.resources import files
from typing import Any, Literal

_CATALOG: dict[str, list[Any]] = json.loads(
    files("simcord.preview").joinpath("static/backend-diagnostics.json").read_text(encoding="utf-8")
)
_SNOWFLAKE = re.compile(r"[0-9]{1,20}\Z")


def valid_command_name(value: str) -> bool:
    """Return whether a Discord command or option name has a valid shape."""
    return 1 <= len(value) <= 32 and all(
        character.isalnum() or character in "-_'" or unicodedata.category(character).startswith("M")
        for character in value
    )


def make_diagnostic(
    code: str,
    *,
    state: Literal["current", "recovered"] = "current",
    subject: Mapping[str, str] | None = None,
    correlation: str | None = None,
) -> dict[str, Any]:
    """Create a typed record without serializing caller exception or request text."""
    if code not in _CATALOG:
        code = "internal-error"
    category, severity, complete, message, remediation = _CATALOG[code]
    if state not in {"current", "recovered"}:
        state = "current"
    safe_subject: dict[str, str] | None = None
    if isinstance(subject, Mapping):
        message_id = subject.get("messageId")
        control_key = subject.get("controlKey")
        command_option = subject.get("commandOption")
        if isinstance(message_id, str) and _SNOWFLAKE.fullmatch(message_id):
            safe_subject = {"messageId": message_id}
            if isinstance(control_key, str) and len(control_key) <= 160:
                safe_subject["controlKey"] = control_key
        elif isinstance(command_option, str) and valid_command_name(command_option):
            safe_subject = {"commandOption": command_option}
    seed = json.dumps([code, safe_subject, correlation], sort_keys=True, separators=(",", ":"))
    diagnostic: dict[str, Any] = {
        "id": "d_" + hashlib.sha256(seed.encode()).hexdigest()[:16],
        "code": code,
        "category": category,
        "severity": severity,
        "state": state,
        "complete": complete or state == "recovered",
        "message": message,
        "remediation": remediation,
    }
    if safe_subject is not None:
        diagnostic["subject"] = safe_subject
    if isinstance(correlation, str) and re.fullmatch(r"c_[A-Za-z0-9_-]{8,64}", correlation):
        diagnostic["correlation"] = correlation
    return diagnostic


__all__ = ["make_diagnostic"]
