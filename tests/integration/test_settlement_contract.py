"""Deterministic acceptance tests for 2.0 settlement semantics.

Mirrors the scenarios GuildMate's settlement-contract document requires:
no test-side re-settle loops, no sleeps, no flaking on executor-backed work.
"""

import asyncio
import threading
import time

import discord
import pytest

import simcord
from fixtures.sample_bot import create_bot


@pytest.mark.asyncio
async def test_delayed_executor_response_is_joined():
    """A handler blocked in run_in_executor longer than the old idle window
    still completes before the actor verb returns."""
    log: list[str] = []
    bot = create_bot()

    @bot.listen("on_message")
    async def slow_worker(message: discord.Message) -> None:
        if message.content != "go":
            return
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: (time.sleep(0.3), log.append("executor"))
        )
        await message.channel.send("done")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "go")
        assert channel.last_message is not None
        assert channel.last_message.content == "done"
        assert log == ["executor"]


@pytest.mark.asyncio
async def test_to_thread_mutation_exists_on_actor_return():
    """asyncio.to_thread work plus a subsequent Discord mutation is complete
    when the verb returns."""
    log: list[str] = []
    bot = create_bot()

    @bot.listen("on_message")
    async def thread_worker(message: discord.Message) -> None:
        if message.content != "go":
            return
        await asyncio.to_thread(time.sleep, 0.2)
        log.append("thread")
        await message.channel.send("done")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "go")
        assert channel.last_message is not None
        assert channel.last_message.content == "done"
        assert log == ["thread"]


@pytest.mark.asyncio
async def test_true_external_input_waiter_is_parked():
    """A caller-rooted task waiting on an unset Event is classified parked:
    settle() returns and the waiter keeps running."""
    stop = asyncio.Event()
    bot = create_bot()
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        waiter = asyncio.get_running_loop().create_task(stop.wait())
        await env.settle()  # direct settle: caller-rooted waiter left alone
        assert not waiter.done()
        await alice.send(channel, "ping")  # verbs still work beside it
        assert channel.last_message is not None
        stop.set()


@pytest.mark.asyncio
async def test_mixed_parked_waiter_and_active_executor_work():
    """A parked background task beside an event handler awaiting executor
    work: settle ignores the waiter but joins the handler."""
    done = asyncio.Event()

    async def parked_waiter():
        await done.wait()

    bot = create_bot()

    @bot.listen("on_message")
    async def worker(message: discord.Message) -> None:
        if message.content != "go":
            return
        await asyncio.to_thread(time.sleep, 0.2)
        await message.channel.send("done")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        waiter = asyncio.get_running_loop().create_task(parked_waiter())
        await env.settle()  # baseline the waiter as pre-existing
        await alice.send(channel, "go")
        assert channel.last_message is not None
        assert channel.last_message.content == "done"
        assert not waiter.done()
        done.set()


@pytest.mark.asyncio
async def test_timeout_names_event_and_task_with_hint():
    """An event-owned worker that never finishes raises TimeoutError naming
    the dispatched event and why the task was considered active."""
    bot = create_bot()
    release = threading.Event()

    @bot.listen("on_message")
    async def stuck_worker(message: discord.Message) -> None:
        if message.content != "stuck":
            return
        await asyncio.to_thread(lambda: release.wait(timeout=10))

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")

        async def bounded():
            await alice.send(channel, "stuck")

        with pytest.raises(asyncio.TimeoutError) as exc_info:
            await asyncio.wait_for(bounded(), timeout=6)
        release.set()
        cause = exc_info.value.__cause__ or exc_info.value
        text = str(cause)
        assert "MEMBER.send" in text
        assert "externally-woken" in text
        assert "background_names" in text


@pytest.mark.asyncio
async def test_background_names_hatch_allows_intentional_park():
    """A handler whitelisted via background_names parks cleanly instead of
    stalling settle()."""
    bot = create_bot()

    @bot.listen("on_message")
    async def my_waiter(message: discord.Message) -> None:
        if message.content == "park":
            await asyncio.Event().wait()

    async with simcord.run(bot, background_names={"my_waiter"}) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "park")  # returns without joining the waiter


@pytest.mark.asyncio
async def test_startup_machinery_is_joined_before_ready():
    """Startup settles join login/setup_hook machinery even though it is
    rooted in the attaching coroutine (regression guard): READY must have
    fired before the first verb runs."""
    bot = create_bot()
    async with simcord.run(bot) as env:
        assert env.bot.is_ready()


