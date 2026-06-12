"""Error catalog: backend failures that map to real Discord HTTP errors.

The fake transports translate :class:`BackendError` into genuine
``discord.HTTPException`` subclasses carrying authentic Discord JSON error
codes, so bot code that branches on them behaves exactly as in production.
"""

from __future__ import annotations

from typing import Any


class BackendError(Exception):
    def __init__(self, status: int, code: int, message: str) -> None:
        super().__init__(f"{status} (error code: {code}): {message}")
        self.status = status
        self.code = code
        self.message = message

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class SetupError(Exception):
    """The test mis-set-up or mis-drove the virtual world (not a bot bug)."""


# --- the catalog (codes per https://discord.com/developers/docs/topics/opcodes-and-status-codes) ---


def missing_permissions() -> BackendError:
    return BackendError(403, 50013, "Missing Permissions")


def missing_access() -> BackendError:
    return BackendError(403, 50001, "Missing Access")


def unknown_guild() -> BackendError:
    return BackendError(404, 10004, "Unknown Guild")


def unknown_channel() -> BackendError:
    return BackendError(404, 10003, "Unknown Channel")


def unknown_member() -> BackendError:
    return BackendError(404, 10007, "Unknown Member")


def unknown_message() -> BackendError:
    return BackendError(404, 10008, "Unknown Message")


def unknown_role() -> BackendError:
    return BackendError(404, 10011, "Unknown Role")


def unknown_user() -> BackendError:
    return BackendError(404, 10013, "Unknown User")


def unknown_webhook() -> BackendError:
    return BackendError(404, 10015, "Unknown Webhook")


def unknown_ban() -> BackendError:
    return BackendError(404, 10026, "Unknown Ban")


def already_acknowledged() -> BackendError:
    return BackendError(400, 40060, "Interaction has already been acknowledged")


def cannot_edit_other_user() -> BackendError:
    return BackendError(403, 50005, "Cannot edit a message authored by another user")


def invalid_form_body(detail: str) -> BackendError:
    return BackendError(400, 50035, f"Invalid Form Body: {detail}")


def cannot_dm_bot() -> BackendError:
    return BackendError(403, 50007, "Cannot send messages to this user")
