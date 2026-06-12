from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: discord.MessageFlags.ephemeral — only the interaction's invoker sees the message.
EPHEMERAL_FLAG = 1 << 6


@dataclass
class Reaction:
    emoji: str  # unicode emoji or "name:id" for custom
    user_ids: list[int] = field(default_factory=list)


@dataclass
class Message:
    id: int
    channel_id: int
    author_id: int
    content: str = ""
    timestamp: str = ""
    edited_timestamp: str | None = None
    type: int = 0
    flags: int = 0
    pinned: bool = False
    tts: bool = False
    embeds: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[Reaction] = field(default_factory=list)
    mention_user_ids: list[int] = field(default_factory=list)
    mention_role_ids: list[int] = field(default_factory=list)
    mention_everyone: bool = False
    reference: dict[str, Any] | None = None
    interaction_metadata: dict[str, Any] | None = None
    webhook_id: int | None = None

    @property
    def is_ephemeral(self) -> bool:
        return bool(self.flags & EPHEMERAL_FLAG)

    def reaction_for(self, emoji: str) -> Reaction | None:
        for reaction in self.reactions:
            if reaction.emoji == emoji:
                return reaction
        return None

    def visible_to(self, user_id: int | None) -> bool:
        """Whether ``user_id`` could see this message in their Discord client.

        Non-ephemeral messages are visible to everyone; an ephemeral message is
        visible only to the interaction invoker recorded in its
        ``interaction_metadata`` (and to nobody when ``user_id`` is ``None``).
        """
        if not self.is_ephemeral:
            return True
        if user_id is None:
            return False
        meta = self.interaction_metadata or {}
        return str((meta.get("user") or {}).get("id")) == str(user_id)
