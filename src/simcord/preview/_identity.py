"""Authorized user and application identities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..backend.cdn import CDN_BASE
from ..backend.errors import BackendError
from ..backend.models import Message

if TYPE_CHECKING:
    from . import Preview
    from ._pages import _Page


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    id: int
    kind: str
    name: str
    username: str
    global_name: str | None
    nickname: str | None
    bot: bool
    system: bool
    application: bool
    webhook: bool
    avatar: str | None
    avatar_kind: str
    avatar_available: bool
    role_color: int | None
    role_id: int | None
    presence: str | None


def _identity_wire(identity: IdentityRecord) -> dict[str, Any]:
    return {
        "id": str(identity.id),
        "kind": identity.kind,
        "name": identity.name,
        "username": identity.username,
        "global_name": identity.global_name,
        "nickname": identity.nickname,
        "bot": identity.bot,
        "system": identity.system,
        "application": identity.application,
        "webhook": identity.webhook,
        "avatar": identity.avatar,
        "avatar_kind": identity.avatar_kind,
        "avatar_available": identity.avatar_available,
        "role_color": identity.role_color,
        "role_id": str(identity.role_id) if identity.role_id is not None else None,
        "presence": identity.presence,
    }


def resolve_identity(
    preview: Preview,
    page: _Page,
    user_id: int,
    *,
    message: Message | None = None,
    override: str | None = None,
) -> IdentityRecord:
    """Resolve one authorized user/member identity for every preview surface."""
    env = preview.env
    user = env.backend.get_user(user_id)
    try:
        channel = env.backend.get_channel(message.channel_id if message is not None else page.channel_id)
    except BackendError:
        channel = None
    guild = (
        env.backend.guilds.get(channel.guild_id)
        if channel is not None and channel.guild_id is not None
        else None
    )
    member = guild.members.get(user_id) if guild is not None else None
    nickname = member.nick if member is not None else None
    display = override if override is not None else nickname or user.global_name or user.name
    role_id: int | None = None
    role_color: int | None = None
    if guild is not None and member is not None:
        colored = [
            role
            for rid in member.role_ids
            if (role := guild.roles.get(rid)) is not None and int(role.color or 0) != 0
        ]
        if colored:
            role = max(colored, key=lambda item: (int(item.position), int(item.id)))
            role_id = role.id
            role_color = int(role.color)
    asset_id: str | None = None
    record = None
    if channel is None:
        avatar_kind = "custom" if user.avatar else "default"
    else:
        if message is not None and message.author_avatar:
            avatar_kind = "webhook"
            avatar_url = message.author_avatar
            source = ("message", message.channel_id, message.id)
            asset_key = f"webhook-avatar:{message.channel_id}:{message.id}:{avatar_url}"
        elif member is not None and member.avatar:
            avatar_kind = "guild"
            avatar_url = f"{CDN_BASE}/guilds/{channel.guild_id}/users/{user.id}/avatars/{member.avatar}.png"
            source = ("member_avatar", channel.guild_id, user.id, member.avatar)
            asset_key = f"member-avatar:{channel.guild_id}:{user.id}:{member.avatar}"
        elif user.avatar:
            avatar_kind = "custom"
            avatar_url = f"{CDN_BASE}/avatars/{user.id}/{user.avatar}.png"
            source = ("user_avatar", user.id, user.avatar)
            asset_key = f"avatar:{user.id}:{user.avatar}"
        else:
            avatar_kind = "default"
            avatar_index = (user.id >> 22) % 6
            avatar_url = f"{CDN_BASE}/embed/avatars/{avatar_index}.png"
            source = ("default_avatar", user.id, avatar_index)
            asset_key = f"default-avatar:{user.id}:{avatar_index}"
        asset_id = page.asset_id(
            asset_key,
            {"url": avatar_url, "filename": f"avatar-{user.id}.png", "content_type": "image/png"},
            source=source,
        )
        record = page.assets.get(asset_id)
    return IdentityRecord(
        id=user.id,
        kind="webhook"
        if message is not None and message.webhook_id is not None
        else "application"
        if user.bot and user.id == env.backend.bot_user.id
        else "user",
        name=display,
        username=user.name,
        global_name=user.global_name,
        nickname=nickname,
        bot=bool(user.bot),
        system=bool(user.system),
        application=bool(user.bot),
        webhook=bool(message is not None and message.webhook_id is not None),
        avatar=asset_id,
        avatar_kind=avatar_kind,
        avatar_available=bool(record is not None and record.available),
        role_color=role_color,
        role_id=role_id,
        presence=getattr(member, "presence", None),
    )
