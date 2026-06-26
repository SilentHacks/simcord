"""Webhooks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import errors
from ..models import User, Webhook
from .base import BackendBase


class WebhookMixin(BackendBase):
    def create_webhook(self, channel_id: int, name: str, creator_id: int) -> Webhook:
        channel = self.get_channel(channel_id)
        webhook_id = self.snowflake()
        # A webhook authors its own messages: on real Discord the message's
        # author.id equals the webhook id. Register the authoring bot user under
        # that same id so provenance checks like ``author.id == webhook_id`` hold.
        webhook_user = User(id=webhook_id, name=name, bot=True)
        self.users[webhook_id] = webhook_user
        webhook = Webhook(
            id=webhook_id,
            token=f"simcord_webhook_{webhook_id}",
            channel_id=channel_id,
            guild_id=channel.guild_id,
            name=name,
            creator_id=creator_id,
            webhook_user_id=webhook_id,
        )
        self.webhooks[webhook.id] = webhook
        self.webhook_tokens[webhook.token] = webhook.id
        return webhook

    def get_webhook(self, webhook_id: int) -> Webhook:
        webhook = self.webhooks.get(webhook_id)
        if webhook is None:
            raise errors.unknown_webhook()
        return webhook

    def edit_webhook(self, webhook_id: int, changes: Mapping[str, Any]) -> Webhook:
        """Edit a webhook's name and/or channel, announcing WEBHOOKS_UPDATE."""
        webhook = self.get_webhook(webhook_id)
        if changes.get("name") is not None:
            webhook.name = changes["name"]
        if changes.get("channel_id") is not None:
            webhook.channel_id = int(changes["channel_id"])
        self._emit_webhooks_update(webhook.channel_id)
        return webhook

    def delete_webhook(self, webhook_id: int) -> None:
        webhook = self.get_webhook(webhook_id)
        del self.webhooks[webhook_id]
        self.webhook_tokens.pop(webhook.token, None)
        self._emit_webhooks_update(webhook.channel_id)

    def _emit_webhooks_update(self, channel_id: int) -> None:
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"channel_id": str(channel_id)}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("WEBHOOKS_UPDATE", payload)
