"""Actors: simulated humans that drive the bot through the gateway.

Everything an actor does is permission-checked and validated against what a
real user could physically do in the Discord client (no clicking disabled or
missing buttons, no invoking unsynced commands, no speaking in hidden
channels), then delivered as authentic gateway events.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Union

import discord

from . import interactions as _interactions
from .backend.errors import SetupError
from .builders import ChannelHandle, GuildHandle, UserHandle
from .results import InteractionResult, ResponseMessage, to_discord_message

BUTTON = 2
STRING_SELECT = 3

MessageLike = Union[discord.Message, ResponseMessage]


class MemberActor:
    """A guild member that acts like a real human user."""

    def __init__(self, env: Any, guild: GuildHandle, user: UserHandle) -> None:
        self._env = env
        self.guild = guild
        self.user = user

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def name(self) -> str:
        return self.user.name

    @property
    def mention(self) -> str:
        return self.user.mention

    @property
    def member(self) -> Optional[discord.Member]:
        cached = self._env.bot.get_guild(self.guild.id)
        return cached.get_member(self.id) if cached else None

    def _check(self, channel: ChannelHandle, *permissions: str) -> None:
        self._env.backend.require_permissions(self.guild.id, self.id, channel.id, *permissions)

    # ------------------------------------------------------------------ text

    async def send(
        self,
        channel: ChannelHandle,
        content: str = "",
        *,
        reply_to: Optional[MessageLike] = None,
        attachments: Sequence[tuple[str, bytes]] = (),
    ) -> discord.Message:
        backend = self._env.backend
        perm = "send_messages_in_threads" if channel.is_thread else "send_messages"
        self._check(channel, perm)
        reference = None
        if reply_to is not None:
            reference = {"channel_id": str(channel.id), "message_id": str(reply_to.id)}
        attachment_payloads = [
            backend.cdn.store_attachment(backend.snowflake(), channel.id, filename, data, None)
            for filename, data in attachments
        ]
        message = backend.create_message(
            channel.id,
            self.id,
            content,
            reference=reference,
            attachments=attachment_payloads,
        )
        await self._env.settle()
        return to_discord_message(self._env, message)

    async def edit(self, message: MessageLike, content: str) -> None:
        stored = self._env.backend.get_message(_channel_id_of(message), message.id)
        if stored.author_id != self.id:
            raise SetupError("Users can only edit their own messages")
        self._env.backend.edit_message(stored.channel_id, stored.id, {"content": content})
        await self._env.settle()

    async def delete(self, message: MessageLike) -> None:
        stored = self._env.backend.get_message(_channel_id_of(message), message.id)
        if stored.author_id != self.id:
            self._env.backend.require_permissions(
                self.guild.id, self.id, stored.channel_id, "manage_messages"
            )
        self._env.backend.delete_message(stored.channel_id, stored.id)
        await self._env.settle()

    async def typing(self, channel: ChannelHandle) -> None:
        self._check(channel, "send_messages")
        backend = self._env.backend
        from .backend import serializers

        guild = backend.get_guild(self.guild.id)
        payload = {
            "channel_id": str(channel.id),
            "user_id": str(self.id),
            "timestamp": 0,
            "guild_id": str(self.guild.id),
            "member": serializers.member_payload(backend, guild, guild.members[self.id]),
        }
        backend.emit("TYPING_START", payload)
        await self._env.settle()

    async def react(self, message: MessageLike, emoji: str) -> None:
        backend = self._env.backend
        stored = backend.get_message(_channel_id_of(message), message.id)
        backend.require_permissions(self.guild.id, self.id, stored.channel_id, "add_reactions")
        backend.add_reaction(stored.channel_id, stored.id, emoji, self.id)
        await self._env.settle()

    async def unreact(self, message: MessageLike, emoji: str) -> None:
        backend = self._env.backend
        stored = backend.get_message(_channel_id_of(message), message.id)
        backend.remove_reaction(stored.channel_id, stored.id, emoji, self.id)
        await self._env.settle()

    async def send_dm(self, content: str = "", **kwargs: Any) -> discord.Message:
        return await self.user.send_dm(content, **kwargs)

    # ---------------------------------------------------------- app commands

    def _resolve_command(self, name: str, type: int = 1) -> tuple[dict[str, Any], list[str]]:
        backend = self._env.backend
        parts = name.split()
        root = backend.find_command(parts[0], self.guild.id, type=type)
        if root is None:
            root = self._unsynced_fallback(parts[0], type)
        leaf, nesting = _interactions.walk_to_subcommand(root, parts[1:])
        return root, nesting

    def _unsynced_fallback(self, name: str, type: int) -> dict[str, Any]:
        tree = getattr(self._env.bot, "tree", None)
        in_tree = None
        if tree is not None:
            for scope in (None, discord.Object(self.guild.id)):
                for cmd in tree.get_commands(guild=scope, type=discord.AppCommandType(type)):
                    if cmd.name == name:
                        in_tree = (cmd, scope)
        if in_tree is None:
            raise SetupError(f"No application command named '{name}' exists")
        if self._env.strict_sync:
            raise SetupError(
                f"Command '{name}' exists in the command tree but was never synced — "
                "did you forget `await bot.tree.sync()`? "
                "(Pass strict_sync=False to dpt.run to auto-register unsynced commands.)"
            )
        cmd, scope = in_tree
        guild_id = None if scope is None else self.guild.id
        registered = self._env.backend.register_commands(
            guild_id,
            [c.to_dict(tree) for c in self._env.bot.tree.get_commands(guild=scope)],  # type: ignore[union-attr]
        )
        return next(c for c in registered if c["name"] == name and c.get("type", 1) == type)

    async def slash(self, channel: ChannelHandle, name: str, /, **options: Any) -> InteractionResult:
        """Invoke a synced slash command (use spaces for subcommands: "config set")."""
        self._check(channel, "use_application_commands")
        root, nesting = self._resolve_command(name)
        leaf, _ = _interactions.walk_to_subcommand(root, nesting)
        leaf_options, resolved = _interactions.build_options(self, name, leaf, options)
        data: dict[str, Any] = {
            "id": root["id"],
            "name": root["name"],
            "type": root.get("type", 1),
            "options": _interactions.nest_options(root, nesting, leaf_options),
        }
        if resolved:
            data["resolved"] = resolved
        if root.get("guild_id"):
            data["guild_id"] = root["guild_id"]
        return await self._dispatch_interaction(2, channel, data)

    async def context_menu(
        self, channel: ChannelHandle, name: str, target: Union["MemberActor", MessageLike]
    ) -> InteractionResult:
        """Invoke a user or message context-menu command on a target."""
        self._check(channel, "use_application_commands")
        backend = self._env.backend
        from .backend import serializers

        if isinstance(target, MemberActor):
            command_type = 2
            guild = backend.get_guild(self.guild.id)
            resolved: dict[str, Any] = {
                "users": {str(target.id): dict(serializers.user_payload(backend.get_user(target.id)))},
                "members": {
                    str(target.id): dict(
                        serializers.member_payload(backend, guild, guild.members[target.id], with_user=False)
                    )
                },
            }
        else:
            command_type = 3
            stored = backend.get_message(_channel_id_of(target), target.id)
            resolved = {"messages": {str(target.id): dict(serializers.message_payload(backend, stored))}}
        root, _ = self._resolve_command(name, type=command_type)
        data = {
            "id": root["id"],
            "name": root["name"],
            "type": command_type,
            "target_id": str(target.id),
            "resolved": resolved,
        }
        return await self._dispatch_interaction(2, channel, data)

    async def autocomplete(
        self, channel: ChannelHandle, name: str, option: str, value: str, /, **filled: Any
    ) -> list[dict[str, Any]]:
        """Type into an autocomplete option; returns the choices the bot offered."""
        root, nesting = self._resolve_command(name)
        leaf, _ = _interactions.walk_to_subcommand(root, nesting)
        leaf_options, _resolved = _interactions.build_options(self, name, leaf, filled, partial=True)
        declared = {o["name"]: o for o in (leaf.get("options") or [])}
        if option not in declared:
            raise SetupError(f"Command '{name}' has no option '{option}'")
        leaf_options.append(
            {"name": option, "type": declared[option]["type"], "value": value, "focused": True}
        )
        data = {
            "id": root["id"],
            "name": root["name"],
            "type": root.get("type", 1),
            "options": _interactions.nest_options(root, nesting, leaf_options),
        }
        result = await self._dispatch_interaction(4, channel, data)
        return result.autocomplete_choices or []

    # ------------------------------------------------------------ components

    async def click(
        self,
        message: MessageLike,
        *,
        label: Optional[str] = None,
        custom_id: Optional[str] = None,
    ) -> InteractionResult:
        """Click a button on a message, exactly as a user could."""
        stored = self._visible_message(message)
        button = _find_component(stored.components, types=(BUTTON,), custom_id=custom_id, label=label)
        return await self._component_interaction(
            stored, {"custom_id": button["custom_id"], "component_type": BUTTON}
        )

    async def select(
        self,
        message: MessageLike,
        values: Sequence[str],
        *,
        custom_id: Optional[str] = None,
    ) -> InteractionResult:
        """Choose values in a string select menu."""
        stored = self._visible_message(message)
        menu = _find_component(stored.components, types=(STRING_SELECT,), custom_id=custom_id, label=None)
        valid = {o["value"] for o in menu.get("options") or []}
        for value in values:
            if value not in valid:
                raise SetupError(f"Select option {value!r} does not exist (options: {sorted(valid)})")
        return await self._component_interaction(
            stored,
            {"custom_id": menu["custom_id"], "component_type": STRING_SELECT, "values": list(values)},
        )

    async def submit_modal(self, shown: InteractionResult, values: dict[str, str]) -> InteractionResult:
        """Fill in and submit a modal the bot previously showed this user."""
        spec = shown.modal
        if spec is None:
            raise SetupError("That interaction did not respond with a modal")
        components = []
        for row in spec.get("components") or []:
            for item in row.get("components") or []:
                custom_id = item.get("custom_id")
                if custom_id in values:
                    components.append(
                        {"type": 1, "components": [{"type": 4, "custom_id": custom_id, "value": values[custom_id]}]}
                    )
        channel = ChannelHandle(
            self._env, self.guild, self._env.backend.get_channel(shown.record["channel_id"])
        )
        return await self._dispatch_interaction(
            5, channel, {"custom_id": spec["custom_id"], "components": components}
        )

    # -------------------------------------------------------------- plumbing

    def _visible_message(self, message: MessageLike) -> Any:
        backend = self._env.backend
        stored = backend.get_message(_channel_id_of(message), message.id)
        if stored.flags & discord.MessageFlags(ephemeral=True).value:
            meta = stored.interaction_metadata or {}
            if str((meta.get("user") or {}).get("id")) != str(self.id):
                raise SetupError(
                    "That message is ephemeral and not visible to this user — "
                    "a real user could not interact with it"
                )
        return stored

    async def _component_interaction(self, stored: Any, data: dict[str, Any]) -> InteractionResult:
        backend = self._env.backend
        from .backend import serializers

        channel = ChannelHandle(self._env, self.guild, backend.get_channel(stored.channel_id))
        result = await self._dispatch_interaction(
            3,
            channel,
            data,
            extra={"message": dict(serializers.message_payload(backend, stored))},
            source_message_id=stored.id,
        )
        return result

    async def _dispatch_interaction(
        self,
        type: int,
        channel: ChannelHandle,
        data: dict[str, Any],
        *,
        extra: Optional[dict[str, Any]] = None,
        source_message_id: Optional[int] = None,
    ) -> InteractionResult:
        backend = self._env.backend
        record, payload = _interactions.base_payload(
            backend,
            type=type,
            channel_id=channel.id,
            guild_id=self.guild.id,
            user_id=self.id,
            data=data,
        )
        if extra:
            payload.update(extra)
        if source_message_id is not None:
            record["source_message_id"] = source_message_id
        backend.emit("INTERACTION_CREATE", payload)
        await self._env.settle()
        return InteractionResult(self._env, record)

    def __repr__(self) -> str:
        return f"<MemberActor id={self.id} name={self.name!r} guild={self.guild.id}>"


def _channel_id_of(message: MessageLike) -> int:
    if isinstance(message, discord.Message):
        return message.channel.id
    return message._message.channel_id  # ResponseMessage


def _find_component(
    rows: list[dict[str, Any]],
    *,
    types: tuple[int, ...],
    custom_id: Optional[str],
    label: Optional[str],
) -> dict[str, Any]:
    found = []
    for row in rows or []:
        for component in row.get("components") or []:
            if component.get("type") not in types:
                continue
            if custom_id is not None and component.get("custom_id") != custom_id:
                continue
            if label is not None and component.get("label") != label:
                continue
            found.append(component)
    if not found:
        raise SetupError(
            f"No matching component (custom_id={custom_id!r}, label={label!r}) — "
            "a real user could not interact with it"
        )
    component = found[0]
    if component.get("disabled"):
        raise SetupError("That component is disabled — a real user could not interact with it")
    return component
