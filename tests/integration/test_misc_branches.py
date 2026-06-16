"""Assorted uncovered branches: the bot typing route, the @everyone mention
permission gate, and the actor repr."""

import discord


async def test_bot_typing_indicator(env, channel):
    cached = env.bot.get_channel(channel.id)
    async with cached.typing():
        pass
    await env.settle()

    assert any(method == "POST" and path.endswith("/typing") for method, path, _ in env.http_log)


async def test_everyone_mention_requires_permission(env, channel):
    crier_role = env.guild.create_role(
        "Crier", permissions=discord.Permissions(mention_everyone=True, send_messages=True)
    )
    crier = env.guild.add_member(env.create_user("crier"), roles=[crier_role])
    quiet = env.guild.add_member(env.create_user("quiet"))

    loud = await crier.send(channel, "@everyone listen up")
    hushed = await quiet.send(channel, "@everyone listen up")
    await env.settle()

    assert env.backend.get_message(channel.id, loud.id).mention_everyone is True
    assert env.backend.get_message(channel.id, hushed.id).mention_everyone is False


async def test_member_actor_repr(env, alice):
    assert repr(alice).startswith("<MemberActor")
    assert "alice" in repr(alice)
