from __future__ import annotations

from dataclasses import dataclass, field

from .auditlog import AuditLogEntry
from .automod import AutoModRule
from .expression import GuildEmoji, Sticker
from .member import Member
from .role import Role
from .scheduled_event import ScheduledEvent
from .voice import VoiceState


@dataclass
class Guild:
    id: int
    name: str
    owner_id: int
    # Editable guild settings (discord.py's Guild.edit surface).
    description: str | None = None
    afk_channel_id: int | None = None
    afk_timeout: int = 300
    system_channel_id: int | None = None
    rules_channel_id: int | None = None
    public_updates_channel_id: int | None = None
    verification_level: int = 0
    default_message_notifications: int = 0
    explicit_content_filter: int = 0
    preferred_locale: str = "en-US"
    vanity_url_code: str | None = None
    roles: dict[int, Role] = field(default_factory=dict)
    members: dict[int, Member] = field(default_factory=dict)
    channel_ids: list[int] = field(default_factory=list)
    thread_ids: list[int] = field(default_factory=list)
    bans: dict[int, str | None] = field(default_factory=dict)  # user id -> reason
    audit_log_entries: list[AuditLogEntry] = field(default_factory=list)  # append-only, oldest first
    scheduled_events: dict[int, ScheduledEvent] = field(default_factory=dict)
    voice_states: dict[int, VoiceState] = field(default_factory=dict)  # by user id
    emojis: dict[int, GuildEmoji] = field(default_factory=dict)
    stickers: dict[int, Sticker] = field(default_factory=dict)
    auto_mod_rules: dict[int, AutoModRule] = field(default_factory=dict)

    @property
    def everyone_role(self) -> Role:
        return self.roles[self.id]

    def top_role_position(self, user_id: int) -> int:
        member = self.members.get(user_id)
        if member is None:
            return 0
        return max((self.roles[r].position for r in member.role_ids if r in self.roles), default=0)