@pytest.mark.asyncio
async def test_wait_for_listener_parks_cleanly():
    """A handler awaiting Client.wait_for parks; later input resolves it and
    the next verb joins the continuation."""
    import asyncio as aio

    bot = create_bot()
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")

        async def arm_and_wait():
            try:
                await bot.wait_for("message", check=lambda m: m.channel.id == channel.id, timeout=5)
                return True
            except TimeoutError:
                return False

        waiter_task = aio.get_running_loop().create_task(arm_and_wait())
        await env.settle()  # direct settle: caller-rooted waiter left alone
        await alice.send(channel, "wake")  # resolves wait_for + joins
        assert await aio.wait_for(waiter_task, timeout=2) is True
        assert channel.last_message is not None


@pytest.mark.asyncio
async def test_descendant_outliving_its_root_is_joined():
    """A handler that spawns a slower child and returns immediately does not
    let settle() finish early: the orphaned-descendant chain stays event-owned
    until the child completes."""
    log: list[str] = []
    bot = create_bot()

    @bot.listen("on_message")
    async def spawning_handler(message: discord.Message) -> None:
        if message.content != "go":
            return

        async def slow_child() -> None:
            await asyncio.sleep(0.2)
            log.append("child")
            await message.channel.send("done")

        asyncio.get_running_loop().create_task(slow_child())  # handler returns at once

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "go")
        assert log == ["child"]
        assert channel.last_message is not None
        assert channel.last_message.content == "done"


@pytest.mark.asyncio
async def test_resumed_wait_for_handler_is_joined_by_next_verb():
    """A handler that parks on Client.wait_for and later resumes in place is
    rejoined by the settling that woke it — its continuation (the reply) is
    complete when the waking verb returns, with no test-side awaits."""
    bot = create_bot()

    @bot.listen("on_message")
    async def armed_waiter(message: discord.Message) -> None:
        if message.content != "arm":
            return
        await bot.wait_for("message", check=lambda m: m.content == "fire", timeout=30)
        # Real continuation work: long enough that a settle() ignoring the
        # resumed task returns before this reply exists.
        await asyncio.sleep(0.2)
        await message.channel.send("fired")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "arm")  # parks the handler; settle returns
        await alice.send(channel, "fire")  # resumes it in place; must be joined
        assert channel.last_message is not None
        assert channel.last_message.content == "fired"


@pytest.mark.asyncio
async def test_three_level_descendant_chain_is_joined():
    """Ownership must walk up through several live ancestors to reach the
    event-window root: root spawns middle, middle spawns leaf, both parents
    finish first. The leaf is still joined."""
    log: list[str] = []
    bot = create_bot()

    @bot.listen("on_message")
    async def chain_root(message: discord.Message) -> None:
        if message.content != "go":
            return

        async def middle() -> None:
            await asyncio.sleep(0.05)

            async def leaf() -> None:
                await asyncio.sleep(0.15)
                log.append("leaf")
                await message.channel.send("done")

            asyncio.get_running_loop().create_task(leaf())

        asyncio.get_running_loop().create_task(middle())

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "go")
        assert log == ["leaf"]
        assert channel.last_message is not None
        assert channel.last_message.content == "done"


@pytest.mark.asyncio
async def test_work_spawned_between_settles_by_machinery_is_joined():
    """Parked bot machinery that wakes between settles and spawns new work
    has that work rooted into the next settle's window — no test-side
    re-settles, no sleeps."""
    wake = asyncio.Event()
    bot = create_bot()

    @bot.listen("on_message")
    async def armer(message: discord.Message) -> None:
        if message.content != "arm":
            return

        async def late_spawner() -> None:
            await wake.wait()

            async def worker() -> None:
                await asyncio.sleep(0.2)
                await message.channel.send("worked")

            asyncio.get_running_loop().create_task(worker())

        asyncio.get_running_loop().create_task(late_spawner())

    async with simcord.run(bot, background_names=["late_spawner"]) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "arm")  # parks the spawner; baseline settles
        wake.set()  # wakes it between settles; it spawns the worker now
        await env.settle()  # must join the freshly spawned worker
        assert channel.last_message is not None
        assert channel.last_message.content == "worked"
