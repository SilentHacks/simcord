"""The leaf-safe permission validators have owner/admin/DM bypass branches that
the route-level tests rarely hit (the bot is never an owner or an admin)."""

from __future__ import annotations

import discord


async def test_validators_bypass_for_guild_owner(env):
    backend = env.backend
    guild_id = env.guild.id
    owner_id = backend.get_guild(guild_id).owner_id
    role = backend.create_role(guild_id, "High", permissions=0)

    # No exceptions: the owner clears every hierarchy/grant check, and a DM
    # (guild_id=None) requires no guild permissions at all.
    backend.require_permissions(None, owner_id, None, "manage_guild")
    backend.require_hierarchy(guild_id, owner_id, backend.bot_user.id)
    backend.require_role_assignable(guild_id, owner_id, role.id)
    backend.require_position_assignable(guild_id, owner_id, 999)
    backend.require_can_grant(guild_id, owner_id, discord.Permissions.all().value)


async def test_can_grant_bypasses_for_administrator(env):
    backend = env.backend
    guild_id = env.guild.id
    admin_role = backend.create_role(
        guild_id, "Admin", permissions=discord.Permissions(administrator=True).value
    )
    admin = env.create_user("administrator")
    backend.add_member(guild_id, admin.id, roles=[admin_role.id])

    # A non-owner administrator can grant any permission.
    backend.require_can_grant(guild_id, admin.id, discord.Permissions.all().value)
