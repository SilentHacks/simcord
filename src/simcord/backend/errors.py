"""Error catalog: backend failures that map to real Discord HTTP errors.

The fake transports translate :class:`BackendError` into genuine
``discord.HTTPException`` subclasses carrying authentic Discord JSON error
codes, so bot code that branches on them behaves exactly as in production.
"""

from __future__ import annotations

from typing import Any


class BackendError(Exception):
    def __init__(self, status: int, code: int, message: str, *, errors: dict[str, Any] | None = None) -> None:
        super().__init__(f"{status} (error code: {code}): {message}")
        self.status = status
        self.code = code
        self.message = message
        self.errors = errors

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.errors is not None:
            payload["errors"] = self.errors
        return payload


class SetupError(Exception):
    """The test mis-set-up or mis-drove the virtual world (not a bot bug)."""


class OptionError(SetupError):
    """A slash option value that violates its declared Discord constraints."""

    def __init__(self, code: str, option: str | None, message: str) -> None:
        self.code = code
        self.option = option
        super().__init__(message)


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


def unknown_interaction() -> BackendError:
    return BackendError(404, 10062, "Unknown Interaction")


def unknown_ban() -> BackendError:
    return BackendError(404, 10026, "Unknown Ban")


def already_acknowledged() -> BackendError:
    return BackendError(400, 40060, "Interaction has already been acknowledged")


def cannot_edit_other_user() -> BackendError:
    return BackendError(403, 50005, "Cannot edit a message authored by another user")


def invalid_form_body(detail: str | None = None, *, errors: dict[str, Any] | None = None) -> BackendError:
    message = "Invalid Form Body" if detail is None else f"Invalid Form Body: {detail}"
    return BackendError(400, 50035, message, errors=errors)


def cannot_execute_on_channel_type() -> BackendError:
    return BackendError(400, 50024, "Cannot execute action on this channel type")


def already_crossposted() -> BackendError:
    return BackendError(400, 40033, "This message has already been crossposted")


def cannot_dm_bot() -> BackendError:
    return BackendError(403, 50007, "Cannot send messages to this user")


def unknown_invite() -> BackendError:
    return BackendError(404, 10006, "Unknown Invite")


def unknown_emoji() -> BackendError:
    return BackendError(404, 10014, "Unknown Emoji")


def unknown_sticker() -> BackendError:
    return BackendError(404, 10060, "Unknown Sticker")


def unknown_scheduled_event() -> BackendError:
    return BackendError(404, 10070, "Unknown Guild Scheduled Event")


def unknown_auto_mod_rule() -> BackendError:
    return BackendError(404, 10000, "Unknown Auto Moderation Rule")


def target_not_connected_to_voice() -> BackendError:
    return BackendError(400, 40032, "Target user is not connected to voice")


def unknown_command_permissions() -> BackendError:
    return BackendError(404, 10066, "Unknown application command permissions")


def unknown_sku(*, status: int = 404) -> BackendError:
    return BackendError(status, 10027, "Unknown SKU")


def unknown_entitlement() -> BackendError:
    return BackendError(404, 10029, "Unknown Entitlement")


def not_consumable() -> BackendError:
    return BackendError(400, 40018, "Only consumable SKUs can be consumed")


def not_sandbox_entitlement() -> BackendError:
    return BackendError(400, 40019, "You can only delete sandbox entitlements.")


def entitlement_already_granted() -> BackendError:
    return BackendError(400, 40074, "An entitlement has already been granted for this resource")


def invalid_sku() -> BackendError:
    return BackendError(400, 50057, "Invalid SKU")


def entitlement_already_consumed() -> BackendError:
    return BackendError(400, 40022, "Entitlement already consumed")
