"""Invites."""

from __future__ import annotations

from .. import errors, serializers
from ..models import Invite
from .base import BackendBase


class InviteMixin(BackendBase):
    def set_vanity_url(self, guild_id: int, code: str) -> None:
        """Install a fixture-owned vanity invite without an API invite event."""
        guild = self.get_guild(guild_id)
        if not guild.channel_ids:
            raise errors.SetupError("set_vanity_url needs a channel; create one first")
        invite = Invite(
            code=code,
            guild_id=guild_id,
            channel_id=guild.channel_ids[0],
            inviter_id=self.bot_user.id,
            created_at=self.now_iso(),
        )
        guild.vanity_url_code = code
        self.invites[code] = invite

    def create_invite(
        self,
        channel_id: int,
        inviter_id: int,
        *,
        max_uses: int = 0,
        max_age: int = 86400,
        temporary: bool = False,
    ) -> Invite:
        channel = self.get_channel(channel_id)
        invite = Invite(
            code=f"sc{self.snowflake() % 100000000:08d}",
            guild_id=channel.guild_id,  # type: ignore[arg-type]
            channel_id=channel_id,
            inviter_id=inviter_id,
            created_at=self.now_iso(),
            max_uses=max_uses,
            max_age=max_age,
            temporary=temporary,
            expires_at=self.iso_after(max_age) if max_age else None,
        )
        self.invites[invite.code] = invite
        self.emit("INVITE_CREATE", serializers.invite_payload(self, invite, with_inviter=True))
        return invite

    def get_invite(self, code: str) -> Invite:
        invite = self.invites.get(code)
        if invite is None:
            raise errors.unknown_invite()
        return invite

    def delete_invite(self, code: str) -> Invite:
        invite = self.get_invite(code)
        del self.invites[code]
        self.emit(
            "INVITE_DELETE",
            {
                "code": code,
                "channel_id": str(invite.channel_id),
                "guild_id": str(invite.guild_id),
            },
        )
        return invite
