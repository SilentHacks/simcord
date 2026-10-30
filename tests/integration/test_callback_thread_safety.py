"""Owned native producers and loop callbacks share one callback lifecycle."""

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import simcord
from fixtures.sample_bot import create_bot


async def test_actor_joins_callback_invoked_before_worker_publishes_handle():
    bot = create_bot()
    loop = asyncio.get_running_loop()
    invoked = asyncio.Event()
    effects = []
    native_done = threading.Event()

    def callback():
        effects.append("callback")
        invoked.set()

    def worker():
        loop.call_soon_threadsafe(callback)
        effects.append("native")
        native_done.set()

    @bot.command()
    async def publish(ctx):
        # Deliberately leave the facade unawaited: actor settlement must join
        # both native completion and its thread-safe callback.
        loop.run_in_executor(None, worker)
        await ctx.send("published")

    async with simcord.run(bot) as env:
        guild = env.create_guild("callbacks")
        channel = guild.create_text_channel("publication")
        actor = guild.add_member(env.create_user("alice"))
        # The loop can reenter the metadata lock, but the native producer must
        # wait to publish its returned Handle. This forces invocation first,
        # without replacing the scheduler or instrumenting source lines.
        with env._work._callback_lock:
            sending = asyncio.create_task(actor.send(channel, "!publish"))
            await asyncio.wait_for(invoked.wait(), timeout=5)
            assert effects == ["callback"]
            assert not native_done.is_set()
        await asyncio.wait_for(sending, timeout=5)
        assert effects == ["callback", "native"]
        assert native_done.is_set()
        assert channel.last_message.content == "published"
        assert not env.errors


@pytest.mark.parametrize("fail", [False, True])
async def test_actor_joins_native_producers_and_only_uncancelled_callbacks(fail):
    bot = create_bot()
    loop = asyncio.get_running_loop()
    marker = contextvars.ContextVar("callback_producer")
    producers = 3
    registered = threading.Barrier(producers + 1, timeout=10)
    effects = []
    native_done = set()

    def callback(producer, index):
        assert marker.get() == producer
        effects.append((producer, index))
        if fail and (producer, index) == (0, 1):
            raise ValueError("callback failure")

    def worker(producer):
        marker.set(producer)
        for index in range(120):
            handle = loop.call_soon_threadsafe(callback, producer, index)
            if index % 3 == 0:
                handle.cancel()
        # The command holds the loop at the same barrier, so cancellation is
        # guaranteed to precede execution, not merely likely on a fast worker.
        registered.wait()
        native_done.add(producer)
        if fail and producer == 1:
            raise RuntimeError("producer failure")

    with ThreadPoolExecutor(max_workers=producers) as executor:

        @bot.command()
        async def produce(ctx):
            for producer in range(producers):
                loop.run_in_executor(executor, worker, producer)
            registered.wait()
            await ctx.send("produced")

        async with simcord.run(bot, check_errors=False) as env:
            guild = env.create_guild("callbacks")
            channel = guild.create_text_channel("producers")
            actor = guild.add_member(env.create_user("alice"))
            await actor.send(channel, "!produce")
            expected = {
                (producer, index) for producer in range(producers) for index in range(120) if index % 3
            }
            assert set(effects) == expected
            assert len(effects) == len(expected)
            assert native_done == set(range(producers))
            assert channel.last_message.content == "produced"
            if fail:
                assert sorted(str(error) for error in env.errors) == [
                    "callback failure",
                    "producer failure",
                ]
            else:
                assert not env.errors
