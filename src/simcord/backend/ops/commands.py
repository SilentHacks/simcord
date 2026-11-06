"""Application commands and interactions."""

from __future__ import annotations

from typing import Any

from ...enums import AppCommandType
from .. import errors
from ..command_access import command_access
from ..models import Interaction
from .base import BackendBase

COMMAND_FIELDS = (
    "name",
    "description",
    "type",
    "nsfw",
    "options",
    "default_member_permissions",
    "dm_permission",
    "contexts",
    "integration_types",
    "name_localizations",
    "description_localizations",
)


def _prepare_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(options) > 25:
        raise errors.invalid_form_body(
            errors={
                "_errors": [{"code": "BASE_TYPE_MAX_LENGTH", "message": "Must be 25 or fewer in length."}]
            }
        )
    prepared = []
    optional_seen = False
    for index, option in enumerate(options):
        required = option.get("required", False)
        if required and optional_seen:
            raise errors.invalid_form_body(
                errors={
                    str(index): {
                        "_errors": [
                            {
                                "code": "APPLICATION_COMMAND_OPTIONS_REQUIRED_INVALID",
                                "message": "Required options must be placed before non-required options",
                            }
                        ]
                    }
                }
            )
        optional_seen |= not required
        normalized = dict(option)
        normalized["name_localizations"] = normalized.get("name_localizations") or None
        normalized["description_localizations"] = normalized.get("description_localizations") or None
        for key in ("required", "autocomplete"):
            if normalized.get(key) is False:
                normalized.pop(key)
        for key in ("options", "choices", "channel_types"):
            if key in normalized and not normalized[key]:
                normalized.pop(key)
        if "options" in normalized:
            try:
                normalized["options"] = _prepare_options(normalized["options"])
            except errors.BackendError as exc:
                raise errors.invalid_form_body(errors={str(index): {"options": exc.errors}}) from None
        prepared.append(normalized)
    return prepared


