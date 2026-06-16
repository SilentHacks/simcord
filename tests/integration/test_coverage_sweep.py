"""Coverage for the read/list/filter routes the higher-level tests skip:
ban listing + unban, role listing, audit-log filtering, scheduled-event reads,
and the scheduled-event validation branches.
"""

import datetime

import discord
import pytest

import simcord
from simcord.http import router


async def test_ban_list_fetch_and_unban(env):
    guild = env.bot.get_guild(env.guild.id)
    target = env.guild.add_member(env.create_user("baddie"))
    await guild.ban(discord.Object(id=target.id), reason="spam")
    await env.settle()

    bans = [b async for b in guild.bans()]
    assert target.id in {b.user.id for b in bans}

    entry = await guild.fetch_ban(discord.Object(id=target.id))
    assert entry.reason == "spam"

    await guild.unban(discord.Object(id=target.id))
    await env.settle()
    assert env.guild.get_ban(target) is None
    assert "GUILD_BAN_REMOVE" in env.transcript()


async def test_fetch_roles_lists_all(env):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_role(name="Extra")
    await env.settle()

    roles = await guild.fetch_roles()
    assert "Extra" in {r.name for r in roles}


async def test_audit_log_filtering(env):
    guild = env.bot.get_guild(env.guild.id)
    victim = env.guild.add_member(env.create_user("victim"))
    await guild.kick(discord.Object(id=victim.id))
    await guild.create_role(name="Filler")
    await env.settle()

    # Filter by action: only the kick entry comes back.
    kicks = [e async for e in guild.audit_logs(action=discord.AuditLogAction.kick)]
    assert kicks and all(e.action is discord.AuditLogAction.kick for e in kicks)

    # before/after cursors narrow the window without error.
    everything = [e async for e in guild.audit_logs(limit=None)]
    pivot = everything[len(everything) // 2]
    older = [e async for e in guild.audit_logs(before=discord.Object(pivot.id))]
    newer = [e async for e in guild.audit_logs(after=discord.Object(pivot.id))]
    assert all(e.id < pivot.id for e in older)
    assert all(e.id > pivot.id for e in newer)


async def test_scheduled_event_get_and_subscribers(env):
    guild = env.bot.get_guild(env.guild.id)
    start = discord.utils.utcnow() + datetime.timedelta(hours=1)
    end = start + datetime.timedelta(hours=2)
    event = await guild.create_scheduled_event(
        name="Launch Party",
        start_time=start,
        end_time=end,
        entity_type=discord.EntityType.external,
        location="The Moon",
        privacy_level=discord.PrivacyLevel.guild_only,
    )
    await env.settle()

    fetched = await guild.fetch_scheduled_event(event.id)
    assert fetched.id == event.id

    # An interested member subscribes, then shows up in the subscriber list.
    member = env.guild.add_member(env.create_user("fan"))
    env.backend.set_scheduled_event_subscription(env.guild.id, event.id, member.id, True)
    users = [u async for u in event.users()]
    assert member.id in {u.id for u in users}


def test_scheduled_event_validation_branches(env):
    guild_id = env.guild.id

    # External event without location → invalid form body.
    with pytest.raises(simcord.BackendError):
        router.dispatch(
            env.backend,
            "POST",
            f"/guilds/{guild_id}/scheduled-events",
            json={
                "name": "Bad",
                "entity_type": 3,
                "scheduled_start_time": "2030-01-01T00:00:00+00:00",
                "scheduled_end_time": "2030-01-01T02:00:00+00:00",
                "entity_metadata": {},
            },
        )

    # Voice/stage event without a channel → invalid form body.
    with pytest.raises(simcord.BackendError):
        router.dispatch(
            env.backend,
            "POST",
            f"/guilds/{guild_id}/scheduled-events",
            json={
                "name": "Bad voice",
                "entity_type": 2,
                "scheduled_start_time": "2030-01-01T00:00:00+00:00",
            },
        )
