import asyncio
import contextvars
import datetime
import threading
import time

import discord
import pytest
from discord.ext import tasks

import simcord
from fixtures.sample_bot import create_bot
from simcord.backend import Backend


def test_allocator_does_not_advance_utc_and_reserves_explicit_ids():
    backend = Backend()
    before = backend.clock.now()
    values = {backend.snowflake() for _ in range(5000)}
    assert len(values) == 5000
    assert backend.clock.now() == before
    assert {value >> 22 for value in values} == {next(iter(values)) >> 22}
    following = max(values) + 1
    backend.reserve_id(following)
    assert backend.snowflake() == following + 1
    with pytest.raises(simcord.SetupError, match="already reserved"):
        backend.create_guild("collision", id=following)
    guild = backend.create_guild("explicit", id=123)
    assert guild.roles[guild.id].id == guild.id
    with pytest.raises(simcord.SetupError, match="already reserved"):
        backend.create_guild("duplicate", id=123)


def test_allocator_exhaustion_does_not_advance_utc():
    backend = Backend()
    before = backend.clock.now()
    millisecond = backend.application_id >> 22
    backend._sequences[millisecond] = 1 << 22
    with pytest.raises(simcord.SetupError, match="sequence exhausted"):
        backend.snowflake()
    assert backend.clock.now() == before


async def test_callbacks_observe_intermediate_instants_and_sdk_utc():
    async with simcord.run(create_bot()) as env:
        start = env.now()
        observed = []

        async def owned_worker():
            for _ in range(2):
                await asyncio.sleep(10)
                observed.append((env.now(), discord.utils.utcnow(), time.monotonic()))
                env.backend.make_user("timestamp must not tick")

        with env._bot_scope():
            task = asyncio.create_task(owned_worker())
        await env.advance_time(20)
        assert task.done()
        assert [row[0] - start for row in observed] == [
            datetime.timedelta(seconds=10),
            datetime.timedelta(seconds=20),
        ]
        assert all(utc == sdk for utc, sdk, _ in observed)
        assert observed[1][2] - observed[0][2] == 10
        assert discord.utils.utcnow() == env.now()


async def test_submillisecond_progress_accumulates_without_truncation():
    async with simcord.run(create_bot()) as env:
        before = env.now()
        for _ in range(10):
            await env.advance_time(0.0001)
        assert env.now() - before == datetime.timedelta(milliseconds=1)
        assert discord.utils.utcnow() == env.now()


async def test_sdk_sleep_until_and_calendar_and_relative_loops():
    async with simcord.run(create_bot()) as env:
        start = env.now()
        seen = []

        @tasks.loop(seconds=10, count=3)
        async def relative():
            seen.append(("relative", env.now()))

        @tasks.loop(time=(start + datetime.timedelta(seconds=15)).timetz(), count=1)
        async def calendar():
            seen.append(("calendar", env.now()))

        async def sleep_until():
            await discord.utils.sleep_until(start + datetime.timedelta(seconds=12))
            seen.append(("until", env.now()))

        with env._bot_scope():
            relative.start()
            calendar.start()
            sleeper = asyncio.create_task(sleep_until())
        await env.advance_time(20)
        assert sleeper.done()
        assert seen == [
            ("relative", start),
            ("relative", start + datetime.timedelta(seconds=10)),
            ("until", start + datetime.timedelta(seconds=12)),
            ("calendar", start + datetime.timedelta(seconds=15, microseconds=1)),
            ("relative", start + datetime.timedelta(seconds=20)),
        ]


async def test_resource_deadlines_precede_tied_bot_timers():
    async with simcord.run(create_bot()) as env:
        guild = env.create_guild()
        start = env.now()
        event = guild.create_scheduled_event(
            "boundary",
            start_time=(start + datetime.timedelta(seconds=10)).isoformat(),
            end_time=(start + datetime.timedelta(seconds=20)).isoformat(),
            entity_type=3,
            location="here",
        )
        observed = []

        async def inspect():
            await asyncio.sleep(10)
            observed.append((env.now(), env.backend.get_scheduled_event(guild.id, event.id).status))
            await asyncio.sleep(10)
            observed.append((env.now(), env.backend.get_scheduled_event(guild.id, event.id).status))

        with env._bot_scope():
            task = asyncio.create_task(inspect())
        await env.advance_time(20)
        assert task.done()
        assert observed == [
            (start + datetime.timedelta(seconds=10), 2),
            (start + datetime.timedelta(seconds=20), 3),
        ]


async def test_restart_does_not_resurrect_departed_guilds():
    async with simcord.run(create_bot()) as env:
        departed = env.create_guild("departed")
        retained = env.create_guild("retained")
        await env.settle()
        await env.bot.get_guild(departed.id).leave()
        await env.settle()
        await env.restart_bot(create_bot())
        assert env.bot.get_guild(departed.id) is None
        assert env.bot.get_guild(retained.id) is not None


async def test_prestart_shard_layout_and_historical_identifier():
    bot = discord.AutoShardedClient(intents=discord.Intents(guilds=True), shard_count=2)
    env = simcord.Env(bot)
    before = env.now()
    guild = env.create_guild("shard one", shard_id=1)
    assert (guild.id >> 22) % 2 == 1
    assert env.now() == before
    assert discord.utils.snowflake_time(guild.id) <= before
    await env.start()
    try:
        await env.settle()
        assert bot.get_guild(guild.id) is not None
    finally:
        await env.shutdown()


