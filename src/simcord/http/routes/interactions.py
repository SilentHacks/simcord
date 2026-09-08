"""Interaction lifecycle routes: callback, followups, original-response ops.

The webhook execute route is shared with standalone channel webhooks: tokens
are looked up first as interaction tokens, then as webhook tokens.
"""

from __future__ import annotations

from typing import Any

from ...backend import errors
from ...backend.models import EPHEMERAL_FLAG, Interaction
from ...components import ComponentValidationError, validate_modal, walk_components
from ...enums import CallbackType
from .._helpers import bot_message, message_edit_changes, message_response
from ..router import RequestContext, route


@route("POST", "/interactions/{interaction_id}/{token}/callback")
def interaction_callback(ctx: RequestContext) -> Any:
    backend = ctx.backend
    if ctx.int_arg("interaction_id") not in backend.interactions:
        raise errors.unknown_webhook()
    record = backend.interaction_by_token(ctx.args["token"])
    if record.id != ctx.int_arg("interaction_id"):
        raise errors.unknown_webhook()
    if record.responded:
        raise errors.already_acknowledged()
    body = ctx.body()
    callback_type = body["type"]
    data = body.get("data") or {}
    ephemeral = bool(int(data.get("flags") or 0) & EPHEMERAL_FLAG)
    message = None

    # The marker methods set `responded`, so they must run only after the
    # callback is handled successfully: a callback that 400s (e.g. an oversized
    # embed) does not consume the interaction on real Discord — a retry must not
    # see 40060.
    if callback_type == CallbackType.CHANNEL_MESSAGE_WITH_SOURCE:
        message = bot_message(ctx, record.channel_id, interaction=record, body=data)
        record.respond_with_message(message.id, ephemeral=ephemeral)
    elif callback_type == CallbackType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE:
        record.defer(ephemeral=ephemeral)
    elif callback_type == CallbackType.DEFERRED_UPDATE_MESSAGE:
        record.defer_update()
    elif callback_type == CallbackType.UPDATE_MESSAGE:
        if record.source_message_id is not None:
            existing = backend.get_message(record.channel_id, record.source_message_id)
            message = backend.edit_message(
                record.channel_id,
                record.source_message_id,
                message_edit_changes(ctx, existing, body=data),
            )
        record.update_source(record.source_message_id)
    elif callback_type == CallbackType.MODAL:
        try:
            modal = validate_modal(data)
        except (ComponentValidationError, TypeError, ValueError) as exc:
            detail = exc if isinstance(exc, ComponentValidationError) else ComponentValidationError(str(exc))
            raise errors.invalid_form_body(str(detail)) from exc
        record.show_modal(modal)
    elif callback_type == CallbackType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT:
        record.complete_autocomplete(data.get("choices", []))
    elif callback_type == CallbackType.PONG:
        record.pong()
    else:
        raise errors.invalid_form_body(f"unknown interaction callback type {callback_type}")

    response: dict[str, Any] = {
        "interaction": {
            "id": str(record.id),
            "type": record.type,
            "activity_instance_id": None,
            "response_message_id": str(record.message_id) if record.message_id else None,
            "response_message_loading": record.loading,
            "response_message_ephemeral": record.ephemeral,
        }
    }
    if message is not None:
        response["resource"] = {"type": callback_type, "message": message_response(ctx, message)}
    return response


def _incoming_webhook_body(ctx: RequestContext) -> tuple[dict[str, Any], bool]:
    body = dict(ctx.body())
    with_components = str(ctx.params.get("with_components", "")).lower() in {"1", "true"}
    if not with_components:
        body.pop("components", None)
    elif any(
        component.get("type") in {2, 3, 5, 6, 7, 8}
        for component in walk_components(body.get("components", []))
    ):
        raise errors.invalid_form_body("non-application webhooks cannot send interactive components")
    return body, with_components


@route("POST", "/webhooks/{webhook_id}/{token}")
def execute_webhook(ctx: RequestContext) -> Any:
    backend = ctx.backend
    token = ctx.args["token"]
    if token in backend.interaction_tokens:
        record = backend.interaction_by_token(token)
        if ctx.int_arg("webhook_id") != backend.application_id:
            raise errors.unknown_webhook()
        message = bot_message(ctx, record.channel_id, interaction=record, webhook_execute=True)
        record.followup_ids.append(message.id)
        return message_response(ctx, message)
    webhook = _incoming_webhook(ctx)
    body, _ = _incoming_webhook_body(ctx)
    message = bot_message(
        ctx,
        webhook.channel_id,
        author_id=webhook.webhook_user_id,
        webhook_id=webhook.id,
        body=body,
        webhook_execute=True,
    )
    return message_response(ctx, message)


