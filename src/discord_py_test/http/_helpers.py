"""Shared helpers for route handlers."""

from __future__ import annotations

from typing import Any

from ..backend import errors, serializers
from ..backend.models import EPHEMERAL_FLAG, Interaction, Message
from .router import RequestContext


def bot_message(
    ctx: RequestContext,
    channel_id: int,
    *,
    author_id: int | None = None,
    interaction: Interaction | None = None,
    webhook_id: int | None = None,
    body: dict[str, Any] | None = None,
) -> Message:
    """Create a message from a request body, authored by the bot (or a webhook user).

    ``body`` defaults to the request's JSON body, but callers (e.g. interaction
    callbacks, where the message payload is nested under ``data``) may pass an
    explicit body instead of mutating the shared request context.
    """
    backend = ctx.backend
    if body is None:
        body = ctx.body()
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


def message_response(ctx: RequestContext, message: Message) -> dict[str, Any]:
    return dict(serializers.message_payload(ctx.backend, message))
