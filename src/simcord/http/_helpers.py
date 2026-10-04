"""Shared helpers for route handlers."""

from __future__ import annotations

from typing import Any

from ..backend import errors, serializers
from ..backend.cdn import sticker_url
from ..backend.models import (
    EPHEMERAL_FLAG,
    AllowedMentions,
    Interaction,
    Message,
    MessageSticker,
    Poll,
    PollAnswer,
)
from ..components import ComponentValidationError, resolve_attachment_references, validate_message_state
from ..enums import AppCommandType, MessageType
from .router import RequestContext


def poll_from_wire(backend: Any, wire: dict[str, Any]) -> Poll:
    """Build a :class:`Poll` from the create payload discord.py sends."""
    answers = []
    for index, answer in enumerate(wire.get("answers") or [], start=1):
        media = answer.get("poll_media") or {}
        emoji = media.get("emoji")
        emoji_str = None
        if isinstance(emoji, dict):
            emoji_str = f"{emoji.get('name')}:{emoji['id']}" if emoji.get("id") else emoji.get("name")
        answers.append(PollAnswer(answer_id=index, text=media.get("text"), emoji=emoji_str))
    duration_hours = float(wire.get("duration") or 24)
    return Poll(
        question=(wire.get("question") or {}).get("text") or "",
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

    if ("users" in parse and "users" in value) or ("roles" in parse and "roles" in value):
        raise errors.invalid_form_body("allowed_mentions cannot mix parse and explicit IDs of one type")
    replied_user = value.get("replied_user", False)
    if not isinstance(replied_user, bool):
        raise errors.invalid_form_body("allowed_mentions.replied_user must be a boolean")
    return AllowedMentions(
        everyone="everyone" in parse,
        users=None if "users" in parse else ids("users"),
        roles=None if "roles" in parse else ids("roles"),
        replied_user=replied_user,
    )


def _message_stickers(ctx: RequestContext, channel_id: int, raw_ids: Any) -> list[MessageSticker]:
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
) -> Message:
    """Create a message from a request body, authored by the bot or a webhook."""
    backend = ctx.backend
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
    author_name = body.get("username") if apply_identity else None
    author_avatar = body.get("avatar_url") if apply_identity else None
    try:
        flags = int(body.get("flags") or 0)
    except (TypeError, ValueError) as exc:
        raise errors.invalid_form_body("flags must be an integer") from exc
    tts = body.get("tts", False)
    if not isinstance(tts, bool):
        raise errors.invalid_form_body("tts must be a boolean")
    sticker_items = _message_stickers(ctx, channel_id, body.get("sticker_ids", []))
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
    reference = body.get("message_reference")
    if reference:
        reference = {
            "channel_id": str(reference.get("channel_id", channel_id)),
            "message_id": str(reference["message_id"]),
        }
    embeds = body.get("embeds") or ([body["embed"]] if body.get("embed") else [])
    _validate_embeds(embeds)
    poll = poll_from_wire(backend, body["poll"]) if body.get("poll") else None
    components = body.get("components") or []
    try:
        preview_components = resolve_attachment_references(components, _preview_uploads(ctx))
        validate_message_state(
            preview_components,
            flags=flags,
            content=body.get("content"),
            embeds=embeds,
            poll=poll,
            stickers=sticker_items,
        )
    except (ComponentValidationError, TypeError, ValueError) as exc:
        detail = exc if isinstance(exc, ComponentValidationError) else ComponentValidationError(str(exc))
        raise errors.invalid_form_body(str(detail)) from exc
    uploads = ctx.store_files(channel_id)
    try:
        components = resolve_attachment_references(components, uploads)
    except ComponentValidationError as exc:
        raise errors.invalid_form_body(str(exc)) from exc
    return backend.create_message(
        channel_id,
        author_id if author_id is not None else backend.bot_user.id,
        body.get("content"),
        embeds=embeds,
        components=components,
        attachments=uploads,
        stickers=sticker_items,
        flags=flags,
        tts=tts,
        allowed_mentions=mention_policy,
        reference=reference,
        interaction_metadata=interaction_metadata,
        webhook_id=webhook_id,
        author_name=author_name,
        author_avatar=author_avatar,
        poll=poll,
        message_type=message_type,
        broadcast=not flags & EPHEMERAL_FLAG,
    )