class CommandsMixin(BackendBase):
    # --------------------------------------------------- application commands

    def prepare_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        unsupported = sorted(payload.keys() - set(COMMAND_FIELDS))
        if unsupported:
            raise ValueError(f"Unsupported command field(s): {', '.join(unsupported)}")
        cmd = dict(payload)
        cmd.setdefault("type", AppCommandType.CHAT_INPUT)
        cmd.setdefault("description", "")
        options = cmd.pop("options", None)
        if options:
            try:
                cmd["options"] = _prepare_options(options)
            except errors.BackendError as exc:
                raise errors.invalid_form_body(errors={"options": exc.errors}) from None
        permissions = cmd.get("default_member_permissions")
        cmd["default_member_permissions"] = str(permissions) if permissions is not None else None
        cmd.setdefault("dm_permission", True)
        cmd.setdefault("nsfw", False)
        cmd["name_localizations"] = cmd.get("name_localizations") or None
        cmd["description_localizations"] = cmd.get("description_localizations") or None
        return cmd

    def _store_command(
        self, guild_id: int | None, cmd: dict[str, Any], previous: dict[str, Any] | None
    ) -> dict[str, Any]:
        if guild_id is not None:
            for key in ("dm_permission", "contexts", "integration_types"):
                cmd.pop(key, None)
        unchanged = previous is not None and cmd == {
            key: value
            for key, value in previous.items()
            if key not in {"id", "application_id", "guild_id", "version"}
        }
        cmd["version"] = previous["version"] if unchanged and previous is not None else str(self.snowflake())
        cmd["id"] = previous["id"] if previous is not None else str(self.snowflake())
        cmd["application_id"] = str(self.application_id)
        if guild_id is not None:
            cmd["guild_id"] = str(guild_id)
        return cmd

    def register_commands(self, guild_id: int | None, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = []
        for index, payload in enumerate(payloads):
            try:
                prepared.append(self.prepare_command(payload))
            except errors.BackendError as exc:
                if exc.errors is not None:
                    raise errors.invalid_form_body(errors={str(index): exc.errors}) from None
                raise
        existing = self.commands.get(guild_id, {})
        registered = {}
        for cmd in prepared:
            key = (cmd["name"], cmd["type"])
            registered[key] = self._store_command(guild_id, cmd, existing.get(key))
        self.commands[guild_id] = registered
        return list(registered.values())

    def get_command(self, guild_id: int | None, command_id: int) -> dict[str, Any]:
        for command in self.commands.get(guild_id, {}).values():
            if int(command["id"]) == command_id:
                return command
        raise errors.BackendError(404, 10063, "Unknown application command")

    def upsert_command(
        self, guild_id: int | None, payload: dict[str, Any], *, command_id: int | None = None
    ) -> dict[str, Any]:
        previous = self.get_command(guild_id, command_id) if command_id is not None else None
        if previous is not None:
            payload = {**{key: previous[key] for key in COMMAND_FIELDS if key in previous}, **payload}
        cmd = self.prepare_command(payload)
        key = (cmd["name"], cmd["type"])
        commands = self.commands.setdefault(guild_id, {})
        if previous is None:
            previous = commands.get(key)
        cmd = self._store_command(guild_id, cmd, previous)
        if previous is not None:
            commands.pop((previous["name"], previous["type"]))
        commands[key] = cmd
        return cmd

    def delete_command(self, guild_id: int | None, command_id: int) -> None:
        command = self.get_command(guild_id, command_id)
        del self.commands[guild_id][(command["name"], command["type"])]

    def set_command_permissions(
        self, guild_id: int, command_id: int, permissions: list[dict[str, Any]]
    ) -> None:
        """Seed a command's per-guild permission overrides (omnipotent setup).

        Real Discord only lets a *user* (OAuth2 bearer) set these, so there is no
        bot-driven route — tests arrange them, then the bot reads them back via
        ``AppCommand.fetch_permissions``.
        """
        self.command_permissions[(guild_id, command_id)] = list(permissions)

    def get_command_permissions(self, guild_id: int, command_id: int) -> list[dict[str, Any]] | None:
        return self.command_permissions.get((guild_id, command_id))

    def find_command(
        self, name: str, guild_id: int | None, type: int = AppCommandType.CHAT_INPUT
    ) -> dict[str, Any] | None:
        for scope in (guild_id, None):
            cmd = self.commands.get(scope, {}).get((name, type))
            if cmd is not None:
                return cmd
        return None

    def visible_commands(
        self, *, user_id: int, channel_id: int, type: int = AppCommandType.CHAT_INPUT
    ) -> list[dict[str, Any]]:
        channel = self.get_channel(channel_id)
        scopes = (channel.guild_id, None) if channel.guild_id is not None else (None,)
        visible = [
            command
            for scope in scopes
            for command in self.commands.get(scope, {}).values()
            if command.get("type", AppCommandType.CHAT_INPUT) == type
            and command_access(self, command, user_id=user_id, channel_id=channel_id).allowed
        ]
        return sorted(visible, key=lambda command: (command["name"], int(command["id"])))

    # ----------------------------------------------------------- interactions

    def new_interaction(self, type: int, channel_id: int, user_id: int, guild_id: int | None) -> Interaction:
        interaction_id = self.snowflake()
        record = Interaction(
            id=interaction_id,
            token=f"simcord_interaction_{interaction_id}",
            type=type,
            channel_id=channel_id,
            guild_id=guild_id,
            user_id=user_id,
            expires_at=self.iso_after(15 * 60),
            initial_response_deadline=self.iso_after(3),
        )
        self.interactions[interaction_id] = record
        self.interaction_tokens[record.token] = interaction_id
        return record

    def interaction_by_token(self, token: str) -> Interaction:
        interaction_id = self.interaction_tokens.get(token)
        if interaction_id is None:
            raise errors.unknown_webhook()
        record = self.interactions[interaction_id]
        if record.expires_at is not None and self.now_iso() >= record.expires_at:
            raise errors.unknown_webhook()
        if (
            not record.responded
            and record.initial_response_deadline is not None
            and self.now_iso() >= record.initial_response_deadline
        ):
            raise errors.unknown_webhook()
        return record
