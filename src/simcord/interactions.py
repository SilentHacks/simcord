"""Builders for INTERACTION_CREATE gateway payloads.

These produce the same wire shapes Discord sends: typed option values with
``resolved`` objects, member context with computed permission snapshots, and
subcommand/group nesting — so discord.py's real option parsing, transformers,
namespaces, and checks all execute.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from .backend import Backend, serializers
from .backend.errors import OptionError, SetupError
from .backend.models import Interaction
from .enums import AppCommandType, InteractionType, OptionType

if TYPE_CHECKING:
    from .actors import MemberActor
    from .builders import UserHandle

_SUBCOMMAND_TYPES = (OptionType.SUBCOMMAND, OptionType.SUBCOMMAND_GROUP)
# Option types that carry snowflake values resolved out-of-band.
_SNOWFLAKE_TYPES = (OptionType.USER, OptionType.CHANNEL, OptionType.ROLE, OptionType.MENTIONABLE)
_INTEGER_LIMIT = 2**53 - 1
_NUMBER_LIMIT = 2**53
_INTEGER_INPUT = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_FILE_TYPE_GROUPS = {
    "image": {".png", ".gif", ".jpg", ".jpeg", ".jfif", ".webp", ".avif"},
    "video": {".mp4", ".mov", ".qt", ".webm"},
    "audio": {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac"},
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
    if int(type) == int(InteractionType.APPLICATION_COMMAND):
        record.command_name = data.get("name") if isinstance(data.get("name"), str) else None
        try:
            record.command_type = int(data.get("type", AppCommandType.CHAT_INPUT))
            if data.get("target_id") is not None:
                record.target_id = int(data["target_id"])
                record.target_type = record.command_type
                if record.command_type == AppCommandType.MESSAGE:
                    resolved = data.get("resolved") or {}
                    target = (resolved.get("messages") or {}).get(str(record.target_id)) or {}
                    if target.get("channel_id") is not None:
                        record.target_channel_id = int(target["channel_id"])
        except (TypeError, ValueError):
            record.command_type = None
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
        "context": 0 if guild_id is not None else 1,
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


def command_leaves(command: dict[str, Any]) -> list[tuple[list[str], dict[str, Any]]]:
    """Flatten a chat-input command into leaf invocation paths and specs."""
    root_name = command["name"]
    leaves: list[tuple[list[str], dict[str, Any]]] = []

    def visit(node: dict[str, Any], path: list[str]) -> None:
        children = [option for option in node.get("options") or [] if option.get("type") in _SUBCOMMAND_TYPES]
        if not children:
            leaves.append((path, node))
            return
        for child in children:
            visit(child, [*path, child["name"]])

    visit(command, [root_name])
    return leaves


def resolve_handle(
    backend: Backend, value: Any, resolved: dict[str, dict[str, Any]], *, user_id: int
) -> None:
    """Add a single handle's ``resolved`` payload, by kind, into ``resolved``.

    Shared by slash-command option building and entity-select interactions so
    the ``users``/``members``/``roles``/``channels`` payload shapes have a single
    source of truth. ``user_id`` is the acting user, used to stamp the per-channel
    ``permissions`` field Discord includes on resolved channels.
    """
    from .actors import MemberActor
    from .builders import ChannelHandle, RoleHandle, UserHandle

    wire_id = str(value.id)
    if isinstance(value, (MemberActor, UserHandle)):
        resolved.setdefault("users", {})[wire_id] = dict(serializers.user_payload(backend.get_user(value.id)))
        if isinstance(value, MemberActor):
            guild = backend.get_guild(value.guild.id)
            resolved.setdefault("members", {})[wire_id] = dict(
                serializers.member_payload(backend, guild, guild.members[value.id], with_user=False)
            )
    elif isinstance(value, RoleHandle):
        resolved.setdefault("roles", {})[wire_id] = dict(serializers.role_payload(value._role))
    elif isinstance(value, ChannelHandle):
        channel = backend.get_channel(value.id)
        payload = dict(serializers.channel_payload(backend, channel))
        # Resolved channels carry the acting user's permissions in that channel,
        # which AppCommandChannel requires.
        if channel.guild_id is not None:
            payload["permissions"] = str(backend.compute_permissions(channel.guild_id, user_id, channel.id))
        resolved.setdefault("channels", {})[wire_id] = payload
    else:
        raise SetupError(f"Don't know how to resolve {type(value).__name__} into interaction data")


def _check_snowflake_handle(command_name: str, name: str, option_type: int, value: Any) -> None:
    """Reject a handle whose kind cannot fill this snowflake option type.

    Resolving dispatches on the value's Python type, so a mismatch (e.g. a role
    handle in a ``USER`` option) would otherwise resolve into the wrong bucket
    and fail deep inside discord.py. Catch it here, at the call site, instead.
    """
    from .actors import MemberActor
    from .builders import ChannelHandle, RoleHandle, UserHandle

    user = (MemberActor, UserHandle)
    allowed: dict[int, tuple[type, ...]] = {
        OptionType.USER: user,
        OptionType.CHANNEL: (ChannelHandle,),
        OptionType.ROLE: (RoleHandle,),
        OptionType.MENTIONABLE: (*user, RoleHandle),
    }
    expected = allowed[OptionType(option_type)]
    if not isinstance(value, expected):
        names = " or ".join(t.__name__ for t in expected)
        raise OptionError(
            "option-entity",
            name,
            f"Option '{name}' of '{command_name}' expects {names}, got {type(value).__name__}",
        )


def _option_error(code: str, command_name: str, option: dict[str, Any], message: str) -> OptionError:
    name = option["name"]
    return OptionError(code, name, f"Option '{name}' of '{command_name}' {message}")


def validate_option_value(command_name: str, option: dict[str, Any], value: Any) -> Any:
    """Validate one option value against its Discord option definition."""
    name = option["name"]
    option_type = OptionType(option["type"])

    if option_type == OptionType.STRING:
        if not isinstance(value, str):
            raise _option_error(
                "option-type", command_name, option, f"expects str, got {type(value).__name__}"
            )
        length = len(value)
        if "min_length" in option and length < option["min_length"]:
            raise _option_error(
                "option-length",
                command_name,
                option,
                f"must be at least {option['min_length']} characters, got {length}",
            )
        if "max_length" in option and length > option["max_length"]:
            raise _option_error(
                "option-length",
                command_name,
                option,
                f"must be at most {option['max_length']} characters, got {length}",
            )
    elif option_type == OptionType.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _option_error(
                "option-type", command_name, option, f"expects int, got {type(value).__name__}"
            )
        if abs(value) > _INTEGER_LIMIT:
            raise _option_error(
                "option-integer-range",
                command_name,
                option,
                f"must be between -{_INTEGER_LIMIT} and {_INTEGER_LIMIT}, got {value}",
            )
        _check_numeric_range(command_name, option, value)
    elif option_type == OptionType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _option_error(
                "option-type", command_name, option, f"expects a number, got {type(value).__name__}"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise _option_error(
                "option-type", command_name, option, f"expects a finite number, got {value!r}"
            )
        if abs(value) > _NUMBER_LIMIT:
            raise _option_error(
                "option-integer-range",
                command_name,
                option,
                f"must be between -{_NUMBER_LIMIT} and {_NUMBER_LIMIT}, got {value}",
            )
        _check_numeric_range(command_name, option, value)
    elif option_type == OptionType.BOOLEAN:
        if not isinstance(value, bool):
            raise _option_error(
                "option-type", command_name, option, f"expects bool, got {type(value).__name__}"
            )
    elif option_type in _SNOWFLAKE_TYPES:
        _check_snowflake_handle(command_name, name, option_type, value)
        if option_type == OptionType.CHANNEL and option.get("channel_types"):
            allowed = option["channel_types"]
            channel_type = value._channel.type
            if channel_type not in allowed:
                raise _option_error(
                    "option-channel-type",
                    command_name,
                    option,
                    f"only allows channel types {allowed}, got {channel_type}",
                )
    elif option_type == OptionType.ATTACHMENT:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], str)
            or not isinstance(value[1], bytes | bytearray)
        ):
            raise _option_error(
                "option-type",
                command_name,
                option,
                "expects a (filename: str, data: bytes) tuple",
            )
        filename, data = value
        file_types = option.get("file_types") or []
        if len(file_types) > 10 or any(
            not isinstance(file_type, str)
            or not (
                file_type.casefold() in _FILE_TYPE_GROUPS
                or (file_type.startswith(".") and len(file_type) > 1)
            )
            for file_type in file_types
        ):
            raise _option_error("option-file-type", command_name, option, "has an invalid file_types filter")
        if file_types:
            basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
            suffix = "." + basename.rsplit(".", 1)[-1].casefold() if "." in basename else ""
            allowed_extensions = set()
            for file_type in file_types:
                normalized = file_type.casefold()
                allowed_extensions.update(_FILE_TYPE_GROUPS.get(normalized, {normalized}))
            if suffix not in allowed_extensions:
                raise _option_error(
                    "option-file-type",
                    command_name,
                    option,
                    f"does not allow file extension {suffix or '(none)'!r}",
                )
        return filename, bytes(data)
    else:
        raise _option_error("option-type", command_name, option, f"has unsupported option type {option_type}")

    choices = option.get("choices")
    if choices and len(choices) > 25:
        raise _option_error("option-choice", command_name, option, "cannot declare more than 25 choices")
    if (
        choices
        and option_type in (OptionType.STRING, OptionType.INTEGER, OptionType.NUMBER)
        and not option.get("autocomplete")
    ):
        allowed_choices = [choice["value"] for choice in choices]
        if value not in allowed_choices:
            raise _option_error(
                "option-choice",
                command_name,
                option,
                f"only allows {allowed_choices}, got {value!r}",
            )
    return value


def _check_numeric_range(command_name: str, option: dict[str, Any], value: int | float) -> None:
    if "min_value" in option and value < option["min_value"]:
        raise _option_error(
            "option-range",
            command_name,
            option,
            f"must be ≥ {option['min_value']}, got {value}",
        )
    if "max_value" in option and value > option["max_value"]:
        raise _option_error(
            "option-range",
            command_name,
            option,
            f"must be ≤ {option['max_value']}, got {value}",
        )


def parse_option_input(command_name: str, option: dict[str, Any], raw: Any) -> Any:
    """Parse a browser wire value into its typed Python form, then validate it."""
    option_type = OptionType(option["type"])
    name = option["name"]
    if option_type == OptionType.STRING:
        if not isinstance(raw, str):
            raise _option_error("option-type", command_name, option, "expects a string input")
        value: Any = raw
    elif option_type == OptionType.INTEGER:
        if not isinstance(raw, str) or _INTEGER_INPUT.fullmatch(raw) is None:
            raise _option_error(
                "option-type", command_name, option, "expects a canonical decimal integer string"
            )
        digits = raw[1:] if raw.startswith("-") else raw
        if len(digits) > 16:
            raise _option_error(
                "option-integer-range",
                command_name,
                option,
                f"must be between -{_INTEGER_LIMIT} and {_INTEGER_LIMIT}",
            )
        value = int(raw)
    elif option_type == OptionType.NUMBER:
        if not isinstance(raw, str):
            raise _option_error("option-type", command_name, option, "expects a decimal string")
        try:
            value = float(raw)
        except ValueError:
            raise _option_error("option-type", command_name, option, "expects a decimal string") from None
        if not math.isfinite(value):
            raise _option_error("option-type", command_name, option, "expects a finite decimal string")
    elif option_type == OptionType.BOOLEAN:
        if not isinstance(raw, bool):
            raise _option_error("option-type", command_name, option, "expects a JSON boolean")
        value = raw
    else:
        raise OptionError(
            "option-type", name, f"Option '{name}' of '{command_name}' cannot be parsed from browser input"
        )
    return validate_option_value(command_name, option, value)


def check_options(
    command_name: str,
    spec: dict[str, Any],
    provided: dict[str, Any],
    *,
    partial: bool = False,
) -> dict[str, Any]:
    """Return validated provided values, optionally dropping invalid partial values."""
    options = spec.get("options") or []
    if any(option.get("type") in _SUBCOMMAND_TYPES for option in options):
        raise OptionError("command-not-leaf", None, f"Command '{command_name}' still has subcommand options")
    declared = {option["name"]: option for option in options}
    for name in provided:
        if name not in declared:
            error = OptionError("option-unknown", name, f"Command '{command_name}' has no option '{name}'")
            error.add_note(f"Declared options: {sorted(declared)}")
            raise error

    accepted: dict[str, Any] = {}
    for name, value in provided.items():
        try:
            accepted[name] = validate_option_value(command_name, declared[name], value)
        except OptionError:
            if not partial:
                raise
    if not partial:
        for name, option in declared.items():
            if option.get("required") and name not in provided:
                raise OptionError(
                    "option-required", name, f"Command '{command_name}' requires option '{name}'"
                )
    return accepted


def store_interaction_attachment(
    backend: Backend,
    channel_id: int,
    filename: str,
    data: bytes,
    resolved: dict[str, Any],
) -> str:
    """Store an interaction upload in the fake CDN and add it to ``resolved``."""
    attachment_id = backend.snowflake()
    attachment = backend.cdn.store_attachment(attachment_id, channel_id, filename, data, None)
    resolved.setdefault("attachments", {})[str(attachment_id)] = attachment
    return str(attachment_id)


def build_options(
    actor: MemberActor | UserHandle,
    command_name: str,
    spec: dict[str, Any],
    provided: dict[str, Any],
    *,
    partial: bool = False,
    channel_id: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the wire `options` array and `resolved` block from python values."""
    backend = actor._env.backend
    accepted = check_options(command_name, spec, provided, partial=partial)
    declared = {option["name"]: option for option in (spec.get("options") or [])}
    built: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {}

    for name, value in accepted.items():
        option = declared[name]
        option_type = option["type"]
        if option_type in _SNOWFLAKE_TYPES:
            wire_value = str(value.id)
            resolve_handle(backend, value, resolved, user_id=actor.id)
        elif option_type == OptionType.ATTACHMENT:
            if partial:
                continue
            if channel_id is None:
                raise SetupError("channel_id is required to upload an interaction attachment")
            filename, data = value
            wire_value = store_interaction_attachment(backend, channel_id, filename, data, resolved)
        else:
            wire_value = value
        built.append({"name": name, "type": option_type, "value": wire_value})
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
