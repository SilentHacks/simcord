"""Message reactions."""

from __future__ import annotations

from typing import Any

from .. import errors, serializers
from ..models import Message, Reaction
from .base import BackendBase


class ReactionMixin(BackendBase):
    def set_reaction(self, channel_id: int, message_id: int, emoji: str, user_id: int, reacted: bool) -> None:
        if not isinstance(reacted, bool):
            raise errors.invalid_form_body("reaction membership must be a boolean")
        message = self.get_message(channel_id, message_id)
        reaction = message.reaction_for(emoji)
        currently_reacted = reaction is not None and user_id in reaction.user_ids
        if currently_reacted == reacted:
            return
        if reacted:
            if reaction is None:
                reaction = Reaction(emoji=emoji)
                message.reactions.append(reaction)
            reaction.user_ids.append(user_id)
            self._emit_reaction("MESSAGE_REACTION_ADD", message, emoji, user_id)
            return
        assert reaction is not None
        reaction.user_ids.remove(user_id)
        if not reaction.user_ids:
            message.reactions.remove(reaction)
        self._emit_reaction("MESSAGE_REACTION_REMOVE", message, emoji, user_id)

    def add_reaction(self, channel_id: int, message_id: int, emoji: str, user_id: int) -> None:
        self.set_reaction(channel_id, message_id, emoji, user_id, True)

    def remove_reaction(self, channel_id: int, message_id: int, emoji: str, user_id: int) -> None:
        message = self.get_message(channel_id, message_id)
        reaction = message.reaction_for(emoji)
        if reaction is None or user_id not in reaction.user_ids:
            raise errors.unknown_message()
        self.set_reaction(channel_id, message_id, emoji, user_id, False)

    def clear_reactions(self, channel_id: int, message_id: int) -> None:
        """Remove every reaction from a message (MESSAGE_REACTION_REMOVE_ALL)."""
        message = self.get_message(channel_id, message_id)
        message.reactions.clear()
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"channel_id": str(channel_id), "message_id": str(message_id)}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_REACTION_REMOVE_ALL", payload)

    def clear_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        """Remove all of one emoji's reactions from a message (MESSAGE_REACTION_REMOVE_EMOJI)."""
        message = self.get_message(channel_id, message_id)
        reaction = message.reaction_for(emoji)
        if reaction is not None:
            message.reactions.remove(reaction)
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "emoji": serializers.emoji_payload(emoji),
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_REACTION_REMOVE_EMOJI", payload)

    def _emit_reaction(self, event: str, message: Message, emoji: str, user_id: int) -> None:
        channel = self.get_channel(message.channel_id)
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "channel_id": str(message.channel_id),
            "message_id": str(message.id),
            "emoji": serializers.emoji_payload(emoji),
            "burst": False,
            "type": 0,
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
            guild = self.guilds[channel.guild_id]
            if event == "MESSAGE_REACTION_ADD" and user_id in guild.members:
                payload["member"] = serializers.member_payload(self, guild, guild.members[user_id])
        self.emit(event, payload)
