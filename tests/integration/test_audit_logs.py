import discord
import pytest


async def test_ban_records_audit_entry(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    target = env.guild.add_member(env.create_user("spammer"))
    await env.settle()

    member = guild.get_member(target.id)
    await member.ban(reason="spam")
    await env.settle()

    entries = [e async for e in guild.audit_logs(action=discord.AuditLogAction.ban)]
    assert len(entries) == 1
    assert entries[0].target.id == target.id
    assert entries[0].user.id == guild.me.id
    assert entries[0].reason == "spam"
    # The reason is also threaded into the ban record itself (S1 fix).
    assert env.guild.get_ban(target)["reason"] == "spam"


async def test_role_update_records_changes(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="VIP")
    await guild.get_member(alice.id).add_roles(role)
    await env.settle()

    entries = [e async for e in guild.audit_logs(action=discord.AuditLogAction.member_role_update)]
    assert entries
    assert entries[0].target.id == alice.id
    # The backend recorded the $add option naming the granted role.
    role_updates = [e for e in env.guild.audit_log() if e.action_type == 25]
    assert role_updates[-1].options["$add"][0]["id"] == str(role.id)


async def test_audit_log_filters_by_user(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    a = env.guild.add_member(env.create_user("a"))
    b = env.guild.add_member(env.create_user("b"))
    await env.settle()
    await guild.get_member(a.id).kick()
    await guild.get_member(b.id).kick()
    await env.settle()

    # Every entry's executor is the bot; filtering by it returns both kicks.
    by_bot = [e async for e in guild.audit_logs(user=guild.me, action=discord.AuditLogAction.kick)]
    assert len(by_bot) == 2


async def test_audit_log_requires_view_audit_log(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    # Strip view_audit_log from every role (the bot's managed role is above the
    # bot itself, so it can't drop it over the API — set it on the backend).
    mask = ~discord.Permissions(view_audit_log=True).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask

    with pytest.raises(discord.Forbidden):
        _ = [e async for e in guild.audit_logs(limit=1)]


async def test_sample_bot_recent_bans_command(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    mod = env.guild.add_member(env.create_user("mod"))
    target = env.guild.add_member(env.create_user("troll"))
    await env.settle()
    await guild.get_member(target.id).ban(reason="spam")

    result = await mod.slash(channel, "recent-bans")
    assert "troll" in result.response.content
