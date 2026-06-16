import discord
import pytest

import simcord


async def test_reaction_idempotent_and_remove_unknown(env, channel, alice):
    message = await alice.send(channel, "react!")
    await alice.react(message, "👍")
    await alice.react(message, "👍")  # adding the same reaction twice is a no-op

    backend_message = env.backend.get_message(channel.id, message.id)
    assert backend_message.reaction_for("👍").user_ids.count(alice.id) == 1

    # Removing a reaction that was never added fails loudly.
    with pytest.raises(simcord.BackendError):
        env.backend.remove_reaction(channel.id, message.id, "🔥", alice.id)


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


async def test_bot_add_list_and_remove_reactions(env, channel, alice):
    message = await alice.send(channel, "react!")
    await alice.react(message, "👍")
    await env.settle()

    cached = await env.bot.get_channel(channel.id).fetch_message(message.id)
    # The bot adds its own reaction (PUT .../reactions/{emoji}/@me).
    await cached.add_reaction("👍")
    await env.settle()

    refetched = await env.bot.get_channel(channel.id).fetch_message(message.id)
    reaction = discord.utils.get(refetched.reactions, emoji="👍")
    assert reaction.count == 2

    # List who reacted (GET .../reactions/{emoji}).
    user_ids = {user.id async for user in reaction.users()}
    assert {alice.id, env.bot.user.id} <= user_ids

    # The bot removes its own reaction (DELETE .../@me).
    await refetched.remove_reaction("👍", env.bot.user)
    await env.settle()

    # Then removes another member's reaction (DELETE .../{user_id}, manage_messages).
    again = await env.bot.get_channel(channel.id).fetch_message(message.id)
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    await again.remove_reaction("👍", member)
    await env.settle()

    final = await env.bot.get_channel(channel.id).fetch_message(message.id)
    assert final.reactions == []


async def test_reaction_users_empty_for_absent_emoji(env, channel, alice):
    """GET reactions for an emoji nobody used returns an empty list, not an error."""
    from simcord.http import router

    message = await alice.send(channel, "hi")
    await env.settle()

    users = router.dispatch(
        env.backend,
        "GET",
        f"/channels/{channel.id}/messages/{message.id}/reactions/%F0%9F%91%8D",
    )
    assert users == []


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
