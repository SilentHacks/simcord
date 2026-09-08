"""Actors: simulated humans that drive the bot through the gateway.

Everything an actor does is permission-checked and validated against what a
real user could physically do in the Discord client (no clicking disabled or
missing buttons, no invoking unsynced commands, no speaking in hidden
channels), then delivered as authentic gateway events.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import wraps
from typing import TYPE_CHECKING, Any

import discord

from . import interactions as _interactions
from .backend import serializers
from .backend.errors import SetupError
from .builders import ChannelHandle, GuildHandle, RoleHandle, UserHandle
from .components import validate_modal, walk_components
from .enums import SELECT_TYPES, AppCommandType, ComponentType, InteractionType
from .results import InteractionResult, ResponseMessage, to_discord_message

if TYPE_CHECKING:
    from .env import Env

MessageLike = discord.Message | ResponseMessage


class MemberActor:
    """A guild member that acts like a real human user."""

    def __init__(self, env: Env, guild: GuildHandle, user: UserHandle) -> None:
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
    def member(self) -> discord.Member | None:
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
        reply_to: MessageLike | None = None,
        attachments: Sequence[tuple[str, bytes]] = (),
    ) -> discord.Message:
        backend = self._env.backend
        perm = "send_messages_in_threads" if channel.is_thread else "send_messages"
        self._check(channel, perm)
        reference = None
        if reply_to is not None:
            reference = {"channel_id": str(channel.id), "message_id": str(reply_to.id)}
        attachment_payloads = backend.store_attachments(channel.id, attachments)
        message = backend.create_message(
            channel.id,
            self.id,
            content,
            reference=reference,
            attachments=attachment_payloads,
        )
        await self._env._settle_internal(dispatch="MEMBER.send")
        return to_discord_message(self._env, message)

    async def edit(self, message: MessageLike, content: str) -> None:
        stored = self._env.backend.get_message(_channel_id_of(message), message.id)
        if stored.author_id != self.id:
            raise SetupError("Users can only edit their own messages")
        self._env.backend.edit_message(stored.channel_id, stored.id, {"content": content})
        await self._env._settle_internal(dispatch="MEMBER.edit")

    async def delete(self, message: MessageLike) -> None:
        stored = self._env.backend.get_message(_channel_id_of(message), message.id)
        if stored.author_id != self.id:
            self._env.backend.require_permissions(
                self.guild.id, self.id, stored.channel_id, "manage_messages"
            )
        self._env.backend.delete_message(stored.channel_id, stored.id)
        await self._env._settle_internal(dispatch="MEMBER.delete")

    async def typing(self, channel: ChannelHandle) -> None:
        self._check(channel, "send_messages")
        backend = self._env.backend
        guild = backend.get_guild(self.guild.id)
        payload = {
            "channel_id": str(channel.id),
            "user_id": str(self.id),
            "timestamp": 0,
            "guild_id": str(self.guild.id),
            "member": serializers.member_payload(backend, guild, guild.members[self.id]),
        }
        backend.emit("TYPING_START", payload)
        await self._env._settle_internal(dispatch="MEMBER.typing")

    async def react(self, message: MessageLike, emoji: str) -> None:
        backend = self._env.backend
        stored = backend.get_message(_channel_id_of(message), message.id)
        backend.require_permissions(self.guild.id, self.id, stored.channel_id, "add_reactions")
        backend.add_reaction(stored.channel_id, stored.id, emoji, self.id)
        await self._env._settle_internal(dispatch="MEMBER.react")

    async def unreact(self, message: MessageLike, emoji: str) -> None:
        backend = self._env.backend
        stored = backend.get_message(_channel_id_of(message), message.id)
        backend.remove_reaction(stored.channel_id, stored.id, emoji, self.id)
        await self._env._settle_internal(dispatch="MEMBER.unreact")

    async def send_dm(self, content: str = "", **kwargs: Any) -> discord.Message:
        return await self.user.send_dm(content, **kwargs)

    # ---------------------------------------------------------- app commands

    def _resolve_root(self, name: str, type: int) -> dict[str, Any]:
        """Find a registered command by exact name, falling back to the unsynced tree."""
        root = self._env.backend.find_command(name, self.guild.id, type=type)
        if root is None:
            root = self._unsynced_fallback(name, type)
        return root

    def _resolve_command(
        self, name: str, type: int = AppCommandType.CHAT_INPUT
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """Resolve "root [group] [sub]" to (root command, leaf spec, nesting path).

        Only slash commands nest, so the space-separated parts are walked as a
        subcommand path; context menus (whose names contain spaces) resolve by
        full name via :meth:`_resolve_root` instead.
        """
        parts = name.split()
        root = self._resolve_root(parts[0], type)
        leaf, nesting = _interactions.walk_to_subcommand(root, parts[1:])
        return root, leaf, nesting

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
                "(Pass strict_sync=False to simcord.run to auto-register unsynced commands.)"
            )
        cmd, scope = in_tree
        guild_id = None if scope is None else self.guild.id
        registered = self._env.backend.register_commands(
            guild_id,
            [c.to_dict(tree) for c in self._env.bot.tree.get_commands(guild=scope)],  # type: ignore[union-attr]
        )
        return next(
            c for c in registered if c["name"] == name and c.get("type", AppCommandType.CHAT_INPUT) == type
        )

    async def slash(self, channel: ChannelHandle, name: str, /, **options: Any) -> InteractionResult:
        """Invoke a synced slash command (use spaces for subcommands: "config set")."""
        self._check(channel, "use_application_commands")
        root, leaf, nesting = self._resolve_command(name)
        leaf_options, resolved = _interactions.build_options(self, name, leaf, options)
        data: dict[str, Any] = {
            "id": root["id"],
            "name": root["name"],
            "type": root.get("type", AppCommandType.CHAT_INPUT),
            "options": _interactions.nest_options(root, nesting, leaf_options),
        }
        if resolved:
            data["resolved"] = resolved
        if root.get("guild_id"):
            data["guild_id"] = root["guild_id"]
        return await self._dispatch_interaction(InteractionType.APPLICATION_COMMAND, channel, data)

    async def context_menu(
        self, channel: ChannelHandle, name: str, target: MemberActor | MessageLike
    ) -> InteractionResult:
        """Invoke a user or message context-menu command on a target."""
        self._check(channel, "use_application_commands")
        backend = self._env.backend
        if isinstance(target, MemberActor):
            command_type = AppCommandType.USER
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
            command_type = AppCommandType.MESSAGE
            stored = backend.get_message(_channel_id_of(target), target.id)
            resolved = {"messages": {str(target.id): dict(serializers.message_payload(backend, stored))}}
        # Context-menu names contain spaces and never nest, so resolve the full
        # name directly rather than treating words as a subcommand path.
        root = self._resolve_root(name, command_type)
        data = {
            "id": root["id"],
            "name": root["name"],
            "type": command_type,
            "target_id": str(target.id),
            "resolved": resolved,
        }
        return await self._dispatch_interaction(InteractionType.APPLICATION_COMMAND, channel, data)

    async def autocomplete(
        self, channel: ChannelHandle, name: str, option: str, value: str, /, **filled: Any
    ) -> list[dict[str, Any]]:
        """Type into an autocomplete option; returns the choices the bot offered."""
        root, leaf, nesting = self._resolve_command(name)
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
            "type": root.get("type", AppCommandType.CHAT_INPUT),
            "options": _interactions.nest_options(root, nesting, leaf_options),
        }
        result = await self._dispatch_interaction(
            InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE, channel, data
        )
        return result.autocomplete_choices or []

    # ------------------------------------------------------------ components
    async def click(
        self,
        message: MessageLike,
        *,
        label: str | None = None,
        custom_id: str | None = None,
    ) -> InteractionResult:
        """Click one interactive button, including buttons nested in V2 layouts."""
        return await _component_click(self, message, label=label, custom_id=custom_id)

    async def select(
        self,
        message: MessageLike,
        values: Sequence[Any],
        *,
        custom_id: str | None = None,
    ) -> InteractionResult:
        """Choose values in a select menu.

        String values are strings; entity values are existing
        :class:`UserHandle`/:class:`MemberActor`, :class:`RoleHandle`, or
        :class:`ChannelHandle` handles. Modal values use this same contract.
        """
        return await _component_select(self, message, values, custom_id=custom_id)

    async def submit_modal(self, shown: InteractionResult, values: dict[str, Any]) -> InteractionResult:
        """Fill and submit a modal shown to this user.

        Values are strings for text inputs and string selects, existing entity
        handles for entity selects, booleans for checkboxes, and
        ``(filename, bytes)`` tuples for file uploads (a sequence of tuples for
        multi-file controls). Optional controls may be omitted.
        """
        return await _submit_modal(self, shown, values)

    # ------------------------------------------------------------------ polls

    async def vote(self, message: MessageLike, *, answer: int) -> None:
        """Cast (or move) this user's vote to ``answer`` (a 1-based answer id)."""
        backend = self._env.backend
        stored = backend.get_message(_channel_id_of(message), message.id)
        # require_permissions enforces view_channel whenever a channel id is passed.
        backend.require_permissions(self.guild.id, self.id, stored.channel_id)
        backend.add_poll_vote(stored.channel_id, stored.id, answer, self.id)
        await self._env._settle_internal(dispatch="MEMBER.vote")

    async def remove_vote(self, message: MessageLike, *, answer: int) -> None:
        """Retract this user's vote for ``answer``."""
        backend = self._env.backend
        stored = backend.get_message(_channel_id_of(message), message.id)
        backend.require_permissions(self.guild.id, self.id, stored.channel_id)
        backend.remove_poll_vote(stored.channel_id, stored.id, answer, self.id)
        await self._env._settle_internal(dispatch="MEMBER.remove_vote")

    # ------------------------------------------------------------------ voice

    async def join_voice(
        self, channel: ChannelHandle, *, self_mute: bool = False, self_deaf: bool = False
    ) -> None:
        """Connect to a voice/stage channel (state only — no audio)."""
        self._check(channel, "connect")
        self._env.backend.set_voice_state(
            self.guild.id, self.id, channel.id, self_mute=self_mute, self_deaf=self_deaf
        )
        await self._env._settle_internal(dispatch="MEMBER.join_voice")

    async def leave_voice(self) -> None:
        """Disconnect from voice."""
        self._env.backend.set_voice_state(self.guild.id, self.id, None)
        await self._env._settle_internal(dispatch="MEMBER.leave_voice")

    async def set_voice(self, *, self_mute: bool | None = None, self_deaf: bool | None = None) -> None:
        """Update self-mute/self-deaf while connected."""
        state = self._env.backend.get_guild(self.guild.id).voice_states.get(self.id)
        if state is None:
            raise SetupError("This user is not connected to a voice channel")
        flags: dict[str, Any] = {}
        if self_mute is not None:
            flags["self_mute"] = self_mute
        if self_deaf is not None:
            flags["self_deaf"] = self_deaf
        self._env.backend.set_voice_state(self.guild.id, self.id, state.channel_id, **flags)
        await self._env._settle_internal(dispatch="MEMBER.set_voice")

    # -------------------------------------------------------- scheduled events

    async def subscribe_event(self, event: Any) -> None:
        """Mark interest in a scheduled event (accepts an id or a handle with ``.id``)."""
        event_id = event if isinstance(event, int) else event.id
        self._env.backend.set_scheduled_event_subscription(self.guild.id, event_id, self.id, True)
        await self._env._settle_internal(dispatch="MEMBER.subscribe_event")

    async def unsubscribe_event(self, event: Any) -> None:
        event_id = event if isinstance(event, int) else event.id
        self._env.backend.set_scheduled_event_subscription(self.guild.id, event_id, self.id, False)
        await self._env._settle_internal(dispatch="MEMBER.unsubscribe_event")

    # -------------------------------------------------------------- plumbing

    def _visible_message(self, message: MessageLike) -> Any:
        return _visible_message(self, message)

    async def _component_interaction(self, stored: Any, data: dict[str, Any]) -> InteractionResult:
        return await _component_interaction(self, stored, data)

    async def _dispatch_interaction(
        self,
        type: int,
        channel: ChannelHandle,
        data: dict[str, Any],
        *,
        extra: dict[str, Any] | None = None,
        source_message_id: int | None = None,
    ) -> InteractionResult:
        return await _dispatch_actor_interaction(
            self,
            type,
            channel,
            data,
            extra=extra,
            source_message_id=source_message_id,
        )

    def __repr__(self) -> str:
        return f"<MemberActor id={self.id} name={self.name!r} guild={self.guild.id}>"


