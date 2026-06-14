import discord
import pytest


async def test_stage_instance_lifecycle(env):
    stage = env.guild.create_stage_channel("Town Hall")
    await env.settle()

    cached = env.bot.get_channel(stage.id)
    instance = await cached.create_instance(topic="Weekly Q&A")
    await env.settle()
    assert instance.topic == "Weekly Q&A"

    fetched = await cached.fetch_instance()
    assert fetched.topic == "Weekly Q&A"

    await instance.edit(topic="Updated")
    await env.settle()
    assert (await cached.fetch_instance()).topic == "Updated"

    await instance.delete()
    await env.settle()
    try:
        await cached.fetch_instance()
    except discord.HTTPException:
        pass
    else:
        raise AssertionError("stage instance should be gone after delete")


async def test_duplicate_stage_instance_rejected(env):
    stage = env.guild.create_stage_channel("Town Hall")
    await env.settle()
    cached = env.bot.get_channel(stage.id)
    await cached.create_instance(topic="first")
    await env.settle()

    with pytest.raises(discord.HTTPException) as exc:
        await cached.create_instance(topic="second")
    assert exc.value.code == 50035


async def test_deleting_stage_channel_closes_instance(env):
    stage = env.guild.create_stage_channel("Town Hall")
    await env.settle()
    cached = env.bot.get_channel(stage.id)
    await cached.create_instance(topic="live")
    await env.settle()

    await cached.delete()
    await env.settle()
    assert stage.id not in env.backend.stage_instances
