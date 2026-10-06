"""Authorized message presentation and markdown context."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from ..backend.access import can_access_channel, can_access_message
from ..backend.cdn import sticker_url
from ..backend.errors import BackendError
from ..backend.models import EPHEMERAL_FLAG, Message
from ..components import COMPONENTS_V2_FLAG, walk_components
from ..enums import AppCommandType, ComponentType, InteractionType, MessageType
from ._component_projection import (
    _asset_available,
    _attachment,
    _decorate_components,
    _decorate_markdown_emoji,
    _embed_projection,
    _known_emoji,
    _project_emoji,
)
from ._identity import _identity_wire, resolve_identity
from ._markdown import markdown_tokens

if TYPE_CHECKING:
    from ..env import Env
    from . import Preview
    from ._pages import _Page


def _message_day(timestamp: str, timezone: str) -> date | None:
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(ZoneInfo(timezone)).date()
    except (ValueError, OSError):
        return None


def _is_compact_message(previous: Message | None, message: Message, timezone: str, env: Env) -> bool:
    if (
        previous is None
        or previous.author_id != message.author_id
        or previous.type != 0
        or message.type != 0
        or previous.reference
        or message.reference
        or previous.interaction_metadata
        or message.interaction_metadata
        or previous.flags & EPHEMERAL_FLAG
        or message.flags & EPHEMERAL_FLAG
    ):
        return False
    for item in (previous, message):
        thread = env.backend.channels.get(item.id)
        if thread is not None and thread.is_thread and thread.parent_id == item.channel_id:
            return False
    previous_day = _message_day(previous.timestamp, timezone)
    if previous_day is None or previous_day != _message_day(message.timestamp, timezone):
        return False
    try:
        first = datetime.fromisoformat(previous.timestamp.replace("Z", "+00:00"))
        second = datetime.fromisoformat(message.timestamp.replace("Z", "+00:00"))
        return 0 <= (second - first).total_seconds() < 7 * 60
    except ValueError:
        return False


def _message_type_info(value: int) -> dict[str, Any]:
    try:
        kind = MessageType(value)
    except ValueError:
        return {"kind": "unknown", "known": False}
    if kind == MessageType.DEFAULT:
        return {"kind": "default", "known": True}
    if kind == MessageType.REPLY:
        return {"kind": "reply", "known": True}
    if kind == MessageType.CHAT_INPUT_COMMAND:
        return {"kind": "application_command", "known": True}
    if kind == MessageType.CONTEXT_MENU_COMMAND:
        return {"kind": "context_menu_command", "known": True}
    return {"kind": "system", "known": True, "name": kind.name.lower()}


def _sticker_projection(page: _Page, sticker: Any) -> dict[str, Any]:
    suffix, content_type = {
        1: ("png", "image/png"),
        2: ("png", "image/png"),
        3: ("json", "application/json"),
        4: ("gif", "image/gif"),
    }[sticker.format_type]
    asset_id = page.asset_id(
        f"sticker:{sticker.guild_id}:{sticker.id}",
        {
            "url": sticker.url or sticker_url(sticker.id, sticker.format_type),
            "filename": f"{sticker.id}.{suffix}",
            "content_type": content_type,
        },
        source=("sticker", sticker.guild_id, sticker.id),
    )
    return {
        "id": str(sticker.id),
        "name": sticker.name,
        "format_type": sticker.format_type,
        "asset_id": asset_id,
        "available": _asset_available(page, asset_id),
    }


def _discord_message_link(guild_id: int, channel_id: int, message_id: int | None = None) -> str:
    path = f"https://discord.com/channels/{guild_id}/{channel_id}"
    return f"{path}/{message_id}" if message_id is not None else path


def _has_channel_permission(preview: Preview, page: _Page, channel: Any, permission: str) -> bool:
    if channel.guild_id is None:
        return False
    try:
        preview.env.backend.require_permissions(channel.guild_id, page.viewer.id, channel.id, permission)
    except BackendError:
        return False
    return True


def _can_send_message(preview: Preview, page: _Page, channel: Any) -> bool:
    if channel.guild_id is None:
        return page.viewer.id in channel.recipient_ids
    permission = "send_messages_in_threads" if channel.is_thread else "send_messages"
    return _has_channel_permission(preview, page, channel, permission)


def _poll_expired(preview: Preview, poll: Any) -> bool:
    try:
        expiry = datetime.fromisoformat(poll.expiry.replace("Z", "+00:00"))
        now = datetime.fromisoformat(preview.env.backend.now_iso())
    except (AttributeError, TypeError, ValueError):
        return True
    return expiry <= now


def _message_allowed_actions(preview: Preview, page: _Page, channel: Any, message: Message) -> list[str]:
    actions: list[str] = []
    if page.layout == "channel":
        if _can_send_message(preview, page, channel):
            actions.append("reply")
        if message.author_id == page.viewer.id:
            actions.extend(("edit_message", "delete_message"))
        elif _has_channel_permission(preview, page, channel, "manage_messages"):
            actions.append("delete_message")
        if channel.guild_id is not None and _has_channel_permission(
            preview, page, channel, "manage_messages"
        ):
            actions.append("set_pinned")
    if channel.guild_id is None or _has_channel_permission(preview, page, channel, "add_reactions"):
        actions.append("set_reaction")
    poll = message.poll
    if poll is not None and not poll.finalized and not _poll_expired(preview, poll):
        actions.append("set_poll_votes")
    return actions


def _reaction_emoji_projection(page: _Page, emoji: str) -> dict[str, Any]:
    name, separator, emoji_id = emoji.partition(":")
    if not separator or not emoji_id.isdigit():
        return {"name": emoji}
    record = _known_emoji(page, emoji_id)
    if record is None:
        return {"name": name, "id": emoji_id, "custom": True, "available": False, "asset_id": None}
    return _project_emoji(
        page,
        {"name": record.name, "id": emoji_id, "animated": bool(record.animated)},
    )


def _system_projection(
    preview: Preview,
    page: _Page,
    message: Message,
    channel: Any,
    type_info: dict[str, Any],
    author: dict[str, Any],
) -> dict[str, Any]:
    env = preview.env
    message_kind = MessageType(int(message.type))
    icon = {
        MessageType.CHANNEL_NAME_CHANGE: "channel",
        MessageType.CHANNEL_ICON_CHANGE: "channel",
        MessageType.PINS_ADD: "pin",
        MessageType.NEW_MEMBER: "member",
        MessageType.RECIPIENT_ADD: "member",
        MessageType.RECIPIENT_REMOVE: "member",
        MessageType.THREAD_CREATED: "thread",
        MessageType.THREAD_STARTER_MESSAGE: "thread",
    }.get(message_kind, "system")
    system_text = message.content or ""
    if message_kind == MessageType.CHANNEL_NAME_CHANGE:
        system_text = f"changed this channel's name: {system_text}."
    elif message_kind == MessageType.THREAD_CREATED:
        system_text = f"started a thread: {system_text}."
    system: dict[str, Any] = {
        "kind": type_info["name"],
        "icon": icon,
        "text": system_text,
        "text_tokens": markdown_tokens(system_text, "system"),
        "author": author,
    }
    metadata = message.system_metadata
    if metadata is not None:
        if metadata.recipient_id is not None and _user_allowed(preview, page, metadata.recipient_id):
            system["recipient"] = _identity_wire(resolve_identity(preview, page, metadata.recipient_id))
        if metadata.channel_id is not None:
            try:
                target_channel = env.backend.get_channel(metadata.channel_id)
            except BackendError:
                target_channel = None
            if (
                target_channel is not None
                and target_channel.guild_id == channel.guild_id
                and target_channel.guild_id is not None
                and can_access_channel(env, target_channel.id, page.viewer, history=True)
            ):
                system["channel"] = {
                    "id": str(target_channel.id),
                    "name": target_channel.name or str(target_channel.id),
                    "url": _discord_message_link(target_channel.guild_id, target_channel.id),
                }
        if metadata.referenced_message_id is not None:
            reference_channel_id = metadata.referenced_channel_id or message.channel_id
            try:
                referenced = env.backend.get_message(reference_channel_id, metadata.referenced_message_id)
            except BackendError:
                referenced = None
            if referenced is not None and can_access_message(
                env, reference_channel_id, referenced, page.viewer, history=True
            ):
                try:
                    reference_channel = env.backend.get_channel(reference_channel_id)
                except BackendError:
                    reference_channel = None
                if (
                    reference_channel is not None
                    and reference_channel.guild_id is not None
                    and reference_channel.guild_id == channel.guild_id
                ):
                    page.referenced_messages.add((reference_channel_id, referenced.id))
                    system["reference"] = {
                        "id": str(referenced.id),
                        "author": _identity_wire(
                            resolve_identity(
                                preview,
                                page,
                                referenced.author_id,
                                message=referenced,
                                override=referenced.author_name,
                            )
                        ),
                        "url": _discord_message_link(
                            reference_channel.guild_id, reference_channel_id, referenced.id
                        ),
                    }
    return system


def _reply_projection(preview: Preview, page: _Page, message: Message) -> dict[str, Any] | None:
    env = preview.env
    reference = message.reference
    if reference:
        try:
            reference_message_id = reference.get("message_id")
            if reference_message_id is None:
                raise TypeError("reference message id is missing")
            reference_id = int(reference_message_id)
            reference_channel = reference.get("channel_id", message.channel_id)
            if reference_channel is None:
                raise TypeError("reference channel id is missing")
            reference_channel_id = int(reference_channel)
            referenced = env.backend.get_message(reference_channel_id, reference_id)
        except (BackendError, TypeError, ValueError):
            referenced = None
        if referenced is not None and can_access_message(
            env, reference_channel_id, referenced, page.viewer, history=True
        ):
            page.referenced_messages.add((reference_channel_id, referenced.id))
            referenced_identity = _identity_wire(
                resolve_identity(
                    preview,
                    page,
                    referenced.author_id,
                    message=referenced,
                    override=referenced.author_name,
                )
            )
            reply_channel = env.backend.get_channel(referenced.channel_id)
            reply_context = _markdown_context(preview, page, referenced, reply_channel)
            return {
                "state": "resolved",
                "message_id": str(referenced.id),
                "channel_id": str(referenced.channel_id),
                "channel_name": (
                    env.backend.get_channel(referenced.channel_id).name
                    if referenced.channel_id != message.channel_id
                    else None
                ),
                "author": referenced_identity,
                "excerpt_tokens": _decorate_markdown_emoji(
                    markdown_tokens(referenced.content[:100], "message", context=reply_context),
                    page,
                ),
                "preview_kind": "message",
            }
    return None


def _message_texts(message: Message | None, content: str) -> list[str]:
    texts = [message.content or ""] if message is not None else [content]
    if message is not None:
        for embed in message.embeds:
            for key in ("title", "description"):
                if isinstance(embed.get(key), str):
                    texts.append(embed[key])
            for owner, key in (("author", "name"), ("footer", "text")):
                value = embed.get(owner)
                if isinstance(value, dict) and isinstance(value.get(key), str):
                    texts.append(value[key])
            fields = embed.get("fields", [])
            if not isinstance(fields, list):
                continue
            for field in fields:
                if not isinstance(field, dict):
                    continue
                for key in ("name", "value"):
                    if isinstance(field.get(key), str):
                        texts.append(field[key])
        texts.extend(
            component["content"]
            for component in walk_components(message.components)
            if int(component.get("type", -1)) == int(ComponentType.TEXT_DISPLAY)
            and isinstance(component.get("content"), str)
        )
    return texts


def _message_projection(
    preview: Preview, page: _Page, message: Message, *, compact: bool, channel: Any
) -> dict[str, Any]:
    env = preview.env
    context = _markdown_context(preview, page, message, channel)
    type_info = _message_type_info(int(message.type))
    attachments = list(message.attachments)
    can_add_reactions = channel.guild_id is None or _has_channel_permission(
        preview, page, channel, "add_reactions"
    )
    author = _identity_wire(
        resolve_identity(preview, page, message.author_id, message=message, override=message.author_name)
    )
    data = {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "author": author,
        "author_ref": {"kind": author["kind"], "id": str(message.author_id)},
        "timestamp": message.timestamp,
        "edited_timestamp": message.edited_timestamp,
        "type": int(message.type),
        "type_info": type_info,
        "pinned": bool(message.pinned),
        "tts": bool(message.tts),
        "content": message.content,
        "content_tokens": (
            _decorate_markdown_emoji(markdown_tokens(message.content, "message", context=context), page)
            if type_info["kind"] not in {"system", "unknown"}
            else []
        ),
        "embeds": [_embed_projection(page, message, item, attachments, context) for item in message.embeds],
        "components": _decorate_components(preview, page, message, message.components, attachments, context),
        "flags": int(message.flags),
        "ephemeral": bool(message.flags & EPHEMERAL_FLAG),
        "components_v2": bool(message.flags & COMPONENTS_V2_FLAG),
        "compact": compact,
        "attachments": [_attachment(env, message, item, page) for item in attachments],
        "mention_user_ids": [
            str(uid) for uid in message.mention_user_ids if _user_allowed(preview, page, uid)
        ],
        "mention_role_ids": [
            str(rid) for rid in message.mention_role_ids if _role_allowed(preview, page, rid)
        ],
        "mention_everyone": bool(message.mention_everyone),
        "mentions": {"users": [], "roles": [], "channels": [], "everyone": bool(message.mention_everyone)},
        "reactions": [
            {
                "emoji": _reaction_emoji_projection(page, reaction.emoji),
                "count": len(reaction.user_ids),
                "viewer_reacted": page.viewer.id in reaction.user_ids,
                "can_toggle": can_add_reactions or page.viewer.id in reaction.user_ids,
            }
            for reaction in message.reactions
        ],
        "poll": None,
        "stickers": [_sticker_projection(page, sticker) for sticker in message.stickers],
        "thread": None,
        "system": None,
        "allowed_actions": _message_allowed_actions(preview, page, channel, message),
        "reply": {"state": "unavailable"},
        "interaction_header": None,
    }
    data["mentions"]["users"] = list(data["mention_user_ids"])
    if type_info["kind"] == "system":
        data["system"] = _system_projection(preview, page, message, channel, type_info, author)
    data["mentions"]["roles"] = list(data["mention_role_ids"])
    thread = env.backend.channels.get(message.id)
    if (
        thread is not None
        and thread.is_thread
        and thread.parent_id == message.channel_id
        and can_access_channel(env, thread.id, page.viewer, history=True)
    ):
        data["thread"] = {
            "id": str(thread.id),
            "name": thread.name or "",
            "message_count": thread.message_count,
            "archived": bool(thread.thread_metadata and thread.thread_metadata.archived),
        }
    metadata = message.interaction_metadata or {}
    try:
        is_application_command = int(metadata.get("type", -1)) == int(InteractionType.APPLICATION_COMMAND)
    except (TypeError, ValueError):
        is_application_command = False
    if is_application_command:
        try:
            command_type = AppCommandType(int(metadata["command_type"]))
        except (KeyError, TypeError, ValueError):
            command_type = None
        interaction_header: dict[str, Any] = {
            "kind": (
                "context_menu_command"
                if type_info["kind"] == "context_menu_command"
                else "application_command"
            )
        }
        if isinstance(metadata.get("name"), str):
            interaction_header["name"] = metadata["name"]
        if command_type is not None:
            interaction_header["command_type"] = command_type.name.lower()
        user = metadata.get("user")
        if isinstance(user, Mapping):
            user_id = user.get("id")
            if isinstance(user_id, (int, str)) and not isinstance(user_id, bool):
                try:
                    invoker_id = int(user_id)
                except ValueError:
                    invoker_id = None
                if invoker_id is not None and _user_allowed(preview, page, invoker_id):
                    interaction_header["user"] = _identity_wire(resolve_identity(preview, page, invoker_id))
        target_id_value = metadata.get("target_id")
        target_type_value = metadata.get("target_type")
        try:
            target_id = int(target_id_value) if target_id_value is not None else None
            target_type = AppCommandType(int(target_type_value)) if target_type_value is not None else None
        except (TypeError, ValueError):
            target_id = None
            target_type = None
        if target_id is not None and target_type == AppCommandType.USER:
            if _user_allowed(preview, page, target_id):
                interaction_header["target_user"] = _identity_wire(resolve_identity(preview, page, target_id))
        elif target_id is not None and target_type == AppCommandType.MESSAGE:
            try:
                target_channel_id = int(metadata["target_channel_id"])
                target_message = env.backend.get_message(target_channel_id, target_id)
            except (BackendError, KeyError, TypeError, ValueError):
                target_message = None
                target_channel_id = 0
            if target_message is not None and can_access_message(
                env, target_channel_id, target_message, page.viewer, history=True
            ):
                try:
                    target_channel = env.backend.get_channel(target_channel_id)
                except BackendError:
                    target_channel = None
                if target_channel is not None and target_channel.guild_id is not None:
                    page.referenced_messages.add((target_channel_id, target_message.id))
                    interaction_header["target_message"] = {
                        "id": str(target_message.id),
                        "url": _discord_message_link(
                            target_channel.guild_id, target_channel_id, target_message.id
                        ),
                    }
        data["interaction_header"] = interaction_header
    if message.poll is not None:
        poll = message.poll
        total_votes = sum(len(voters) for voters in poll.votes.values())
        data["poll"] = {
            "question": poll.question,
            "answers": [
                {
                    "id": str(answer.answer_id),
                    "text": answer.text,
                    "emoji": _reaction_emoji_projection(page, answer.emoji) if answer.emoji else None,
                    "count": len(poll.votes.get(answer.answer_id, set())),
                    "percentage": (
                        round(len(poll.votes.get(answer.answer_id, set())) * 100 / total_votes)
                        if total_votes
                        else 0
                    ),
                    "viewer_selected": page.viewer.id in poll.votes.get(answer.answer_id, set()),
                }
                for answer in poll.answers
            ],
            "total_votes": total_votes,
            "expiry": poll.expiry,
            "expired": _poll_expired(preview, poll),
            "finalized": bool(poll.finalized),
            "multiselect": bool(poll.allow_multiselect),
            "layout_type": int(poll.layout_type),
        }
    data["mention_names"] = {}
    data["mention_entities"] = {}
    for uid in data["mention_user_ids"]:
        identity = _identity_wire(resolve_identity(preview, page, int(uid)))
        data["mention_names"][uid] = identity["name"]
        data["mention_entities"][uid] = identity
    data["mention_channel_ids"] = list(context["channel_ids"])
    data["mention_channel_names"] = dict(context["channels"])
    data["mentions"]["channels"] = list(context["channel_ids"])
    reply = _reply_projection(preview, page, message)
    if reply is not None:
        data["reply"] = reply
    return data


def _user_allowed(preview: Preview, page: _Page, user_id: int) -> bool:
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    if channel.guild_id is None:
        return user_id in channel.recipient_ids
    guild = env.backend.guilds.get(channel.guild_id)
    return guild is not None and user_id in guild.members


def _role_allowed(preview: Preview, page: _Page, role_id: int) -> bool:
    env = preview.env
    channel = env.backend.get_channel(page.channel_id)
    guild = env.backend.guilds.get(channel.guild_id) if channel.guild_id is not None else None
    return guild is not None and role_id in guild.roles and role_id != guild.id


def _identity_name(
    preview: Preview,
    page: _Page,
    user_id: int,
    *,
    message: Message | None = None,
    override: str | None = None,
) -> str:
    user = preview.env.backend.get_user(user_id)
    channel_id = message.channel_id if message is not None else page.channel_id
    try:
        channel = preview.env.backend.get_channel(channel_id)
    except BackendError:
        channel = None
    guild = (
        preview.env.backend.guilds.get(channel.guild_id)
        if channel is not None and channel.guild_id is not None
        else None
    )
    member = guild.members.get(user_id) if guild is not None else None
    return (
        override
        if override is not None
        else (member.nick if member is not None and member.nick else user.global_name or user.name)
    )


def _markdown_context(
    preview: Preview,
    page: _Page,
    message: Message | None,
    channel: Any,
    *,
    content: str = "",
) -> dict[str, Any]:
    backend = preview.env.backend
    texts = _message_texts(message, content)
    users: dict[str, str] = {}
    for text in texts:
        for match in re.finditer(r"<@!?([0-9]{1,20})>", text):
            user_id = int(match.group(1))
            if _user_allowed(preview, page, user_id):
                users[match.group(1)] = _identity_name(preview, page, user_id)

    roles: dict[str, str] = {}
    for text in texts:
        for match in re.finditer(r"<@&([0-9]{1,20})>", text):
            role_id = int(match.group(1))
            if _role_allowed(preview, page, role_id):
                guild = backend.guilds.get(channel.guild_id)
                if guild is not None and (role := guild.roles.get(role_id)) is not None:
                    roles[match.group(1)] = role.name

    channels: dict[str, str] = {}
    if channel.guild_id is not None:
        for text in texts:
            for match in re.finditer(r"<#([0-9]{1,20})>", text):
                channel_id = int(match.group(1))
                try:
                    mentioned = backend.get_channel(channel_id)
                except BackendError:
                    continue
                if mentioned.guild_id == channel.guild_id and can_access_channel(
                    preview.env, channel_id, page.viewer
                ):
                    channels[match.group(1)] = mentioned.name or match.group(1)

    emojis: dict[str, dict[str, Any]] = {}
    for text in texts:
        for match in re.finditer(r"<(a?):([A-Za-z0-9_]{2,32}):([0-9]{1,20})>", text):
            record = _known_emoji(page, match.group(3))
            if (
                record is not None
                and record.name == match.group(2)
                and bool(record.animated) == bool(match.group(1))
            ):
                emojis[match.group(3)] = {
                    "id": match.group(3),
                    "name": record.name,
                    "animated": bool(record.animated),
                    "custom": True,
                }

    commands: dict[str, str] = {}
    for scope in (channel.guild_id, None):
        for command in backend.commands.get(scope, {}).values():
            command_id = command.get("id")
            name = command.get("name")
            if (
                command_id is not None
                and isinstance(name, str)
                and int(command.get("type", AppCommandType.CHAT_INPUT)) == int(AppCommandType.CHAT_INPUT)
            ):
                commands.setdefault(str(command_id), name)

    return {
        "users": users,
        "roles": roles,
        "channels": channels,
        "channel_ids": list(channels),
        "emojis": emojis,
        "commands": commands,
        "everyone": bool(message.mention_everyone) if message is not None else False,
    }