def _record(ctx: RequestContext) -> Interaction:
    backend = ctx.backend
    record = backend.interaction_by_token(ctx.args["token"])
    if ctx.int_arg("webhook_id") != backend.application_id:
        raise errors.unknown_webhook()
    return record


def _incoming_webhook(ctx: RequestContext) -> Any:
    backend = ctx.backend
    webhook_id = backend.webhook_tokens.get(ctx.args["token"])
    if webhook_id is None or webhook_id != ctx.int_arg("webhook_id"):
        raise errors.unknown_webhook()
    return backend.webhooks[webhook_id]


def _incoming_message(ctx: RequestContext) -> Any:
    webhook = _incoming_webhook(ctx)
    message = ctx.backend.get_message(webhook.channel_id, ctx.int_arg("message_id"))
    if message.webhook_id != webhook.id:
        raise errors.unknown_message()
    return message


@route("GET", "/webhooks/{webhook_id}/{token}/messages/@original")
def get_original_response(ctx: RequestContext) -> Any:
    record = _record(ctx)
    if record.message_id is None:
        raise errors.unknown_message()
    return message_response(ctx, ctx.backend.get_message(record.channel_id, record.message_id))


@route("PATCH", "/webhooks/{webhook_id}/{token}/messages/@original")
def edit_original_response(ctx: RequestContext) -> Any:
    record = _record(ctx)
    backend = ctx.backend
    if record.message_id is None and record.loading:
        # Editing a deferred response materialises the response message.
        body = dict(ctx.body())
        if record.ephemeral:
            body["flags"] = int(body.get("flags") or 0) | EPHEMERAL_FLAG
        message = bot_message(ctx, record.channel_id, interaction=record, body=body)
        record.materialise_deferred(message.id)
        return message_response(ctx, message)
    if record.message_id is None:
        raise errors.unknown_message()
    message = backend.edit_message(
        record.channel_id,
        record.message_id,
        message_edit_changes(ctx, backend.get_message(record.channel_id, record.message_id)),
    )
    return message_response(ctx, message)


@route("DELETE", "/webhooks/{webhook_id}/{token}/messages/@original")
def delete_original_response(ctx: RequestContext) -> Any:
    record = _record(ctx)
    if record.message_id is not None:
        ctx.backend.delete_message(record.channel_id, record.message_id)
        record.message_id = None


@route("GET", "/webhooks/{webhook_id}/{token}/messages/{message_id}")
def get_followup(ctx: RequestContext) -> Any:
    if ctx.args["token"] in ctx.backend.interaction_tokens:
        record = _record(ctx)
        return message_response(ctx, ctx.backend.get_message(record.channel_id, ctx.int_arg("message_id")))
    return message_response(ctx, _incoming_message(ctx))


@route("PATCH", "/webhooks/{webhook_id}/{token}/messages/{message_id}")
def edit_followup(ctx: RequestContext) -> Any:
    if ctx.args["token"] in ctx.backend.interaction_tokens:
        record = _record(ctx)
        message = ctx.backend.get_message(record.channel_id, ctx.int_arg("message_id"))
        message = ctx.backend.edit_message(record.channel_id, message.id, message_edit_changes(ctx, message))
        return message_response(ctx, message)
    message = _incoming_message(ctx)
    body, with_components = _incoming_webhook_body(ctx)
    message = ctx.backend.edit_message(
        message.channel_id,
        message.id,
        message_edit_changes(ctx, message, body=body),
    )
    response = message_response(ctx, message)
    if not with_components:
        response.pop("components", None)
    return response


@route("DELETE", "/webhooks/{webhook_id}/{token}/messages/{message_id}")
def delete_followup(ctx: RequestContext) -> Any:
    if ctx.args["token"] in ctx.backend.interaction_tokens:
        record = _record(ctx)
        ctx.backend.delete_message(record.channel_id, ctx.int_arg("message_id"))
        return
    message = _incoming_message(ctx)
    ctx.backend.delete_message(message.channel_id, message.id)
