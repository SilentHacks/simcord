"""Bot/system users and test-driven webhook posting.

Covers the message-provenance surface a moderation bot branches on — is the
author a human, a bot/application member, or a webhook? — plus the richer
account and guild attributes the builders now seed.
"""

import discord
import pytest

import simcord


async def test_bot_member_message_has_author_bot(env, channel):
    """A user created with bot=True posts messages with author.bot True and no webhook_id."""
    bot_member = env.guild.add_member(env.create_user("AppBot", bot=True))
    await env.settle()

    message = await bot_member.send(channel, "reaction roles below")

    assert message.author.bot is True
    assert message.webhook_id is None
    # And the bot under test sees the same when it reads the channel back.
    cached = env.bot.get_channel(channel.id)
    fetched = await anext(cached.history(limit=1))
    assert fetched.author.bot is True


async def test_human_member_message_is_not_bot(env, channel, alice):
    await env.settle()
    message = await alice.send(channel, "just chatting")
    assert message.author.bot is False
    assert message.webhook_id is None


async def test_create_user_account_attributes_reflected(env):
    user = env.create_user(
        "verified",
        bot=True,
        system=True,
        global_name="Verified Bot",
        discriminator="4242",
        public_flags=discord.PublicUserFlags(verified_bot=True),
    )
    # Handle properties expose the same state for assertions.
    assert user.bot is True
    assert user.system is True
    assert user.global_name == "Verified Bot"
    assert user.discriminator == "4242"

    member = env.guild.add_member(user)
    await env.settle()

    fetched = env.bot.get_user(user.id)
    assert fetched.bot is True
    assert fetched.system is True
    assert fetched.global_name == "Verified Bot"
    assert fetched.discriminator == "4242"
    assert fetched.public_flags.verified_bot is True
    assert member.member is not None


async def test_create_user_defaults_are_plain_human(env):
    user = env.create_user("plain")
    assert user.bot is False
    assert user.system is False
    assert user.global_name is None
    assert user.discriminator == "0"

    env.guild.add_member(user)
    await env.settle()
    fetched = env.bot.get_user(user.id)
    assert fetched.bot is False
    # With no explicit display name, the payload mirrors the username.
    assert fetched.global_name == "plain"
    assert fetched.public_flags.value == 0


async def test_webhook_send_sets_provenance(env, channel):
    """A webhook post arrives with webhook_id set and author.bot True."""
    hook = env.guild.create_webhook(channel, "Announcer")
    message = await hook.send("deploy finished", username="CI Bot")

    assert message.webhook_id == hook.id
    assert message.author.bot is True
    # The per-message username override is what the message displays under.
    assert message.author.name == "CI Bot"

    cached = env.bot.get_channel(channel.id)
    fetched = await anext(cached.history(limit=1))
    assert fetched.webhook_id == hook.id
    assert fetched.author.bot is True


async def test_webhook_send_defaults_to_webhook_name(env, channel):
    hook = env.guild.create_webhook(channel, "News")
    message = await hook.send("headline")
    assert message.author.name == "News"
    assert message.webhook_id == hook.id


async def test_webhook_send_embed(env, channel):
    hook = env.guild.create_webhook(channel, "Embeds")
    embed = discord.Embed(title="Release", description="1.0 is out")
    message = await hook.send(embed=embed)

    assert message.embeds[0].title == "Release"
    assert message.embeds[0].description == "1.0 is out"
    assert message.author.bot is True


async def test_webhook_send_multiple_embeds(env, channel):
    hook = env.guild.create_webhook(channel, "Embeds")
    embeds = [discord.Embed(title="One"), discord.Embed(title="Two")]
    message = await hook.send(embeds=embeds)
    assert [e.title for e in message.embeds] == ["One", "Two"]


async def test_webhook_send_embed_and_embeds_are_exclusive(env, channel):
    hook = env.guild.create_webhook(channel, "Embeds")
    with pytest.raises(simcord.SetupError):
        await hook.send(embed=discord.Embed(title="x"), embeds=[discord.Embed(title="y")])


async def test_webhook_send_attachment(env, channel):
    hook = env.guild.create_webhook(channel, "Files")
    message = await hook.send("see attached", attachments=[("log.txt", b"hello bytes")])
    assert message.attachments[0].filename == "log.txt"
    assert message.webhook_id == hook.id


async def test_webhook_handle_repr_and_props(env, channel):
    hook = env.guild.create_webhook(channel, "Announcer")
    assert hook.name == "Announcer"
    assert hook.channel_id == channel.id
    assert repr(hook).startswith("<WebhookHandle")


async def test_moderation_distinguishes_message_sources(env, channel):
    """The three provenance rows a 'delete everything except bots/webhooks' bot needs."""
    human = env.guild.add_member(env.create_user("human"))
    bot_member = env.guild.add_member(env.create_user("BotMember", bot=True))
    hook = env.guild.create_webhook(channel, "Hook")
    await env.settle()

    human_msg = await human.send(channel, "delete me")
    bot_msg = await bot_member.send(channel, "keep me (bot)")
    hook_msg = await hook.send("keep me (webhook)")

    assert (human_msg.author.bot, human_msg.webhook_id) == (False, None)
    assert (bot_msg.author.bot, bot_msg.webhook_id) == (True, None)
    assert hook_msg.author.bot is True and hook_msg.webhook_id == hook.id


async def test_create_guild_seeds_settings(env):
    guild = env.create_guild(
        "Configured",
        description="a configured guild",
        verification_level=discord.VerificationLevel.high,
        notifications=discord.NotificationLevel.only_mentions,
        content_filter=discord.ContentFilter.all_members,
        preferred_locale="en-GB",
        afk_timeout=900,
    )
    await env.settle()

    cached = env.bot.get_guild(guild.id)
    assert cached.description == "a configured guild"
    assert cached.verification_level is discord.VerificationLevel.high
    assert cached.default_notifications is discord.NotificationLevel.only_mentions
    assert cached.explicit_content_filter is discord.ContentFilter.all_members
    assert str(cached.preferred_locale) == "en-GB"
    assert cached.afk_timeout == 900


async def test_create_guild_custom_owner(env):
    owner = env.create_user("Boss")
    guild = env.create_guild("Owned", owner=owner)
    await env.settle()

    assert env.backend.get_guild(guild.id).owner_id == owner.id
    cached = env.bot.get_guild(guild.id)
    assert cached is not None
    assert cached.owner_id == owner.id
    # The owner is a real member, so discord.py can resolve Guild.owner.
    assert cached.get_member(owner.id) is not None
    assert cached.owner is not None and cached.owner.id == owner.id


async def test_create_guild_defaults_have_synthetic_owner(env):
    guild = env.create_guild("Default")
    await env.settle()
    backend_guild = env.backend.get_guild(guild.id)
    # The bot must never own a guild by default (owners bypass permission checks).
    assert backend_guild.owner_id != env.bot.user.id
    # The synthetic owner is a member too, so Guild.owner resolves.
    assert backend_guild.owner_id in backend_guild.members
    cached = env.bot.get_guild(guild.id)
    assert cached.owner is not None and cached.owner.id == backend_guild.owner_id
