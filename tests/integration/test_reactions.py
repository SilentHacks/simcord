import discord
import pytest


async def test_clear_all_reactions(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    message = await alice.send(channel, "react!")
    await alice.react(message, "👍")
    await bob.react(message, "🔥")

    cached = await env.bot.get_channel(channel.id).fetch_message(message.id)
    await cached.clear_reactions()
    await env.settle()

    refetched = await env.bot.get_channel(channel.id).fetch_message(message.id)
    assert refetched.reactions == []
    assert "MESSAGE_REACTION_REMOVE_ALL" in env.transcript()


async def test_clear_single_reaction(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    message = await alice.send(channel, "react!")
    await alice.react(message, "👍")
    await bob.react(message, "🔥")

    cached = await env.bot.get_channel(channel.id).fetch_message(message.id)
    await cached.clear_reaction("👍")
    await env.settle()

    refetched = await env.bot.get_channel(channel.id).fetch_message(message.id)
    assert [str(r.emoji) for r in refetched.reactions] == ["🔥"]
    assert "MESSAGE_REACTION_REMOVE_EMOJI" in env.transcript()


async def test_clear_reactions_dispatches_raw_event(env, channel, alice):
    seen: list[int] = []

    @env.bot.listen()
    async def on_raw_reaction_clear(payload):
        seen.append(payload.message_id)

    message = await alice.send(channel, "react!")
    await alice.react(message, "👍")
    cached = await env.bot.get_channel(channel.id).fetch_message(message.id)
    await cached.clear_reactions()
    await env.settle()

    assert seen == [message.id]


async def test_clear_reactions_requires_manage_messages(env, alice):
    locked = env.guild.create_text_channel(
        "locked", overwrites={env.guild.default_role: discord.PermissionOverwrite(manage_messages=False)}
    )
    message = await alice.send(locked, "react!")
    await alice.react(message, "👍")

    cached = await env.bot.get_channel(locked.id).fetch_message(message.id)
    with pytest.raises(discord.Forbidden) as exc_info:
        await cached.clear_reactions()
    assert exc_info.value.code == 50013
