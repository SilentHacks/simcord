"""Shared helpers for route handlers."""

from __future__ import annotations

import re
from typing import Any

from .. import intents
from .._payload import detach
from ..backend import errors, serializers
from ..backend.cdn import sticker_url
from ..backend.models import (
    AllowedMentions,
    Interaction,
    Message,
    MessageSticker,
    Poll,
    PollAnswer,
)
from ..components import (
    ComponentValidationError,
    check_attachment_references,
    resolve_attachment_references,
)
from ..enums import AppCommandType, MessageType
from ._access import require_text_access
from .router import RequestContext

_CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_]+):(\d+)>")


def poll_from_wire(backend: Any, wire: dict[str, Any]) -> Poll:
    """Build a :class:`Poll` from the create payload discord.py sends."""
    question = (wire.get("question") or {}).get("text") or ""
    if not 1 <= len(question) <= 300:
        raise errors.invalid_form_body(
            errors={
                "poll": {
                    "question": {
                        "text": {
                            "_errors": [
                                {
                                    "code": "BASE_TYPE_BAD_LENGTH",
                                    "message": "Must be between 1 and 300 in length.",
                                }
                            ]
                        }
                    }
                }
            }
        )
    duration_hours = float(wire.get("duration", 24))
    if not 1 <= duration_hours <= 768:
        maximum = duration_hours > 768
        raise errors.invalid_form_body(
            errors={
                "poll": {
                    "duration": {
                        "_errors": [
                            {
                                "code": "NUMBER_TYPE_MAX" if maximum else "NUMBER_TYPE_MIN",
                                "message": (
                                    "int32 value should be less than or equal to 768."
                                    if maximum
                                    else "int32 value should be greater than or equal to 1."
                                ),
                            }
                        ]
                    }
                }
            }
        )
    answers = []
    for index, answer in enumerate(wire.get("answers") or [], start=1):
        media = answer.get("poll_media") or {}
        text = media.get("text")
        if isinstance(text, str) and len(text) > 55:
            raise errors.invalid_form_body(
                errors={
                    "proto_data": {
                        "poll": {
                            "answers": {
                                str(index - 1): {
                                    "_errors": [
                                        {
                                            "code": "POLL_ANSWER_TEXT_INVALID_LENGTH",
                                            "message": "Poll answer text cannot exceed 55 characters",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            )
        emoji = media.get("emoji")
        emoji_str = None
        if isinstance(emoji, dict):
            emoji_str = f"{emoji.get('name')}:{emoji['id']}" if emoji.get("id") else emoji.get("name")
        answers.append(PollAnswer(answer_id=index, text=media.get("text"), emoji=emoji_str))
    return Poll(
        question=question,
        answers=answers,
        expiry=backend.iso_after(duration_hours * 3600),
        allow_multiselect=bool(wire.get("allow_multiselect")),
        layout_type=int(wire.get("layout_type", 1)),
    )


# Message bodies have one strict source of truth: all modelled fields are
# validated and persisted; upload attachment metadata is rebuilt from files.
_MESSAGE_HANDLED = (
    "content",
    "embed",
    "embeds",
    "components",
    "flags",
    "message_reference",
    "poll",
    "sticker_ids",
    "tts",
    "allowed_mentions",
)
_MESSAGE_IGNORED = ("nonce", "enforce_nonce", "attachments")

# ``Webhook.send`` adds fields that a plain channel send never carries, so the
# webhook-execute route classifies them explicitly rather than letting them fall
# through to the bare "unmodelled key" path:
#   * ``avatar_url`` — an incoming webhook's per-message avatar override is
#     modelled when supplied through the incoming-webhook execute route.
_WEBHOOK_IGNORED: tuple[str, ...] = ()
_WEBHOOK_REJECTED = {
    "thread_name": "simcord does not model creating a forum thread via webhook offline.",
    "applied_tags": "simcord does not model creating a forum thread via webhook offline.",
}


def _allowed_mentions(value: Any) -> AllowedMentions:
    if value is None:
        return AllowedMentions()
    if not isinstance(value, dict) or value.keys() - {"parse", "users", "roles", "replied_user"}:
        raise errors.invalid_form_body(
            "allowed_mentions must contain only parse, users, roles, and replied_user"
        )
    parse = value.get("parse", [])
    if not isinstance(parse, list) or any(
        not isinstance(item, str) or item not in {"everyone", "users", "roles"} for item in parse
    ):
        raise errors.invalid_form_body("allowed_mentions.parse must contain mention types")
    if len(set(parse)) != len(parse):
        raise errors.invalid_form_body("allowed_mentions.parse entries must be unique")

    def ids(name: str) -> frozenset[int]:
        raw_ids = value.get(name, [])
        if not isinstance(raw_ids, list):
            raise errors.invalid_form_body(f"allowed_mentions.{name} must be an array")
        parsed: list[int] = []
        for raw_id in raw_ids:
            if isinstance(raw_id, bool):
                raise errors.invalid_form_body(f"allowed_mentions.{name} entries must be snowflakes")
            if isinstance(raw_id, int) and raw_id > 0:
                parsed.append(raw_id)
            elif isinstance(raw_id, str) and raw_id.isascii() and raw_id.isdecimal():
                parsed_id = int(raw_id)
                if parsed_id > 0:
                    parsed.append(parsed_id)
                else:
                    raise errors.invalid_form_body(f"allowed_mentions.{name} entries must be snowflakes")
            else:
                raise errors.invalid_form_body(f"allowed_mentions.{name} entries must be snowflakes")
        if len(set(parsed)) != len(parsed):
            raise errors.invalid_form_body(f"allowed_mentions.{name} entries must be unique")
        return frozenset(parsed)

    for name in ("users", "roles"):
        if name in parse and name in value:
            raise errors.invalid_form_body(
                errors={
                    "allowed_mentions": {
                        "_errors": [
                            {
                                "code": "MESSAGE_ALLOWED_MENTIONS_PARSE_EXCLUSIVE",
                                "message": f'parse:["{name}"] and {name}: [ids...] are mutually exclusive.',
                            }
                        ]
                    }
                }
            )
    replied_user = value.get("replied_user", False)
    if not isinstance(replied_user, bool):
        raise errors.invalid_form_body("allowed_mentions.replied_user must be a boolean")
    return AllowedMentions(
        everyone="everyone" in parse,
        users=None if "users" in parse else ids("users"),
        roles=None if "roles" in parse else ids("roles"),
        replied_user=replied_user,
    )


def _message_stickers(
    ctx: RequestContext, channel_id: int, raw_ids: Any, *, ordinary_send: bool = False
) -> list[MessageSticker]:
    if not isinstance(raw_ids, list):
        raise errors.invalid_form_body("sticker_ids must be an array")
    if len(raw_ids) > 3:
        raise errors.invalid_form_body("sticker_ids: Must be 3 or fewer in length")
    if len(set(str(item) for item in raw_ids)) != len(raw_ids):
        raise errors.invalid_form_body("sticker_ids entries must be unique")
    channel = ctx.backend.get_channel(channel_id)
    items: list[MessageSticker] = []
    for raw_id in raw_ids:
        if isinstance(raw_id, bool):
            raise errors.invalid_form_body("sticker_ids entries must be snowflakes")
        if isinstance(raw_id, int) and raw_id > 0:
            sticker_id = raw_id
        elif isinstance(raw_id, str) and raw_id.isascii() and raw_id.isdecimal():
            sticker_id = int(raw_id)
            if sticker_id <= 0:
                raise errors.invalid_form_body("sticker_ids entries must be snowflakes")
        else:
            raise errors.invalid_form_body("sticker_ids entries must be snowflakes")
        sticker = next(
            (
                guild.stickers[sticker_id]
                for guild in ctx.backend.guilds.values()
                if sticker_id in guild.stickers
            ),
            None,
        )
        if sticker is None:
            if ordinary_send:
                raise errors.BackendError(400, 50081, "Invalid sticker sent")
            raise errors.unknown_sticker()
        if not sticker.available:
            raise errors.invalid_form_body(f"sticker {sticker_id} is unavailable")
        if sticker.guild_id != channel.guild_id:
            if channel.guild_id is None:
                raise errors.missing_permissions()
            ctx.backend.require_permissions(
                channel.guild_id,
                ctx.backend.bot_user.id,
                channel_id,
                "use_external_stickers",
            )
        items.append(
            MessageSticker(
                sticker.id,
                sticker.name,
                sticker.format_type,
                sticker.guild_id,
                sticker.url or sticker_url(sticker.id, sticker.format_type),
            )
        )
    return items


def bot_message(
    ctx: RequestContext,
    channel_id: int,
    *,
    author_id: int | None = None,
    interaction: Interaction | None = None,
    webhook_id: int | None = None,
    body: dict[str, Any] | None = None,
    webhook_execute: bool = False,
    pending: bool = False,
) -> Message:
    """Prepare a complete message before storing uploads or publishing it."""
    ctx.backend.get_channel(channel_id)
    prepared = prepare_bot_message(
        ctx,
        channel_id,
        author_id=author_id,
        interaction=interaction,
        webhook_id=webhook_id,
        body=body,
        webhook_execute=webhook_execute,
        pending=pending,
    )
    return commit_bot_message(ctx, channel_id, prepared)


def prepare_bot_message(
    ctx: RequestContext,
    channel_id: int,
    *,
    author_id: int | None = None,
    interaction: Interaction | None = None,
    webhook_id: int | None = None,
    body: dict[str, Any] | None = None,
    webhook_execute: bool = False,
    pending: bool = False,
    ordinary_send: bool = False,
) -> tuple[Message, list[bytes]]:
    """Validate all fields and read all uploads without changing backend state."""
    backend = ctx.backend
    channel = backend.get_channel(channel_id)
    degradation_kinds: list[str] = []
    handled = _MESSAGE_HANDLED
    ignore = _MESSAGE_IGNORED
    reject: dict[str, str] = {}
    apply_identity = webhook_execute and webhook_id is not None
    if webhook_execute:
        reject = {**_WEBHOOK_REJECTED, "sticker_ids": "Webhook.send does not support stickers offline."}
        handled = tuple(field for field in _MESSAGE_HANDLED if field not in reject)
        if apply_identity:
            handled = (*handled, "username", "avatar_url")
        ignore = (*_MESSAGE_IGNORED, *_WEBHOOK_IGNORED) + (
            () if apply_identity else ("username", "avatar_url")
        )
    body = ctx.fields(
        *handled,
        ignore=ignore,
        reject=reject,
        body=ctx.body() if body is None else body,
    )
    if ordinary_send and isinstance(body.get("content"), str) and len(body["content"]) > 2000:
        raise errors.invalid_form_body(
            errors={
                "content": {
                    "_errors": [
                        {
                            "code": "BASE_TYPE_MAX_LENGTH",
                            "message": "Must be 2000 or fewer in length.",
                        }
                    ]
                }
            }
        )
    if ordinary_send and isinstance(body.get("content"), str) and channel.guild_id is not None:
        if any(
            guild.id != channel.guild_id and int(match[2]) in guild.emojis
            for match in _CUSTOM_EMOJI.finditer(body["content"])
            for guild in backend.guilds.values()
        ) and not backend.has_permission_flags(
            channel.guild_id, backend.bot_user.id, channel_id, "external_emojis"
        ):

            def strip_external(match: re.Match[str]) -> str:
                emoji_id = int(match[2])
                if any(
                    guild.id != channel.guild_id and emoji_id in guild.emojis
                    for guild in backend.guilds.values()
                ):
                    return f":{match[1]}:"
                return match[0]

            content = _CUSTOM_EMOJI.sub(strip_external, body["content"])
            if content != body["content"]:
                body["content"] = content
                degradation_kinds.append("external_emoji_permission_stripped")
    author_name = body.get("username") if apply_identity else None
    author_avatar = body.get("avatar_url") if apply_identity else None
    try:
        flags = int(body.get("flags") or 0)
    except (TypeError, ValueError) as exc:
        raise errors.invalid_form_body("flags must be an integer") from exc
    tts = body.get("tts", False)
    if not isinstance(tts, bool):
        raise errors.invalid_form_body("tts must be a boolean")
    if ordinary_send:
        if flags & (1 << 6):
            flags &= ~(1 << 6)
            degradation_kinds.append("normal_message_ephemeral_ignored")
        if (
            tts
            and channel.guild_id is not None
            and not backend.has_permission_flags(
                channel.guild_id, backend.bot_user.id, channel_id, "send_tts_messages"
            )
        ):
            tts = False
            degradation_kinds.append("tts_permission_dropped")
    sticker_items = _message_stickers(
        ctx, channel_id, body.get("sticker_ids", []), ordinary_send=ordinary_send
    )
    mention_policy = _allowed_mentions(body.get("allowed_mentions"))
    interaction_metadata = None
    message_type = None
    if interaction is not None:
        interaction_metadata = {
            "id": str(interaction.id),
            "type": interaction.type,
            "user": serializers.user_payload(backend.users[interaction.user_id]),
            "authorizing_integration_owners": {},
        }
        if interaction.command_name is not None:
            interaction_metadata["name"] = interaction.command_name
        if interaction.command_type is not None:
            interaction_metadata["command_type"] = interaction.command_type
        if interaction.target_id is not None:
            interaction_metadata["target_id"] = interaction.target_id
            interaction_metadata["target_type"] = interaction.target_type
        if interaction.target_channel_id is not None:
            interaction_metadata["target_channel_id"] = str(interaction.target_channel_id)
        if interaction.command_type == AppCommandType.CHAT_INPUT:
            message_type = MessageType.CHAT_INPUT_COMMAND
        elif interaction.command_type in {AppCommandType.USER, AppCommandType.MESSAGE}:
            message_type = MessageType.CONTEXT_MENU_COMMAND
    reference = backend.normalize_message_reference(channel_id, body.get("message_reference"))
    if ordinary_send and reference is not None and reference["type"] == 0:
        ctx.require_channel_permissions(channel_id, "read_message_history", denied_code=160002)
    if reference is not None and reference["type"] == 1:
        if ordinary_send:
            require_text_access(ctx, int(reference["channel_id"]))
        source = backend._forward_source(
            channel_id, author_id if author_id is not None else backend.bot_user.id, reference
        )
        if not ctx.message_content_access and not intents._content_exempt(
            serializers.message_payload(backend, source), backend.bot_user.id, backend
        ):
            raise errors.BackendError(
                400, 160014, "You cannot forward a message whose content you cannot read."
            )
    embeds = body.get("embeds") if "embeds" in body else ([body["embed"]] if body.get("embed") else [])
    if embeds is None:
        embeds = []
    poll = poll_from_wire(backend, body["poll"]) if body.get("poll") else None
    uploads = _preview_uploads(ctx)
    if ordinary_send:
        for present, permission in (
            (bool(embeds), "embed_links"),
            (bool(ctx.files), "attach_files"),
            (poll is not None, "send_polls"),
        ):
            if present:
                ctx.require_channel_permissions(channel_id, permission)
    components = body.get("components") if body.get("components") is not None else []
    fields = {
        "author_id": author_id if author_id is not None else backend.bot_user.id,
        "content": body.get("content"),
        "embeds": embeds,
        "components": components,
        "stickers": sticker_items,
        "flags": flags,
        "tts": tts,
        "allowed_mentions": mention_policy,
        "reference": reference,
        "interaction_metadata": interaction_metadata,
        "webhook_id": webhook_id,
        "author_name": author_name,
        "author_avatar": author_avatar,
        "poll": poll,
        "message_type": message_type,
    }
    try:
        message = backend.prepare_message(channel_id, attachments=uploads, **fields)
    except errors.BackendError as exc:
        if ordinary_send and exc.code == 50035 and "Cannot send an empty message" in exc.message:
            raise errors.BackendError(400, 50006, "Cannot send an empty message") from exc
        raise
    try:
        check_attachment_references(message.components, uploads)
    except (ComponentValidationError, TypeError, ValueError) as exc:
        raise errors.invalid_form_body(str(exc)) from exc
    upload_data = [file.fp.read() for file in ctx.files]
    if ordinary_send:
        if channel.guild_id is not None:
            guild = backend.get_guild(channel.guild_id)
            if any(
                mention_policy.allows_role(role_id)
                and (role := guild.roles.get(role_id)) is not None
                and not role.mentionable
                and role_id not in message.ping_role_ids
                for role_id in message.mention_role_ids
            ):
                degradation_kinds.append("unmentionable_role_ping_dropped")
        message.mention_user_ids = list(message.ping_user_ids)
        message.mention_role_ids = list(message.ping_role_ids)
        for kind in degradation_kinds:
            backend.record_degradation(kind, channel_id=channel_id)
    return message, upload_data


def commit_bot_message(
    ctx: RequestContext,
    channel_id: int,
    prepared: tuple[Message, list[bytes]],
    *,
    broadcast: bool | None = None,
) -> Message:
    message, upload_data = prepared
    if message.channel_id != channel_id or len(ctx.files) != len(upload_data):
        raise errors.invalid_form_body("Prepared message does not match destination or uploads")
    staged = [
        (
            ctx.backend.cdn.prepare_attachment(
                ctx.backend.snowflake(), channel_id, file.filename, data, file.description
            ),
            data,
        )
        for file, data in zip(ctx.files, upload_data, strict=True)
    ]
    attachments = [attachment for attachment, _ in staged]
    message.attachments = attachments
    message.components = resolve_attachment_references(message.components, attachments, owned=True)
    ctx.backend.cdn.install_attachments(staged)
    return ctx.backend.commit_message(
        message, broadcast=not message.is_ephemeral if broadcast is None else broadcast
    )


def _preview_uploads(ctx: RequestContext) -> list[dict[str, Any]]:
    return [
        {
            "id": str(index),
            "filename": file.filename,
            "description": file.description,
            "size": 0,
            "url": f"https://cdn.simcord.invalid/pending/{index}",
            "proxy_url": f"https://cdn.simcord.invalid/pending/{index}",
            "content_type": "application/octet-stream",
        }
        for index, file in enumerate(ctx.files)
    ]


def edit_bot_message(ctx: RequestContext, message: Message, *, body: dict[str, Any] | None = None) -> Message:
    """Prepare all ingress and uploads, then commit an existing message edit."""
    fields = ctx.fields(
        "content",
        "embeds",
        "components",
        "attachments",
        "flags",
        "allowed_mentions",
        "poll",
        ignore=("tts",),
        body=ctx.body() if body is None else body,
    )
    if "allowed_mentions" in fields or "content" in fields:
        fields["allowed_mentions"] = _allowed_mentions(fields.get("allowed_mentions"))
    if "poll" in fields:
        if not message.flags & (1 << 7) or message.interaction_metadata is None:
            raise errors.invalid_form_body("polls can only be added when completing a deferred response")
        if not isinstance(fields["poll"], dict):
            raise errors.invalid_form_body("poll must be an object")
        try:
            fields["poll"] = poll_from_wire(ctx.backend, fields["poll"])
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise errors.invalid_form_body("invalid poll") from exc
    current_attachments = message.attachments
    preview_uploads = _preview_uploads(ctx)
    upload_indices: list[int] = []
    selected_indices: list[int | None] = []
    if "attachments" in fields:
        declared = fields["attachments"]
        if declared is None:
            declared = []
        if not isinstance(declared, list):
            raise errors.invalid_form_body("attachments must be an array")
        existing = {str(item.get("id")): item for item in current_attachments}
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in declared:
            if not isinstance(item, dict):
                raise errors.invalid_form_body("attachments entries must be objects")
            key = str(item.get("id"))
            if key in seen:
                raise errors.invalid_form_body(f"attachments: duplicate id {key}")
            seen.add(key)
            if key in existing:
                retained = dict(existing[key])
                retained.update({k: item[k] for k in ("filename", "description") if k in item})
                selected.append(retained)
                selected_indices.append(None)
                continue
            try:
                index = int(key)
            except (TypeError, ValueError):
                raise errors.invalid_form_body(f"attachments: unknown attachment id {key}") from None
            if index < 0 or index >= len(preview_uploads):
                raise errors.invalid_form_body(f"attachments: unknown attachment id {key}")
            selected.append(preview_uploads[index])
            selected_indices.append(index)
            upload_indices.append(index)
        fields["attachments"] = selected
    elif ctx.files:
        fields["attachments"] = current_attachments + preview_uploads
        selected_indices = [None] * len(current_attachments) + list(range(len(preview_uploads)))
        upload_indices = list(range(len(preview_uploads)))

    prepared = ctx.backend.prepare_message_edit(
        message.channel_id,
        message.id,
        fields,
        attachment_bindings=fields.get("attachments", current_attachments),
    )
    updates = prepared.fields

    if upload_indices:
        upload_data = {index: ctx.files[index].fp.read() for index in dict.fromkeys(upload_indices)}
        staged = {
            index: (
                ctx.backend.cdn.prepare_attachment(
                    ctx.backend.snowflake(),
                    message.channel_id,
                    ctx.files[index].filename,
                    data,
                    ctx.files[index].description,
                ),
                data,
            )
            for index, data in upload_data.items()
        }
        stored_by_index = {index: attachment for index, (attachment, _) in staged.items()}
        updates["attachments"] = [
            stored_by_index[index] if index is not None else attachment
            for index, attachment in zip(selected_indices, updates["attachments"], strict=True)
        ]
    if updates["components"] is not message.components:
        try:
            updates["components"] = resolve_attachment_references(
                updates["components"], updates["attachments"], owned=True
            )
        except ComponentValidationError as exc:
            raise errors.invalid_form_body(str(exc)) from exc
    if upload_indices:
        ctx.backend.cdn.install_attachments(list(staged.values()))
    return ctx.backend.commit_message_edit(prepared)


def message_response(
    ctx: RequestContext, message: Message, *, for_user: int | None = None, exempt_root: bool = False
) -> dict[str, Any]:
    payload = detach(dict(serializers.message_payload(ctx.backend, message, for_user=for_user)))
    if not ctx.message_content_access:
        intents.censor_message(payload, ctx.backend.bot_user.id, backend=ctx.backend, exempt_root=exempt_root)
    return payload