def _channel_id_of(message: MessageLike, fallback: int | None = None) -> int:
    if isinstance(message, ResponseMessage):
        return message.channel_id
    channel = message.channel
    if channel is None:
        if fallback is None:
            raise SetupError("That message is not bound to a channel")
        return fallback
    return channel.id


_ENTITY_SELECT_HANDLES: dict[ComponentType, tuple[type, ...]] = {
    ComponentType.USER_SELECT: (UserHandle, MemberActor),
    ComponentType.ROLE_SELECT: (RoleHandle,),
    ComponentType.CHANNEL_SELECT: (ChannelHandle,),
    ComponentType.MENTIONABLE_SELECT: (UserHandle, MemberActor, RoleHandle),
}


def _check_user_dm_channel(actor: Any, channel_id: int) -> None:
    if isinstance(actor, UserHandle) and actor._env.backend.dm_channels.get(actor.id) != channel_id:
        raise SetupError(
            "UserHandle interaction operations are restricted to this user's DM channel — "
            "a real user could not interact with guild messages"
        )


def _visible_message(actor: Any, message: MessageLike) -> Any:
    backend = actor._env.backend
    fallback = actor.dm_channel.id if isinstance(actor, UserHandle) else None
    channel_id = _channel_id_of(message, fallback)
    _check_user_dm_channel(actor, channel_id)
    stored = backend.get_message(channel_id, message.id)
    if not stored.visible_to(actor.id):
        raise SetupError(
            "That message is ephemeral and not visible to this user — a real user could not interact with it"
        )
    return stored


