"""Shared helpers for route handlers."""

from __future__ import annotations

from typing import Any

from ..backend import errors, serializers
from ..backend.models import EPHEMERAL_FLAG, Interaction, Message, Poll, PollAnswer
from ..components import ComponentValidationError, resolve_attachment_references, validate_message_state
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


# discord.py's message-create payload (channel send, webhook execute, interaction
# response) is read through ``ctx.fields`` like every edit, so an unrecognised key
# fails loudly instead of being silently dropped:
#   * handled  — keys simcord models into the stored message
#   * ignored  — keys Discord accepts but that have no offline meaning (the bot
#                never speaks; nonces don't dedupe; mentions are derived from
#                content; the ``attachments`` metadata is rebuilt from the files)
#   * rejected — a real discord.py feature simcord does not model, refused with a
#                reason so a sticker send fails loudly rather than vanishing
_MESSAGE_HANDLED = ("content", "embed", "embeds", "components", "flags", "message_reference", "poll")
_MESSAGE_IGNORED = ("tts", "nonce", "enforce_nonce", "allowed_mentions", "attachments")
_MESSAGE_REJECTED = {"sticker_ids": "simcord does not model stickers on messages offline."}

# ``Webhook.send`` adds fields that a plain channel send never carries, so the
# webhook-execute route classifies them explicitly rather than letting them fall
# through to the bare "unmodelled key" path:
#   * ``username``   — an *incoming* webhook's per-message display-name override is
#                      modelled (applied as ``author_name``); an *application*
#                      webhook (interaction followup) is keyed differently — Discord
#                      ignores username/avatar there — so it is accepted-and-ignored.
#   * ``avatar_url`` — accepted-and-ignored: simcord models no avatars for any user,
#                      so there is nothing to apply (and nothing silently faked).
#   * forum-via-webhook (``thread_name``/``applied_tags``) is a real feature simcord
#     does not model, so it is rejected loudly with a reason.
_WEBHOOK_IGNORED = ("avatar_url",)
_WEBHOOK_REJECTED = {
    "thread_name": "simcord does not model creating a forum thread via webhook offline.",
    "applied_tags": "simcord does not model creating a forum thread via webhook offline.",
}


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
    """Prepare a complete message before storing uploads or publishing it."""
    ctx.backend.get_channel(channel_id)
    prepared = prepare_bot_message(
        ctx,
        author_id=author_id,
        interaction=interaction,
        webhook_id=webhook_id,
        body=body,
        webhook_execute=webhook_execute,
    )
    return commit_bot_message(ctx, channel_id, prepared)


def prepare_bot_message(
    ctx: RequestContext,
    *,
    author_id: int | None = None,
    interaction: Interaction | None = None,
    webhook_id: int | None = None,
    body: dict[str, Any] | None = None,
    webhook_execute: bool = False,
) -> tuple[dict[str, Any], list[bytes]]:
    """Validate all fields and read all uploads without changing backend state."""
    backend = ctx.backend
    handled = _MESSAGE_HANDLED
    ignore = _MESSAGE_IGNORED
    reject = _MESSAGE_REJECTED
    apply_username = webhook_execute and webhook_id is not None
    if webhook_execute:
        reject = {**_MESSAGE_REJECTED, **_WEBHOOK_REJECTED}
        handled = (*_MESSAGE_HANDLED, "username") if apply_username else _MESSAGE_HANDLED
        ignore = (*_MESSAGE_IGNORED, *_WEBHOOK_IGNORED) + (() if apply_username else ("username",))
    body = ctx.fields(
        *handled,
        ignore=ignore,
        reject=reject,
        body=ctx.body() if body is None else body,
    )
    author_name = body.get("username") if apply_username else None
    try:
        flags = int(body.get("flags") or 0)
    except (TypeError, ValueError) as exc:
        raise errors.invalid_form_body("flags must be an integer") from exc
    interaction_metadata = None
    if interaction is not None:
        interaction_metadata = {
            "id": str(interaction.id),
            "type": interaction.type,
            "user": serializers.user_payload(backend.users[interaction.user_id]),
            "authorizing_integration_owners": {},
        }
    reference = body.get("message_reference")
    if reference:
        reference = {
            **({"channel_id": str(reference["channel_id"])} if "channel_id" in reference else {}),
            "message_id": str(reference["message_id"]),
        }
    embeds = body.get("embeds") if "embeds" in body else ([body["embed"]] if body.get("embed") else [])
    if embeds is None:
        embeds = []
    poll = poll_from_wire(backend, body["poll"]) if body.get("poll") else None
    uploads = _preview_uploads(ctx)
    try:
        components = validate_message_state(
            body.get("components") if body.get("components") is not None else [],
            flags=flags,
            content=body.get("content"),
            embeds=embeds,
            poll=poll,
            attachments=uploads,
            require_nonempty=True,
        )
        resolve_attachment_references(components, uploads)
    except (ComponentValidationError, TypeError, ValueError) as exc:
        raise errors.invalid_form_body(str(exc)) from exc
    fields = {
        "author_id": author_id if author_id is not None else backend.bot_user.id,
        "content": body.get("content"),
        "embeds": embeds,
        "components": components,
        "flags": flags,
        "reference": reference,
        "interaction_metadata": interaction_metadata,
        "webhook_id": webhook_id,
        "author_name": author_name,
        "poll": poll,
        "broadcast": not flags & EPHEMERAL_FLAG,
    }
    return fields, [file.fp.read() for file in ctx.files]


