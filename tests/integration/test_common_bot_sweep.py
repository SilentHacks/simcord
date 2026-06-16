"""The 1.0 common-bot route sweep: Guild.delete, Member.fetch_voice,
TextChannel.follow and Guild.vanity_invite, each exercised through the real
discord.py method and asserted against settable backend state.
"""

import discord
import pytest

import simcord

# --- Guild.delete (owner-only) ----------------------------------------------


@pytest.mark.filterwarnings("ignore:delete is deprecated:DeprecationWarning")
async def test_delete_guild_requires_ownership(env):
    """The bot is never a guild owner by default, so Guild.delete() is forbidden."""
    guild = env.bot.get_guild(env.guild.id)
    with pytest.raises(discord.Forbidden) as exc:
        await guild.delete()
    assert exc.value.code == 50013


@pytest.mark.filterwarnings("ignore:delete is deprecated:DeprecationWarning")
async def test_owner_can_delete_guild(env):
    """When the bot owns the guild, delete removes it and fires GUILD_DELETE."""
    backend = env.backend
    backend.guilds[env.guild.id].owner_id = backend.bot_user.id
    channel = env.guild.create_text_channel("doomed")  # exercise channel cleanup
    guild = env.bot.get_guild(env.guild.id)

    await guild.delete()
    await env.settle()

    assert env.bot.get_guild(env.guild.id) is None
    assert env.guild.id not in backend.guilds
    assert channel.id not in backend.channels


# --- Member.fetch_voice (GET voice-states) ----------------------------------


async def test_fetch_member_voice_state(env):
    voice = env.guild.create_voice_channel("Voice")
    alice = env.guild.add_member(env.create_user("alice"))
    await alice.join_voice(voice)
    await env.settle()

    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    state = await member.fetch_voice()
    assert state.channel is not None
    assert state.channel.id == voice.id


async def test_fetch_voice_state_when_disconnected_fails_loudly(env):
    bob = env.guild.add_member(env.create_user("bob"))
    await env.settle()

    member = env.bot.get_guild(env.guild.id).get_member(bob.id)
    with pytest.raises(discord.HTTPException):
        await member.fetch_voice()


# --- TextChannel.follow (POST followers) ------------------------------------


async def test_follow_announcement_channel(env):
    news = env.guild.create_news_channel("announce")
    relay = env.guild.create_text_channel("relay")
    await env.settle()

    source = env.bot.get_channel(news.id)
    destination = env.bot.get_channel(relay.id)
    webhook = await source.follow(destination=destination)

    # The forwarding webhook is created in the destination channel.
    assert env.backend.get_webhook(webhook.id).channel_id == relay.id


async def test_follow_non_news_source_rejected(env):
    """The followers endpoint only accepts a news source (discord.py also guards
    this client-side); dispatched directly, a plain text source fails loudly."""
    from simcord.http import router

    plain = env.guild.create_text_channel("plain")
    relay = env.guild.create_text_channel("relay")
    await env.settle()

    with pytest.raises(simcord.BackendError):
        router.dispatch(
            env.backend,
            "POST",
            f"/channels/{plain.id}/followers",
            json={"webhook_channel_id": str(relay.id)},
        )


# --- Guild.vanity_invite (GET vanity-url) -----------------------------------


async def test_vanity_invite_none_by_default(env):
    guild = env.bot.get_guild(env.guild.id)
    assert await guild.vanity_invite() is None


async def test_vanity_invite_when_set(env):
    env.guild.create_text_channel("general")
    env.guild.set_vanity_url("cool-server")
    await env.settle()

    guild = env.bot.get_guild(env.guild.id)
    invite = await guild.vanity_invite()
    assert invite is not None
    assert invite.code == "cool-server"


async def test_set_vanity_url_without_channel_errors(env):
    other = env.create_guild("Empty")
    with pytest.raises(simcord.SetupError):
        other.set_vanity_url("nope")