def _find_component(
    rows: list[dict[str, Any]],
    *,
    types: tuple[int, ...],
    custom_id: str | None,
    label: str | None,
) -> dict[str, Any]:
    found = []
    for component in walk_components(rows):
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
    if len(found) > 1:
        raise SetupError(
            f"Ambiguous component (custom_id={custom_id!r}, label={label!r}); identify exactly one component"
        )
    component = found[0]
    if component.get("disabled"):
        raise SetupError("That component is disabled — a real user could not interact with it")
    return component


async def _component_click(
    actor: Any,
    message: MessageLike,
    *,
    label: str | None,
    custom_id: str | None,
) -> InteractionResult:
    stored = _visible_message(actor, message)
    button = _find_component(
        stored.components, types=(ComponentType.BUTTON,), custom_id=custom_id, label=label
    )
    if button.get("style") in (5, 6) or not button.get("custom_id"):
        raise SetupError("That button is a link or premium control, not an interactive callback")
    data: dict[str, Any] = {
        "custom_id": button["custom_id"],
        "component_type": ComponentType.BUTTON,
    }
    if button.get("id") is not None:
        data["id"] = button["id"]
    return await _component_interaction(actor, stored, data)


def _check_select_handles(menu_type: int, values: Sequence[Any]) -> None:
    allowed = _ENTITY_SELECT_HANDLES[ComponentType(menu_type)]
    for value in values:
        if not isinstance(value, allowed):
            names = " or ".join(t.__name__ for t in allowed)
            raise SetupError(f"{ComponentType(menu_type).name} expects {names}, got {type(value).__name__}")


