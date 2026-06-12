from __future__ import annotations

from dataclasses import dataclass, field

from .member import Member
from .role import Role


@dataclass
class Guild:
    id: int
    name: str
    owner_id: int
    roles: dict[int, Role] = field(default_factory=dict)
    members: dict[int, Member] = field(default_factory=dict)
    channel_ids: list[int] = field(default_factory=list)
    thread_ids: list[int] = field(default_factory=list)
    bans: dict[int, str | None] = field(default_factory=dict)  # user id -> reason

    @property
    def everyone_role(self) -> Role:
        return self.roles[self.id]

    def top_role_position(self, user_id: int) -> int:
        member = self.members.get(user_id)
        if member is None:
            return 0
        return max((self.roles[r].position for r in member.role_ids if r in self.roles), default=0)
