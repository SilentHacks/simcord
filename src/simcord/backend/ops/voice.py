"""Voice state."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ...enums import ChannelType
from .. import errors, serializers
from ..models import VoiceState
from .base import BackendBase


class VoiceStateMixin(BackendBase):
    def prepare_voice_state(
        self, guild_id: int, user_id: int, channel_id: int | None, **flags: Any
    ) -> VoiceState | None:
        """Validate a destination and construct a detached next voice state."""
        guild = self.get_guild(guild_id)
        if channel_id is not None:
            self.get_user(user_id)
            if user_id not in guild.members:
                raise errors.unknown_member()
            channel = self.get_channel(channel_id)
            if channel.guild_id != guild_id:
                raise errors.unknown_channel()
            if channel.type not in (ChannelType.VOICE, ChannelType.STAGE_VOICE):
                raise errors.cannot_execute_on_channel_type()
        state = guild.voice_states.get(user_id)
        if state is None:
            if channel_id is None:
                return None
            state = VoiceState(
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                session_id=f"session_{user_id}",
            )
        allowed = {
            "mute",
            "deaf",
            "self_mute",
            "self_deaf",
            "self_stream",
            "self_video",
            "suppress",
            "request_to_speak_timestamp",
        }
        if flags.keys() - allowed:
            raise errors.invalid_form_body("Unknown voice state field")
        if any(
            not isinstance(value, bool) for key, value in flags.items() if key != "request_to_speak_timestamp"
        ):
            raise errors.invalid_form_body("Voice flags must be booleans")
        return replace(state, channel_id=channel_id, **flags)

    def commit_voice_state(self, state: VoiceState, *, broadcast: bool = True) -> None:
        """Install a prepared state; compound owners publish after all writes."""
        guild = self.get_guild(state.guild_id)
        if state.channel_id is None:
            guild.voice_states.pop(state.user_id, None)
        else:
            guild.voice_states[state.user_id] = state
        if broadcast:
            self.emit("VOICE_STATE_UPDATE", serializers.voice_state_payload(self, state))

    def set_voice_state(
        self, guild_id: int, user_id: int, channel_id: int | None, **flags: Any
    ) -> VoiceState | None:
        """Upsert a member's voice state (or disconnect when ``channel_id`` is None).

        Disconnecting a member who is not connected is a no-op (no event), so a
        stray ``leave_voice`` does not emit a spurious VOICE_STATE_UPDATE.
        """
        state = self.prepare_voice_state(guild_id, user_id, channel_id, **flags)
        if state is not None:
            self.commit_voice_state(state)
        return state
