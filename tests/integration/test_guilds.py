import discord
import pytest


async def test_bot_creates_guild_at_runtime(env):
    # discord.py deprecates Client.create_guild (bots in 10+ guilds cannot use
    # it), but the route is still served for the bots that can.
    with pytest.warns(DeprecationWarning):
        created = await env.bot.create_guild(name="Fresh Server")
    await env.settle()

    assert created.name == "Fresh Server"
    cached = env.bot.get_guild(created.id)
    assert cached is not None
    # A bot that creates a guild owns it.
    assert env.backend.get_guild(created.id).owner_id == env.bot.user.id


async def test_guild_edit_name(env):
    guild = env.bot.get_guild(env.guild.id)
    await guild.edit(name="Renamed")
    await env.settle()

    assert env.bot.get_guild(env.guild.id).name == "Renamed"
    updates = [e for e in env.guild.audit_log() if e.action_type == 1]  # GUILD_UPDATE
    assert updates
    assert any(c["key"] == "name" and c["new_value"] == "Renamed" for c in updates[-1].changes)


async def test_guild_edit_settings(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    system_channel = guild.get_channel(channel.id)
    await guild.edit(
        description="A test guild",
        verification_level=discord.VerificationLevel.high,
        default_notifications=discord.NotificationLevel.only_mentions,
        explicit_content_filter=discord.ContentFilter.all_members,
        afk_timeout=900,
        preferred_locale=discord.Locale.american_english,
        system_channel=system_channel,
    )
    await env.settle()

    refetched = await env.bot.fetch_guild(env.guild.id)
    assert refetched.description == "A test guild"
    assert refetched.verification_level is discord.VerificationLevel.high
    assert refetched.default_notifications is discord.NotificationLevel.only_mentions
    assert refetched.explicit_content_filter is discord.ContentFilter.all_members
    assert refetched.afk_timeout == 900
    assert env.backend.get_guild(env.guild.id).system_channel_id == channel.id


async def test_guild_edit_clears_nullable_field(env):
    guild = env.bot.get_guild(env.guild.id)
    await guild.edit(description="temporary")
    await env.settle()
    assert env.backend.get_guild(env.guild.id).description == "temporary"

    await guild.edit(description=None)
    await env.settle()
    assert env.backend.get_guild(env.guild.id).description is None


async def test_guild_edit_requires_manage_guild(env):
    guild = env.bot.get_guild(env.guild.id)
    mask = ~discord.Permissions(manage_guild=True).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask

    with pytest.raises(discord.Forbidden) as exc_info:
        await guild.edit(name="nope")
    assert exc_info.value.code == 50013
