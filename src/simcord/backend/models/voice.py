from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VoiceState:
    """A member's voice connection state (state only — SimCord never models audio)."""

    user_id: int
    guild_id: int
    channel_id: int | None  # None once disconnected
    session_id: str
    deaf: bool = False  # server deaf
    mute: bool = False  # server mute
    self_deaf: bool = False
    self_mute: bool = False
    self_stream: bool = False
    self_video: bool = False
    suppress: bool = False
    request_to_speak_timestamp: str | None = None
