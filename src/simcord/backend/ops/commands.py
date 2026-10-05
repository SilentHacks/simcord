"""Application commands and interactions."""

from __future__ import annotations

from typing import Any

from ...enums import AppCommandType
from .. import errors
from ..command_access import command_access
from ..models import Interaction
from .base import BackendBase


class CommandsMixin(BackendBase):
    # --------------------------------------------------- application commands

    def register_commands(self, guild_id: int | None, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        registered = {}
        existing = self.commands.get(guild_id, {})
        for payload in payloads:
            cmd = dict(payload)
            cmd.setdefault("type", AppCommandType.CHAT_INPUT)
            previous = existing.get((cmd["name"], cmd["type"]))
            cmd["id"] = previous["id"] if previous is not None else str(self.snowflake())
            cmd["application_id"] = str(self.application_id)
            cmd.setdefault("description", "")
            cmd.setdefault("options", [])
            cmd.setdefault("default_member_permissions", None)
            cmd.setdefault("nsfw", False)
            cmd.setdefault("dm_permission", True)
            if guild_id is not None:
                cmd["guild_id"] = str(guild_id)
            registered[(cmd["name"], cmd["type"])] = cmd
        self.commands[guild_id] = registered
        return list(registered.values())

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
        )
        self.interactions[interaction_id] = record
        self.interaction_tokens[record.token] = interaction_id
        return record

    def interaction_by_token(self, token: str) -> Interaction:
        interaction_id = self.interaction_tokens.get(token)
        if interaction_id is None:
            raise errors.unknown_webhook()
        return self.interactions[interaction_id]
