import discord
import pytest


async def test_create_and_fetch_invite(env, channel):
    ch = env.bot.get_channel(channel.id)
    invite = await ch.create_invite(max_uses=5)
    await env.settle()

    assert invite.code
    assert "INVITE_CREATE" in env.transcript()
    fetched = await env.bot.fetch_invite(invite.code)
    assert fetched.code == invite.code

    listed = await ch.invites()
    assert [i.code for i in listed] == [invite.code]


async def test_delete_invite(env, channel):
    ch = env.bot.get_channel(channel.id)
    invite = await ch.create_invite()
    await invite.delete()
    await env.settle()

    assert "INVITE_DELETE" in env.transcript()
    with pytest.raises(discord.NotFound):
        await env.bot.fetch_invite(invite.code)


async def test_guild_invites_listing(env, channel):
    ch = env.bot.get_channel(channel.id)
    await ch.create_invite()
    await env.settle()

    guild = env.bot.get_guild(env.guild.id)
    listed = await guild.invites()
    assert len(listed) == 1


async def test_create_invite_requires_permission(env, channel):
    ch = env.bot.get_channel(channel.id)
    mask = ~discord.Permissions(create_instant_invite=True).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask
    with pytest.raises(discord.Forbidden):
        await ch.create_invite()