def _as_values(values: Any) -> list[Any]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise SetupError("Select values must be a sequence")
    return list(values)


def _ensure_unique(values: Sequence[Any]) -> None:
    for index, value in enumerate(values):
        if any(value == previous for previous in values[:index]):
            raise SetupError("A select cannot contain duplicate values")


async def _component_select(
    actor: Any,
    message: MessageLike,
    values: Sequence[Any],
    *,
    custom_id: str | None,
) -> InteractionResult:
    stored = _visible_message(actor, message)
    menu = _find_component(stored.components, types=SELECT_TYPES, custom_id=custom_id, label=None)
    chosen = _as_values(values)
    menu_type = ComponentType(menu["type"])
    _ensure_unique(chosen)
    lo = menu.get("min_values", 1)
    hi = menu.get("max_values", 1)
    if not isinstance(lo, int) or not isinstance(hi, int) or not lo <= len(chosen) <= hi:
        raise SetupError(f"Select expects between {lo} and {hi} value(s), got {len(chosen)}")

    data: dict[str, Any] = {"custom_id": menu["custom_id"], "component_type": menu_type}
    if menu.get("id") is not None:
        data["id"] = menu["id"]
    if menu_type == ComponentType.STRING_SELECT:
        if not all(isinstance(value, str) for value in chosen):
            raise SetupError("STRING_SELECT expects string values")
        valid = {option["value"] for option in menu.get("options") or []}
        for value in chosen:
            if value not in valid:
                error = SetupError(f"Select option {value!r} does not exist")
                error.add_note(f"Available options: {sorted(valid)}")
                raise error
        data["values"] = chosen
    else:
        _check_select_handles(menu_type, chosen)
        resolved: dict[str, dict[str, Any]] = {}
        for value in chosen:
            _interactions.resolve_handle(actor._env.backend, value, resolved, user_id=actor.id)
        data["values"] = [str(value.id) for value in chosen]
        data["resolved"] = resolved
    return await _component_interaction(actor, stored, data)


