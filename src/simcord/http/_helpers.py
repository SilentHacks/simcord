"""Shared helpers for route handlers."""

from __future__ import annotations

from typing import Any

from ..backend import errors, serializers
from ..backend.models import EPHEMERAL_FLAG, Interaction, Message, Poll, PollAnswer
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
    """Create a message from a request body, authored by the bot (or a webhook user).

    ``body`` defaults to the request's JSON body, but callers (e.g. interaction
    callbacks, where the message payload is nested under ``data``) may pass an
    explicit body instead of mutating the shared request context. Either way the
    payload is vetted through :meth:`RequestContext.fields`, so an unmodelled key
    raises :class:`UnsupportedField` rather than being silently dropped.

    ``webhook_execute`` widens the field contract for the ``POST /webhooks`` route,
    whose ``Webhook.send`` payload carries ``username``/``avatar_url`` and the
    forum-thread fields a plain channel send never does (see ``_WEBHOOK_*``). An
    incoming webhook (``webhook_id`` set) honours the ``username`` override; an
    application webhook (interaction followup) does not, matching Discord.
    """
    backend = ctx.backend
    handled = _MESSAGE_HANDLED
    ignore = _MESSAGE_IGNORED
    reject = _MESSAGE_REJECTED
    # Only an incoming webhook honours a per-message identity; an application
    # webhook (interaction followup, keyed by token, no webhook_id) ignores it.
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
    flags = int(body.get("flags") or 0)
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
            "channel_id": str(reference.get("channel_id", channel_id)),
            "message_id": str(reference["message_id"]),
        }
    embeds = body.get("embeds") or ([body["embed"]] if body.get("embed") else [])
    _validate_embeds(embeds)
    poll = poll_from_wire(backend, body["poll"]) if body.get("poll") else None
    return backend.create_message(
        channel_id,
        author_id if author_id is not None else backend.bot_user.id,
        body.get("content") or "",
        embeds=embeds,
        components=body.get("components") or [],
        attachments=ctx.store_files(channel_id),
        flags=flags,
        reference=reference,
        interaction_metadata=interaction_metadata,
        webhook_id=webhook_id,
        author_name=author_name,
        poll=poll,
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


def message_edit_changes(ctx: RequestContext) -> dict[str, Any]:
    """The message fields an edit honours, vetted for parity.

    Shared by channel message edits and interaction response/followup edits,
    which are all webhook-message-shaped. ``allowed_mentions`` and ``tts`` are
    accepted and discarded (simcord derives mentions from content and never
    speaks); any other unrecognised field fails loudly with ``UnsupportedField``
    rather than being silently dropped.
    """
    return ctx.fields(
        "content", "embeds", "components", "attachments", "flags", ignore=("allowed_mentions", "tts")
    )


def message_response(ctx: RequestContext, message: Message) -> dict[str, Any]:
    return dict(serializers.message_payload(ctx.backend, message))
