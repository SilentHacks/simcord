"""Builders for INTERACTION_CREATE gateway payloads.

These produce the same wire shapes Discord sends: typed option values with
``resolved`` objects, member context with computed permission snapshots, and
subcommand/group nesting — so discord.py's real option parsing, transformers,
namespaces, and checks all execute.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .backend import Backend, serializers
from .backend.errors import SetupError
from .backend.models import Interaction
from .enums import OptionType

if TYPE_CHECKING:
    from .actors import MemberActor

_SUBCOMMAND_TYPES = (OptionType.SUBCOMMAND, OptionType.SUBCOMMAND_GROUP)
# Option types that carry snowflake values resolved out-of-band.
_SNOWFLAKE_TYPES = (OptionType.USER, OptionType.CHANNEL, OptionType.ROLE, OptionType.MENTIONABLE)

_SCALAR_TYPES: dict[OptionType, Any] = {
    OptionType.STRING: str,
    OptionType.INTEGER: int,
    OptionType.BOOLEAN: bool,
    OptionType.NUMBER: (int, float),
}


def base_payload(
    backend: Backend,
    *,
    type: int,
    channel_id: int,
    guild_id: int | None,
    user_id: int,
    data: dict[str, Any],
) -> tuple[Interaction, dict[str, Any]]:
    """Create an interaction record and its INTERACTION_CREATE payload."""
    record = backend.new_interaction(type, channel_id, user_id, guild_id)
    channel = backend.get_channel(channel_id)
    payload: dict[str, Any] = {
        "id": str(record.id),
        "application_id": str(backend.application_id),
        "type": type,
        "token": record.token,
        "version": 1,
        "data": data,
        "channel_id": str(channel_id),
        "channel": dict(serializers.channel_payload(backend, channel)),
        "locale": "en-US",
        "entitlements": [],
        "authorizing_integration_owners": {},
        "context": 0,
        "attachment_size_limit": 26214400,
    }
    if guild_id is not None:
        guild = backend.get_guild(guild_id)
        member = dict(serializers.member_payload(backend, guild, guild.members[user_id]))
        member["permissions"] = str(backend.compute_permissions(guild_id, user_id, channel_id))
        payload["guild_id"] = str(guild_id)
        payload["member"] = member
        payload["guild_locale"] = "en-US"
        payload["app_permissions"] = str(
            backend.compute_permissions(guild_id, backend.bot_user.id, channel_id)
        )
    else:
        payload["user"] = dict(serializers.user_payload(backend.get_user(user_id)))
        payload["app_permissions"] = "0"
    return record, payload


def walk_to_subcommand(command: dict[str, Any], path: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Resolve 'parent group sub' paths down the command's option tree.

    Returns the leaf (sub)command spec and the path of nesting names below the root.
    """
    node = command
    nesting: list[str] = []
    for name in path:
        options = node.get("options") or []
        child = next(
            (o for o in options if o.get("type") in _SUBCOMMAND_TYPES and o["name"] == name),
            None,
        )
        if child is None:
            error = SetupError(f"Command '{command['name']}' has no subcommand path {' '.join(path)!r}")
            available = [o["name"] for o in options if o.get("type") in _SUBCOMMAND_TYPES]
            error.add_note(f"Available here: {available}")
            raise error
        node = child
        nesting.append(name)
    return node, nesting


def build_options(
    actor: MemberActor,
    command_name: str,
    spec: dict[str, Any],
    provided: dict[str, Any],
    *,
    partial: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the wire `options` array and `resolved` block from python values."""
    from .actors import MemberActor as _MemberActor
    from .builders import ChannelHandle, RoleHandle, UserHandle

    backend = actor._env.backend
    declared = {o["name"]: o for o in (spec.get("options") or []) if o.get("type") not in _SUBCOMMAND_TYPES}
    built: list[dict[str, Any]] = []
    resolved: dict[str, dict[str, Any]] = {}

    for name, value in provided.items():
        option = declared.get(name)
        if option is None:
            error = SetupError(f"Command '{command_name}' has no option '{name}'")
            error.add_note(f"Declared options: {sorted(declared)}")
            raise error
        option_type = option["type"]
        wire_value: Any
        if option_type in _SNOWFLAKE_TYPES:
            wire_value = str(value.id)
            if option_type in (OptionType.USER, OptionType.MENTIONABLE) and isinstance(
                value, (_MemberActor, UserHandle)
            ):
                resolved.setdefault("users", {})[wire_value] = dict(
                    serializers.user_payload(backend.get_user(value.id))
                )
                if isinstance(value, _MemberActor):
                    guild = backend.get_guild(value.guild.id)
                    resolved.setdefault("members", {})[wire_value] = dict(
                        serializers.member_payload(backend, guild, guild.members[value.id], with_user=False)
                    )
            elif option_type == OptionType.CHANNEL and isinstance(value, ChannelHandle):
                resolved.setdefault("channels", {})[wire_value] = dict(
                    serializers.channel_payload(backend, backend.get_channel(value.id))
                )
            elif option_type in (OptionType.ROLE, OptionType.MENTIONABLE) and isinstance(value, RoleHandle):
                resolved.setdefault("roles", {})[wire_value] = dict(serializers.role_payload(value._role))
        else:
            expected = _SCALAR_TYPES.get(option_type)
            if expected is not None and not isinstance(value, expected):
                raise SetupError(
                    f"Option '{name}' of '{command_name}' expects {expected}, got {type(value).__name__}"
                )
            choices = option.get("choices")
            if choices and value not in [c["value"] for c in choices]:
                raise SetupError(
                    f"Option '{name}' of '{command_name}' only allows {[c['value'] for c in choices]}, got {value!r}"
                )
            wire_value = value
        built.append({"name": name, "type": option_type, "value": wire_value})

    if not partial:  # autocomplete fires before all required options are filled
        for name, option in declared.items():
            if option.get("required") and name not in provided:
                raise SetupError(f"Command '{command_name}' requires option '{name}'")
    return built, resolved


def nest_options(
    command: dict[str, Any], nesting: list[str], leaf_options: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Wrap leaf options in subcommand/group layers, innermost-out."""
    options = leaf_options
    node = command
    wrappers = []
    for name in nesting:
        child = next(o for o in (node.get("options") or []) if o["name"] == name)
        wrappers.append({"name": name, "type": child["type"]})
        node = child
    for wrapper in reversed(wrappers):
        options = [{**wrapper, "options": options}]
    return options