async def test_manual_clock_context_and_detached_context_stays_real():
    env = simcord.Env(create_bot())
    real = discord.utils.utcnow()
    await env.start()
    try:
        assert discord.utils.utcnow() >= real
        with env.time_context():
            assert discord.utils.utcnow() == env.now()
            detached = contextvars.copy_context()
        with env._bot_scope():
            owned = contextvars.copy_context()
        await env.restart_bot(create_bot())
        assert owned.run(discord.utils.utcnow) >= real
    finally:
        await env.shutdown()
    assert detached.run(discord.utils.utcnow) >= real


async def test_bot_executor_selects_clock_but_driver_executor_does_not():
    async with simcord.run(create_bot()) as env:
        loop = asyncio.get_running_loop()
        observed = []

        async def worker():
            observed.append(await loop.run_in_executor(None, discord.utils.utcnow))

        with env._bot_scope():
            task = asyncio.create_task(worker())
        await env.settle()
        assert task.done()
        assert observed == [env.now()]
        real = datetime.datetime.now(datetime.UTC)
        assert await loop.run_in_executor(None, discord.utils.utcnow) >= real


async def test_independent_loops_share_dispatch_and_restore_original_bindings():
    original = time.monotonic
    barrier = threading.Barrier(2, timeout=10)

    def in_thread(seconds):
        async def exercise():
            async with simcord.run(discord.Client(intents=discord.Intents.none())) as env:
                await env.advance_time(seconds)
                await asyncio.to_thread(barrier.wait)
                assert discord.utils.utcnow() == env.now()
                return env.now()

        try:
            return asyncio.run(exercise())
        except BaseException:
            barrier.abort()
            raise

    first, second = await asyncio.gather(asyncio.to_thread(in_thread, 10), asyncio.to_thread(in_thread, 20))
    assert second - first == datetime.timedelta(seconds=10)
    assert time.monotonic is original


async def test_failed_start_restores_hooks_and_allows_new_environment():
    class Broken(discord.Client):
        async def setup_hook(self):
            raise ValueError("failed startup")

    loop = asyncio.get_running_loop()
    originals = (loop.create_task, loop.call_later, time.monotonic, discord.utils.datetime)
    env = simcord.Env(Broken(intents=discord.Intents.none()))
    with pytest.raises(ValueError, match="failed startup"):
        await env.start()
    assert not env._started
    assert (loop.create_task, loop.call_later, time.monotonic, discord.utils.datetime) == originals
    async with simcord.run(create_bot()) as next_env:
        assert next_env.bot.is_ready()


async def test_advance_timeout_reports_committed_intermediate_progress():
    async with simcord.run(create_bot(), settle_timeout=0.03) as env:
        start = env.now()
        blocked = asyncio.Event()

        async def worker():
            await asyncio.sleep(10)
            await blocked.wait()

        with env._bot_scope():
            task = asyncio.create_task(worker())
        with pytest.raises(TimeoutError) as caught:
            await env.advance_time(20)
        assert env.now() == start + datetime.timedelta(seconds=10)
        assert any(
            "requested" in note and "reached" in note and "partial progress" in note
            for note in caught.value.__notes__
        )
        blocked.set()
        await env.settle()
        await task


async def test_advance_has_one_real_budget_across_intermediate_steps():
    async with simcord.run(create_bot(), settle_timeout=0.05) as env:
        start = env.now()

        async def slow_steps():
            for _ in range(4):
                await asyncio.sleep(10)
                await asyncio.to_thread(time.sleep, 0.03)

        with env._bot_scope():
            task = asyncio.create_task(slow_steps())
        with pytest.raises(TimeoutError):
            await env.advance_time(40)
        assert env.now() < start + datetime.timedelta(seconds=40)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_restart_releases_loop_and_restores_hooks():
    class Broken(discord.Client):
        async def setup_hook(self):
            raise ValueError("failed restart")

    loop = asyncio.get_running_loop()
    original_task = loop.create_task
    original_clock = time.monotonic
    env = simcord.Env(create_bot())
    await env.start()
    with pytest.raises(ValueError, match="failed restart"):
        await env.restart_bot(Broken(intents=discord.Intents.none()))
    assert loop.create_task == original_task
    assert time.monotonic is original_clock
    async with simcord.run(create_bot()) as replacement:
        assert replacement.bot.is_ready()


async def test_sdk_entitlement_expiry_and_native_datetime_types():
    async with simcord.run(create_bot()) as env:
        end = env.now() + datetime.timedelta(days=7)
        entitlement = discord.Entitlement(
            env.bot._connection,
            {
                "id": "123",
                "sku_id": "456",
                "application_id": str(env.backend.application_id),
                "type": 8,
                "deleted": False,
                "ends_at": end.isoformat(),
            },
        )
        assert type(entitlement.ends_at) is datetime.datetime
        assert not entitlement.is_expired()
        facade = discord.utils.datetime
        assert type(facade.datetime(2026, 1, 1)) is datetime.datetime
        assert isinstance(end, facade.datetime)
        assert facade.datetime.now(datetime.timezone(datetime.timedelta(hours=2))) == env.now()
        await env.advance_time(7 * 86400)
        assert entitlement.is_expired()
