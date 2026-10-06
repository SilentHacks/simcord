"""Catalog-owned diagnostics safe for authorized preview wire data."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Literal

_CATALOG: dict[str, tuple[str, str, bool, str, str]] = {
    "bad-envelope": (
        "protocol",
        "error",
        True,
        "The action envelope is invalid.",
        "Reload the preview and retry the action.",
    ),
    "unsupported-protocol": (
        "protocol",
        "error",
        True,
        "This action uses an unsupported preview protocol.",
        "Reload with a protocol 3 consumer.",
    ),
    "stale-sequence": (
        "protocol",
        "warning",
        True,
        "The action sequence is stale.",
        "Use the current page action sequence.",
    ),
    "sequence-gap": (
        "protocol",
        "warning",
        True,
        "The action sequence has a gap.",
        "Use the next expected page action sequence.",
    ),
    "conflicting-request": (
        "protocol",
        "error",
        True,
        "This request conflicts with an admitted action.",
        "Do not reuse a request ID with different content.",
    ),
    "busy": (
        "action",
        "warning",
        True,
        "Another preview action is in progress.",
        "Wait for the current action to settle.",
    ),
    "stale-context": (
        "authorization",
        "warning",
        True,
        "The preview page generation is stale.",
        "Use the current page state.",
    ),
    "stale-generation": (
        "authorization",
        "warning",
        True,
        "The bot generation is stale.",
        "Reload the preview after the bot restarts.",
    ),
    "stale-revision": (
        "protocol",
        "warning",
        True,
        "The published preview revision is stale.",
        "Read the current page before acting.",
    ),
    "unknown-kind": (
        "protocol",
        "error",
        True,
        "This preview action is unavailable.",
        "Use a supported protocol 3 action.",
    ),
    "validation-failed": (
        "action",
        "warning",
        True,
        "The requested action is unavailable for this page.",
        "Check the current authorized control and its values.",
    ),
    "query-invalid": (
        "navigation",
        "warning",
        True,
        "The navigation query is invalid.",
        "Use plain text of at most 128 Unicode code points.",
    ),
    "stale-cursor": (
        "navigation",
        "warning",
        True,
        "This result cursor is stale or unavailable.",
        "Start a new query from the current page.",
    ),
    "control-unavailable": (
        "authorization",
        "warning",
        True,
        "This control is unavailable.",
        "Use a control in the current authorized page.",
    ),
    "context-unavailable": (
        "authorization",
        "warning",
        True,
        "This preview context is unavailable.",
        "Open a current authorized preview page.",
    ),
    "asset-unavailable": (
        "asset",
        "warning",
        True,
        "This preview asset is unavailable.",
        "Reload the page and check its current authorization.",
    ),
    "commands-unsynced": (
        "command",
        "warning",
        True,
        "Application commands in the command tree have not been synced to SimCord.",
        "Call `await bot.tree.sync()`.",
    ),
    "command-unavailable": (
        "command",
        "warning",
        True,
        "This command is unavailable in the current page.",
        "Refresh the command catalog and choose a command visible to this viewer.",
    ),
    "command-changed": (
        "command",
        "warning",
        True,
        "This command schema changed after it was selected.",
        "Refresh the command catalog and review the current options.",
    ),
    "command-option-invalid": (
        "command",
        "warning",
        True,
        "A command option does not match its declared type or constraints.",
        "Correct the indicated command option and retry.",
    ),
    "autocomplete-unanswered": (
        "command",
        "warning",
        True,
        "The command did not answer the autocomplete request.",
        "Review the command's autocomplete callback and try again.",
    ),
    "access-denied": (
        "authorization",
        "error",
        False,
        "This page is no longer authorized.",
        "Reopen the preview with an authorized viewer.",
    ),
    "action-callback-error": (
        "action",
        "error",
        False,
        "The bot action failed; inspect the local Env error log for details.",
        "Review the underlying exception in Env.errors.",
    ),
    "action-timeout": (
        "action",
        "error",
        False,
        "The bot action did not settle before its deadline.",
        "Inspect the page before attempting another action.",
    ),
    "action-cancelled": (
        "action",
        "warning",
        False,
        "The bot action was cancelled during shutdown.",
        "Inspect the page before attempting another action.",
    ),
    "message-type-unknown": (
        "content",
        "warning",
        False,
        "This message type is not classified in the preview.",
        "Review the supported message type documentation.",
    ),
    "premium-sku-metadata-missing": (
        "component",
        "warning",
        False,
        "Premium button presentation metadata is unavailable.",
        "Supply an offline SKU name, price, and locale.",
    ),
    "sticker-asset-unavailable": (
        "asset",
        "warning",
        False,
        "A sticker media asset is unavailable offline.",
        "Supply the required sticker asset.",
    ),
    "target-unavailable": (
        "authorization",
        "warning",
        False,
        "The requested target is unavailable.",
        "Choose a currently authorized target.",
    ),
    "font-platform-inspection": (
        "runtime",
        "warning",
        False,
        "Platform font inspection is unavailable.",
        "Use a supported Chromium runtime for font inspection.",
    ),
    "internal-error": (
        "runtime",
        "error",
        False,
        "The preview could not complete this operation.",
        "Inspect the local Env error log for details.",
    ),
}
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
