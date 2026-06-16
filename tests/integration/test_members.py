import datetime

import discord
import pytest

import simcord


async def test_member_edit_applies_all_fields(env):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Squad")
    voice = env.guild.create_voice_channel("Voice")
    target = env.guild.add_member(env.create_user("target"))
    await target.join_voice(voice)
    await env.settle()

    member = guild.get_member(target.id)
    until = discord.utils.utcnow() + datetime.timedelta(minutes=5)
    await member.edit(
        nick="Tagged",
        roles=[guild.get_role(role.id)],
        mute=True,
        deafen=True,
        timed_out_until=until,
    )
    await env.settle()

    backend_member = env.backend.get_member(env.guild.id, target.id)
    assert backend_member.nick == "Tagged"
    assert role.id in backend_member.role_ids
    assert backend_member.mute is True
    assert backend_member.deaf is True
    assert backend_member.timed_out_until is not None
    # The whole edit was journalled: a member update plus a role change.
    actions = {e.action_type for e in env.guild.audit_log()}
    assert {24, 25} <= actions  # MEMBER_UPDATE, MEMBER_ROLE_UPDATE


async def test_member_voice_move_and_disconnect(env):
    guild = env.bot.get_guild(env.guild.id)
    a = env.guild.create_voice_channel("A")
    b = env.guild.create_voice_channel("B")
    target = env.guild.add_member(env.create_user("mover"))
    await target.join_voice(a)
    await env.settle()

    member = guild.get_member(target.id)
    await member.move_to(guild.get_channel(b.id))
    await env.settle()
    assert env.backend.get_guild(env.guild.id).voice_states[target.id].channel_id == b.id

    await member.move_to(None)  # disconnect
    await env.settle()
    assert target.id not in env.backend.get_guild(env.guild.id).voice_states


async def test_member_join_event(env, alice):
    welcome = env.guild.create_text_channel("welcome")
    bob = env.guild.add_member(env.create_user("bob"))
    await env.settle()

    assert welcome.last_message.content == f"Welcome {bob.mention}!"


async def test_member_leave_event(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    assert guild.get_member(alice.id) is not None
    env.guild.remove_member(alice)
    await env.settle()
    assert guild.get_member(alice.id) is None


async def test_timeout_via_slash(env, channel):
    mod = env.guild.add_member(env.create_user("mod"))
    target = env.guild.add_member(env.create_user("loudmouth"))

    await mod.slash(channel, "quiet", user=target)

    cached = env.bot.get_guild(env.guild.id).get_member(target.id)
    assert cached.is_timed_out()
    # Timed-out members can't speak — exactly as on Discord.
    with pytest.raises(simcord.BackendError) as exc_info:
        await target.send(channel, "I'm back!")
    assert exc_info.value.code == 50013


async def test_role_management(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="VIP", permissions=discord.Permissions(manage_messages=True))
    member = guild.get_member(alice.id)
    await member.add_roles(role)
    await env.settle()

    assert role in guild.get_member(alice.id).roles
    assert guild.get_member(alice.id).guild_permissions.manage_messages

    await member.remove_roles(role)
    await env.settle()
    assert role not in guild.get_member(alice.id).roles


async def test_bot_cannot_assign_role_above_itself(env, channel, alice):
    high_role = env.guild.create_role("Admin", position=100)
    guild = env.bot.get_guild(env.guild.id)
    member = guild.get_member(alice.id)
    with pytest.raises(discord.Forbidden) as exc_info:
        await member.add_roles(guild.get_role(high_role.id))
    assert exc_info.value.code == 50013


async def test_bot_cannot_grant_permissions_it_lacks(env, channel):
    # The bot's integration role has all perms except administrator, so it
    # cannot create a role that grants administrator.
    guild = env.bot.get_guild(env.guild.id)
    with pytest.raises(discord.Forbidden) as exc_info:
        await guild.create_role(name="Admin", permissions=discord.Permissions(administrator=True))
    assert exc_info.value.code == 50013


async def test_role_hierarchy_enforced(env, channel):
    overlord_role = env.guild.create_role("Overlord", position=100)
    overlord = env.guild.add_member(env.create_user("overlord"), roles=[overlord_role])
    guild = env.bot.get_guild(env.guild.id)

    with pytest.raises(discord.Forbidden) as exc_info:
        await guild.ban(guild.get_member(overlord.id))
    assert exc_info.value.code == 50013
    assert env.guild.get_ban(overlord) is None


async def test_fetch_members_lists_everyone(env, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    guild = env.bot.get_guild(env.guild.id)

    members = [m async for m in guild.fetch_members(limit=None)]
    ids = {m.id for m in members}
    assert {alice.id, bob.id, env.bot.user.id} <= ids


async def test_fetch_members_paginates(env, alice):
    for i in range(5):
        env.guild.add_member(env.create_user(f"user{i}"))
    guild = env.bot.get_guild(env.guild.id)

    # A small page size forces the MemberIterator to follow `after` cursors.
    members = [m async for m in guild.fetch_members(limit=2)]
    assert len(members) == 2


async def test_query_members_by_name(env, alice):
    # query_members searches over the gateway (REQUEST_GUILD_MEMBERS), which the
    # fake websocket already answers — distinct from the REST list endpoint above.
    env.guild.add_member(env.create_user("bob"))
    guild = env.bot.get_guild(env.guild.id)

    found = await guild.query_members("ali")
    assert [m.name for m in found] == ["alice"]