def _component_interaction_data_id(component: dict[str, Any], data: dict[str, Any]) -> None:
    if component.get("id") is not None:
        data["id"] = component["id"]


def _modal_components(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return interactive modal leaves, retaining each layout wrapper."""
    supported = {
        ComponentType.TEXT_INPUT,
        *SELECT_TYPES,
        ComponentType.FILE_UPLOAD,
        ComponentType.RADIO_GROUP,
        ComponentType.CHECKBOX_GROUP,
        ComponentType.CHECKBOX,
    }
    leaves: list[dict[str, Any]] = []
    for component in spec.get("components") or []:
        if component.get("type") == ComponentType.TEXT_DISPLAY:
            continue
        if component.get("type") == ComponentType.ACTION_ROW:
            for child in component.get("components") or []:
                if child.get("type") in supported:
                    leaves.append(child)
        elif component.get("type") == ComponentType.LABEL:
            child = component.get("component")
            if child and child.get("type") in supported:
                leaves.append(child)
        elif component.get("type") in supported:
            leaves.append(component)
    return leaves


def _modal_control_map(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    controls = {}
    for control in _modal_components(spec):
        custom_id = control.get("custom_id")
        if not custom_id:
            raise SetupError("Modal control is missing custom_id")
        if custom_id in controls:
            raise SetupError(f"Modal custom_id {custom_id!r} is ambiguous")
        controls[custom_id] = control
    return controls


def _modal_files(value: Any) -> list[tuple[str, bytes]]:
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], bytes)
    ):
        return [value]
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise SetupError("FileUpload expects (filename, bytes) tuples")
    files = list(value)
    if not all(
        isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], bytes)
        for item in files
    ):
        raise SetupError("FileUpload expects (filename, bytes) tuples")
    return files


def _modal_bounds(component: dict[str, Any], *, default_min: int, default_max: int) -> tuple[int, int]:
    lo = component.get("min_values")
    hi = component.get("max_values")
    lo = default_min if lo is None else lo
    hi = default_max if hi is None else hi
    if (
        isinstance(lo, bool)
        or isinstance(hi, bool)
        or not isinstance(lo, int)
        or not isinstance(hi, int)
        or hi < 1
        or hi < lo
        or hi > default_max
    ):
        raise SetupError(f"Invalid modal value bounds for {component.get('custom_id')!r}")
    return lo, hi


def _modal_leaf(
    actor: Any,
    component: dict[str, Any],
    values: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    channel_id: int,
    pending_uploads: list[tuple[dict[str, Any], str, bytes]],
) -> dict[str, Any] | None:
    typ = ComponentType(component["type"])
    custom_id = component["custom_id"]
    supplied = custom_id in values
    value = values.get(custom_id)
    required = bool(component.get("required", typ in (ComponentType.TEXT_INPUT, *SELECT_TYPES)))
    data: dict[str, Any] = {"type": typ, "custom_id": custom_id}
    _component_interaction_data_id(component, data)

    if typ == ComponentType.TEXT_INPUT:
        if not supplied:
            if required:
                raise SetupError(f"Required modal control {custom_id!r} was not supplied")
            value = ""
        if not isinstance(value, str):
            raise SetupError(f"Text input {custom_id!r} expects a string")
        if required and not value:
            raise SetupError(f"Required modal control {custom_id!r} cannot be empty")
        minimum = component.get("min_length")
        maximum = component.get("max_length")
        if minimum is not None and len(value) < minimum:
            raise SetupError(f"Text input {custom_id!r} is shorter than min_length={minimum}")
        if maximum is not None and len(value) > maximum:
            raise SetupError(f"Text input {custom_id!r} exceeds max_length={maximum}")
        data["value"] = value
    elif typ in SELECT_TYPES:
        chosen = [] if not supplied else _as_values(value)
        _ensure_unique(chosen)
        lo, hi = _modal_bounds(component, default_min=1, default_max=25)
        if required and not chosen:
            raise SetupError(f"Required modal control {custom_id!r} was not supplied")
        if (supplied or required) and not lo <= len(chosen) <= hi:
            raise SetupError(f"Modal control {custom_id!r} expects between {lo} and {hi} values")
        if typ == ComponentType.STRING_SELECT:
            if not all(isinstance(item, str) for item in chosen):
                raise SetupError(f"Select {custom_id!r} expects string values")
            options = {option["value"] for option in component.get("options") or []}
            unknown = [item for item in chosen if item not in options]
            if unknown:
                raise SetupError(f"Select option {unknown[0]!r} does not exist")
        else:
            _check_select_handles(typ, chosen)
            for item in chosen:
                _interactions.resolve_handle(actor._env.backend, item, resolved, user_id=actor.id)
            chosen = [str(item.id) for item in chosen]
        data["values"] = chosen
    elif typ == ComponentType.FILE_UPLOAD:
        files = [] if not supplied else _modal_files(value)
        lo, hi = _modal_bounds(component, default_min=0, default_max=10)
        if required and not files:
            raise SetupError(f"Required modal control {custom_id!r} was not supplied")
        if (supplied or required) and not lo <= len(files) <= hi:
            raise SetupError(f"Modal control {custom_id!r} expects between {lo} and {hi} files")
        data["values"] = []
        for filename, blob in files:
            pending_uploads.append((data, filename, blob))
    elif typ == ComponentType.RADIO_GROUP:
        if not supplied:
            if required:
                raise SetupError(f"Required modal control {custom_id!r} was not supplied")
            value = None
        if value is not None:
            if not isinstance(value, str):
                raise SetupError(f"RadioGroup {custom_id!r} expects a string")
            options = {option["value"] for option in component.get("options") or []}
            if value not in options:
                raise SetupError(f"RadioGroup option {value!r} does not exist")
        data["value"] = value
    elif typ == ComponentType.CHECKBOX_GROUP:
        chosen = [] if not supplied else _as_values(value)
        lo, hi = _modal_bounds(component, default_min=0, default_max=10)
        _ensure_unique(chosen)
        if required and not chosen:
            raise SetupError(f"Required modal control {custom_id!r} was not supplied")
        if (supplied or required) and not lo <= len(chosen) <= hi:
            raise SetupError(f"Modal control {custom_id!r} expects between {lo} and {hi} values")
        if not all(isinstance(item, str) for item in chosen):
            raise SetupError(f"CheckboxGroup {custom_id!r} expects string values")
        options = {option["value"] for option in component.get("options") or []}
        unknown = [item for item in chosen if item not in options]
        if unknown:
            raise SetupError(f"CheckboxGroup option {unknown[0]!r} does not exist")
        data["values"] = chosen
    elif typ == ComponentType.CHECKBOX:
        value = component.get("default", False) if not supplied else value
        if not isinstance(value, bool):
            raise SetupError(f"Checkbox {custom_id!r} expects a bool")
        data["value"] = value
    else:
        raise SetupError(f"Unsupported modal component type {typ}")
    return data


def _modal_submit_nodes(
    nodes: list[dict[str, Any]],
    actor: Any,
    values: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    channel_id: int,
    pending_uploads: list[tuple[dict[str, Any], str, bytes]],
) -> list[dict[str, Any]]:
    out = []
    for node in nodes:
        typ = node.get("type")
        if typ == ComponentType.TEXT_DISPLAY:
            out.append({"type": typ, "id": node["id"]})
        elif typ == ComponentType.ACTION_ROW:
            children = _modal_submit_nodes(
                node.get("components") or [], actor, values, resolved, channel_id, pending_uploads
            )
            out.append({"type": typ, "id": node["id"], "components": children})
        elif typ == ComponentType.LABEL:
            child = node.get("component")
            assert isinstance(child, dict)
            children = _modal_submit_nodes([child], actor, values, resolved, channel_id, pending_uploads)
            out.append({"type": typ, "id": node["id"], "component": children[0]})
        elif typ in {
            ComponentType.TEXT_INPUT,
            *SELECT_TYPES,
            ComponentType.FILE_UPLOAD,
            ComponentType.RADIO_GROUP,
            ComponentType.CHECKBOX_GROUP,
            ComponentType.CHECKBOX,
        }:
            out.append(_modal_leaf(actor, node, values, resolved, channel_id, pending_uploads))
    return out


def _commit_modal_uploads(
    actor: Any,
    channel_id: int,
    pending_uploads: list[tuple[dict[str, Any], str, bytes]],
    resolved: dict[str, dict[str, Any]],
) -> None:
    backend = actor._env.backend
    for data, filename, blob in pending_uploads:
        attachment_id = backend.snowflake()
        attachment = backend.cdn.store_attachment(attachment_id, channel_id, filename, blob, None)
        resolved.setdefault("attachments", {})[str(attachment_id)] = attachment
        data["values"].append(str(attachment_id))


async def _submit_modal(actor: Any, shown: InteractionResult, values: dict[str, Any]) -> InteractionResult:
    spec = shown.modal
    if spec is None:
        raise SetupError("That interaction did not respond with a modal")
    if not isinstance(values, dict):
        raise SetupError("Modal values must be a dict keyed by custom_id")
    try:
        spec = validate_modal(spec)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    channel_id = shown._interaction.channel_id
    _check_user_dm_channel(actor, channel_id)
    controls = _modal_control_map(spec)
    unknown = set(values) - set(controls)
    if unknown:
        raise SetupError(f"Unknown modal custom_id {sorted(unknown)[0]!r}")
    resolved: dict[str, dict[str, Any]] = {}
    pending_uploads: list[tuple[dict[str, Any], str, bytes]] = []
    components = _modal_submit_nodes(spec["components"], actor, values, resolved, channel_id, pending_uploads)
    backend = actor._env.backend
    source_message_id = shown._interaction.source_message_id
    extra = None
    if source_message_id is not None:
        source = backend.get_message(channel_id, source_message_id)
        extra = {"message": dict(serializers.message_payload(backend, source))}
    channel = ChannelHandle(actor._env, getattr(actor, "guild", None), backend.get_channel(channel_id))
    _commit_modal_uploads(actor, channel_id, pending_uploads, resolved)
    data: dict[str, Any] = {"custom_id": spec["custom_id"], "components": components}
    if resolved:
        data["resolved"] = resolved
    return await _dispatch_actor_interaction(
        actor,
        InteractionType.MODAL_SUBMIT,
        channel,
        data,
        extra=extra,
        source_message_id=source_message_id,
    )


async def _component_interaction(actor: Any, stored: Any, data: dict[str, Any]) -> InteractionResult:
    backend = actor._env.backend
    channel = ChannelHandle(actor._env, getattr(actor, "guild", None), backend.get_channel(stored.channel_id))
    return await _dispatch_actor_interaction(
        actor,
        InteractionType.MESSAGE_COMPONENT,
        channel,
        data,
        extra={"message": dict(serializers.message_payload(backend, stored))},
        source_message_id=stored.id,
    )


async def _dispatch_actor_interaction(
    actor: Any,
    type: int,
    channel: ChannelHandle,
    data: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
    source_message_id: int | None = None,
) -> InteractionResult:
    backend = actor._env.backend
    guild = getattr(actor, "guild", None)
    guild_id = guild.id if guild is not None else None
    record, payload = _interactions.base_payload(
        backend,
        type=type,
        channel_id=channel.id,
        guild_id=guild_id,
        user_id=actor.id,
        data=data,
    )
    if extra:
        payload.update(extra)
    if source_message_id is not None:
        record.source_message_id = source_message_id
    backend.emit("INTERACTION_CREATE", payload)
    await actor._env._settle_internal(dispatch="INTERACTION_CREATE")
    return InteractionResult(actor._env, record)


def _guard_actor_operation(method: Any) -> Any:
    @wraps(method)
    async def guarded(self: MemberActor, *args: Any, **kwargs: Any) -> Any:
        token = self._env._begin_operation(method.__name__)
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._env._end_operation(token)

    return guarded


for _operation_name in (
    "send",
    "edit",
    "delete",
    "typing",
    "react",
    "unreact",
    "send_dm",
    "slash",
    "context_menu",
    "autocomplete",
    "click",
    "select",
    "submit_modal",
    "vote",
    "remove_vote",
    "join_voice",
    "leave_voice",
    "set_voice",
    "subscribe_event",
    "unsubscribe_event",
):
    setattr(MemberActor, _operation_name, _guard_actor_operation(getattr(MemberActor, _operation_name)))