def commit_bot_message(
    ctx: RequestContext,
    channel_id: int,
    prepared: tuple[dict[str, Any], list[bytes]],
    *,
    broadcast: bool | None = None,
) -> Message:
    fields, upload_data = prepared
    attachments = [
        ctx.backend.cdn.store_attachment(
            ctx.backend.snowflake(), channel_id, file.filename, data, file.description
        )
        for file, data in zip(ctx.files, upload_data, strict=True)
    ]
    fields = {
        **fields,
        "attachments": attachments,
        "components": resolve_attachment_references(fields["components"], attachments),
    }
    if fields["reference"] is not None:
        fields["reference"] = {"channel_id": str(channel_id), **fields["reference"]}
    if broadcast is not None:
        fields["broadcast"] = broadcast
    return ctx.backend.create_message(channel_id, **fields)


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


def _store_upload(ctx: RequestContext, channel_id: int, index: int, data: bytes) -> dict[str, Any]:
    file = ctx.files[index]
    return ctx.backend.cdn.store_attachment(
        ctx.backend.snowflake(), channel_id, file.filename, data, file.description
    )


def _validate_edit_state(
    message: Message | None,
    fields: dict[str, Any],
    components: list[dict[str, Any]],
    *,
    flags: int,
) -> list[dict[str, Any]]:
    current_content = message.content if message is not None else None
    current_embeds = message.embeds if message is not None else []
    try:
        return validate_message_state(
            components,
            flags=flags,
            content=fields.get("content", current_content),
            embeds=fields.get("embeds", current_embeds)
            if fields.get("embeds", current_embeds) is not None
            else [],
            poll=message.poll if message is not None else None,
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
        ignore=("allowed_mentions", "tts"),
        body=ctx.body() if body is None else body,
    )
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
    effective_components = fields.get("components", current_components)
    if effective_components is None:
        effective_components = []
    preview_attachments = fields.get("attachments", current_attachments)
    try:
        flags = (
            int(fields["flags"])
            if "flags" in fields and fields["flags"] is not None
            else (message.flags if message is not None else 0)
        )
    except (TypeError, ValueError) as exc:
        raise errors.invalid_form_body("flags must be an integer") from exc
    effective_components = _validate_edit_state(message, fields, effective_components, flags=flags)
    try:
        resolve_attachment_references(effective_components, preview_attachments)
    except ComponentValidationError as exc:
        raise errors.invalid_form_body(str(exc)) from exc
    if "components" in fields:
        fields["components"] = effective_components

    if upload_indices:
        upload_data = {index: ctx.files[index].fp.read() for index in dict.fromkeys(upload_indices)}
        stored_by_index = {
            index: _store_upload(ctx, message.channel_id if message is not None else 0, index, data)
            for index, data in upload_data.items()
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
