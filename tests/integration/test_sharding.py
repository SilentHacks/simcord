import asyncio
import time

import discord
import pytest
from discord.ext import commands

import simcord


def make_sharded_bot(*, shard_count: int | None = None, shard_ids: list[int] | None = None):
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    if shard_ids is not None:
        assert shard_count is not None
        return commands.AutoShardedBot(
            command_prefix="!",
            intents=intents,
            shard_count=shard_count,
            shard_ids=shard_ids,
        )
    return commands.AutoShardedBot(command_prefix="!", intents=intents, shard_count=shard_count)


async def test_auto_sharded_bot_is_ready_and_routes_each_guild_once():
    bot = make_sharded_bot(shard_count=3)
    received: list[tuple[int, str]] = []
    ready_shards: list[int] = []
    ready_calls = 0
    connect_calls = 0

    @bot.event
    async def on_connect() -> None:
        nonlocal connect_calls
        connect_calls += 1

    @bot.event
    async def on_shard_ready(shard_id: int) -> None:
        ready_shards.append(shard_id)

    @bot.event
    async def on_ready() -> None:
        nonlocal ready_calls
        ready_calls += 1

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.guild is not None:
            received.append((message.guild.shard_id, message.content))

    async with simcord.run(bot) as env:
        assert bot.is_ready()
        assert set(bot.shards) == {0, 1, 2}
        assert bot.get_shard(2) is not None
        assert bot.get_shard(3) is None
        assert bot.latencies == [(0, 0.0), (1, 0.0), (2, 0.0)]
        assert ready_shards == [0, 1, 2]
        assert ready_calls == 1

        guild0 = env.create_guild("zero", shard_id=0)
        guild2 = env.create_guild("two", shard_id=2)
        assert (guild0.id >> 22) % 3 == 0
        assert (guild2.id >> 22) % 3 == 2

        channel0 = guild0.create_text_channel("zero")
        channel2 = guild2.create_text_channel("two")
        alice = guild0.add_member(env.create_user("alice"))
        bob = guild2.add_member(env.create_user("bob"))
        await alice.send(channel0, "from zero")
        await bob.send(channel2, "from two")

        assert received == [(0, "from zero"), (2, "from two")]

        cached2 = bot.get_guild(guild2.id)
        assert cached2 is not None
        queried = await cached2.query_members(query="bo", limit=1)
        assert [member.id for member in queried] == [bob.id]

        await bot.change_presence(status=discord.Status.idle, shard_id=2)
        shard2 = bot.get_shard(2)
        assert shard2 is not None
        initial_connect_calls = connect_calls
        await shard2.disconnect()
        assert shard2.is_closed()
        await bob.send(channel2, "lost while disconnected")
        joined_while_disconnected = env.create_guild("offline", shard_id=2)
        await cached2.leave()
        await env.settle()
        assert received == [(0, "from zero"), (2, "from two")]
        assert bot.get_guild(joined_while_disconnected.id) is None
        assert bot.get_guild(guild2.id) is cached2
        await shard2.connect()
        assert not shard2.is_closed()
        await env.settle()
        assert connect_calls == initial_connect_calls + 1
        assert bot.get_guild(joined_while_disconnected.id) is not None
        assert bot.get_guild(guild2.id) is None


async def test_partial_shard_worker_only_receives_owned_guilds():
    bot = make_sharded_bot(shard_count=4, shard_ids=[1, 3])
    received_dms: list[str] = []

    @bot.listen("on_message")
    async def record_dm(message: discord.Message) -> None:
        if message.guild is None:
            received_dms.append(message.content)

    async with simcord.run(bot) as env:
        assert bot.is_ready()
        assert set(bot.shards) == {1, 3}

        owned = env.create_guild("owned", shard_id=3)
        unowned = env.create_guild("unowned", shard_id=2)
        await env.settle()
        await env.create_user("dm-user").send_dm("only shard zero")

        assert bot.get_guild(owned.id) is not None
        assert bot.get_guild(unowned.id) is None
        assert received_dms == []
        with pytest.raises(ValueError, match="between 0 and 3"):
            env.create_guild(shard_id=4)
        with pytest.raises(ValueError, match="does not belong"):
            env.create_guild(id=owned.id, shard_id=1)


async def test_run_accepts_discovered_shard_count_override():
    bot = make_sharded_bot()
    setup_observation: dict[str, object] = {}

    async def setup_hook() -> None:
        setup_observation["shard_count"] = bot.shard_count
        setup_observation["shards"] = dict(bot.shards)

    bot.setup_hook = setup_hook

    async with simcord.run(bot, shard_count=2) as env:
        assert bot.shard_count == 2
        assert set(bot.shards) == {0, 1}
        assert setup_observation == {"shard_count": None, "shards": {}}
        guild = env.create_guild(shard_id=1)
        assert (guild.id >> 22) % 2 == 1


async def test_missing_discovered_shard_count_is_loud():
    bot = make_sharded_bot()
    loop = asyncio.get_running_loop()
    original_create_task = loop.create_task
    original_monotonic = time.monotonic

    with pytest.raises(simcord.SetupError, match="has no shard_count"):
        async with simcord.run(bot):
            pass
    assert loop.create_task == original_create_task
    assert time.monotonic is original_monotonic


async def test_sharded_restart_rebuilds_shards_and_cache():
    bot = make_sharded_bot(shard_count=2)
    async with simcord.run(bot) as env:
        guild = env.create_guild(shard_id=1)

        replacement = make_sharded_bot(shard_count=2)
        await env.restart_bot(replacement)

        assert env.bot is replacement
        assert replacement.is_ready()
        assert set(replacement.shards) == {0, 1}
        assert replacement.get_guild(guild.id) is not None