def _validate_embeds(embeds: list[dict[str, Any]]) -> None:
    if len(embeds) > 10:
        raise errors.invalid_form_body("embeds: Must be 10 or fewer in length")
    for embed in embeds:
        total = len(embed.get("title") or "") + len(embed.get("description") or "")
        for fld in embed.get("fields") or []:
            total += len(fld.get("name") or "") + len(fld.get("value") or "")
        total += len((embed.get("footer") or {}).get("text") or "")
        total += len((embed.get("author") or {}).get("name") or "")
        if total > 6000:
            raise errors.invalid_form_body("embeds: total size of embeds exceeds 6000 characters")


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


def _store_upload(ctx: RequestContext, channel_id: int, index: int) -> dict[str, Any]:
    file = ctx.files[index]
    return ctx.backend.cdn.store_attachment(
        ctx.backend.snowflake(), channel_id, file.filename, file.fp.read(), file.description
    )


def _validate_edit_state(
    message: Message | None,
    fields: dict[str, Any],
    components: list[dict[str, Any]],
    *,
    flags: int,
) -> None:
    current_content = message.content if message is not None else None
    current_embeds = message.embeds if message is not None else []
    try:
        _validate_embeds(fields.get("embeds", current_embeds) or [])
        validate_message_state(
            components,
            flags=flags,
            content=fields.get("content", current_content),
            embeds=fields.get("embeds", current_embeds) or [],
            poll=message.poll if message is not None else None,
            stickers=message.stickers if message is not None else None,
            previous_flags=message.flags if message is not None else 0,
        )
    except (ComponentValidationError, TypeError, ValueError) as exc:
        detail = exc if isinstance(exc, ComponentValidationError) else ComponentValidationError(str(exc))
        raise errors.invalid_form_body(str(detail)) from exc


def message_edit_changes(
    ctx: RequestContext, message: Message | None = None, *, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return an atomically applicable webhook-message edit payload."""
    fields = ctx.fields(
        "content",
        "embeds",
        "components",
        "attachments",
        "flags",
        "allowed_mentions",
        ignore=("tts",),
        body=ctx.body() if body is None else body,
    )
    if "allowed_mentions" in fields:
        fields["allowed_mentions"] = _allowed_mentions(fields["allowed_mentions"])
    current_attachments = list(message.attachments) if message is not None else []
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

    current_components = message.components if message is not None else []
    effective_components = fields.get("components", current_components) or []
    preview_attachments = fields.get("attachments", current_attachments)
    try:
        preview_components = resolve_attachment_references(effective_components, preview_attachments)
    except ComponentValidationError as exc:
        raise errors.invalid_form_body(str(exc)) from exc
    try:
        flags = (
            int(fields["flags"])
            if "flags" in fields and fields["flags"] is not None
            else (message.flags if message is not None else 0)
        )
    except (TypeError, ValueError) as exc:
        raise errors.invalid_form_body("flags must be an integer") from exc
    _validate_edit_state(message, fields, preview_components, flags=flags)

    if upload_indices:
        stored_by_index = {
            index: _store_upload(ctx, message.channel_id if message is not None else 0, index)
            for index in set(upload_indices)
        }
        fields["attachments"] = [
            stored_by_index[index] if index is not None else attachment
            for index, attachment in zip(selected_indices, fields["attachments"], strict=True)
        ]
    if "components" in fields:
        try:
            fields["components"] = resolve_attachment_references(
                fields["components"] or [], fields.get("attachments", current_attachments)
            )
        except ComponentValidationError as exc:
            raise errors.invalid_form_body(str(exc)) from exc
    return fields


def message_response(ctx: RequestContext, message: Message) -> dict[str, Any]:
    return dict(serializers.message_payload(ctx.backend, message))
