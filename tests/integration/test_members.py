import discord
import pytest

import simcord as dpt


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
    with pytest.raises(dpt.BackendError) as exc_info:
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
        await guild.create_role(name="God", permissions=discord.Permissions(administrator=True))
    assert exc_info.value.code == 50013


async def test_role_hierarchy_enforced(env, channel):
    overlord_role = env.guild.create_role("Overlord", position=100)
    overlord = env.guild.add_member(env.create_user("overlord"), roles=[overlord_role])
    guild = env.bot.get_guild(env.guild.id)

    with pytest.raises(discord.Forbidden) as exc_info:
        await guild.ban(guild.get_member(overlord.id))
    assert exc_info.value.code == 50013
    assert env.guild.get_ban(overlord) is None
