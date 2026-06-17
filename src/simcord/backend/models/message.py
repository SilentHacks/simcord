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
class PollAnswer:
    answer_id: int
    text: str | None = None
    emoji: str | None = None  # unicode emoji or "name:id" for custom


@dataclass
class Poll:
    question: str
    answers: list[PollAnswer]
    expiry: str
    allow_multiselect: bool = False
    layout_type: int = 1
    finalized: bool = False
    #: answer_id -> set of user ids who picked it.
    votes: dict[int, set[int]] = field(default_factory=dict)

    def answer(self, answer_id: int) -> PollAnswer | None:
        return next((a for a in self.answers if a.answer_id == answer_id), None)


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
    #: A per-message display-name override (an incoming webhook's ``username=``).
    #: When set, the serialized author reports this name instead of the authoring
    #: user's; ``None`` means "use the author user's own name" (the common case).
    author_name: str | None = None
    poll: Poll | None = None

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
