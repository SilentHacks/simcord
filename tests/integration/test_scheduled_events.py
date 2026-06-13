import datetime

import discord
import pytest

UTC = datetime.UTC
START = datetime.datetime(2027, 1, 1, 18, 0, tzinfo=UTC)
END = datetime.datetime(2027, 1, 1, 20, 0, tzinfo=UTC)


async def test_create_external_event(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    event = await guild.create_scheduled_event(
        name="Community Meetup",
        start_time=START,
        end_time=END,
        entity_type=discord.EntityType.external,
        location="Online",
    )
    await env.settle()

    assert event.id in env.backend.get_guild(env.guild.id).scheduled_events
    fetched = await guild.fetch_scheduled_events()
    assert [e.name for e in fetched] == ["Community Meetup"]


async def test_create_event_requires_manage_events(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    mask = ~discord.Permissions(manage_events=True).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask
    with pytest.raises(discord.Forbidden):
        await guild.create_scheduled_event(
            name="nope",
            start_time=START,
            end_time=END,
            entity_type=discord.EntityType.external,
            location="Online",
        )


async def test_subscribe_and_status_change(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    event = await guild.create_scheduled_event(
        name="Standup",
        start_time=START,
        end_time=END,
        entity_type=discord.EntityType.external,
        location="HQ",
    )
    await env.settle()

    await alice.subscribe_event(event.id)
    users = [u async for u in event.users()]
    assert [u.id for u in users] == [alice.id]
    assert "GUILD_SCHEDULED_EVENT_USER_ADD" in env.transcript()

    await event.edit(status=discord.EventStatus.active)
    await env.settle()
    assert env.backend.get_guild(env.guild.id).scheduled_events[event.id].status == 2

    await event.delete()
    await env.settle()
    assert event.id not in env.backend.get_guild(env.guild.id).scheduled_events


async def test_voice_channel_event(env, channel):
    voice = env.guild.create_voice_channel("Stage Hall")
    await env.settle()
    guild = env.bot.get_guild(env.guild.id)
    vc = guild.get_channel(voice.id)
    event = await guild.create_scheduled_event(
        name="Voice Hangout",
        start_time=START,
        entity_type=discord.EntityType.voice,
        channel=vc,
    )
    await env.settle()
    assert env.backend.get_guild(env.guild.id).scheduled_events[event.id].channel_id == voice.id
