"""Deterministic acceptance tests for 2.0 settlement semantics.

Mirrors the scenarios GuildMate's settlement-contract document requires:
no test-side re-settle loops, no sleeps, no flaking on executor-backed work.
"""

import asyncio
import contextvars
import gc
import sys
import threading
import time
import weakref
from typing import Any

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

    async with simcord.run(bot, settle_timeout=0.02) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(asyncio.TimeoutError) as exc_info:
            await alice.send(channel, "stuck")
        release.set()
        cause = exc_info.value.__cause__ or exc_info.value
        text = str(cause)
        assert "bot-owned" in text
        assert "unknown wait" in text


@pytest.mark.asyncio
async def test_external_wait_scope_allows_intentional_parking():
    """Only an explicit external_wait declaration may park bot-owned work."""
    stop = asyncio.Event()
    bot = create_bot()

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")

        @bot.listen("on_message")
        async def waiter(message: discord.Message) -> None:
            if message.content == "park":
                await env.external_wait(stop.wait(), reason="test release")

        await alice.send(channel, "park")
        assert not stop.is_set()
        stop.set()
        await alice.send(channel, "wake")


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
            await env.external_wait(wake.wait(), reason="late worker trigger")

            async def worker() -> None:
                await asyncio.sleep(0.2)
                await message.channel.send("worked")

            asyncio.get_running_loop().create_task(worker())

        asyncio.get_running_loop().create_task(late_spawner())

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "arm")  # parks the spawner; baseline settles
        wake.set()  # wakes it between settles; it spawns the worker now
        await env.settle()  # must join the freshly spawned worker
        assert channel.last_message is not None
        assert channel.last_message.content == "worked"


@pytest.mark.asyncio
async def test_call_soon_chain_is_joined_before_actor_returns():
    bot = create_bot()
    spawned: set[asyncio.Task[discord.Message]] = set()

    @bot.listen("on_message")
    async def callback_chain(message: discord.Message) -> None:
        if message.content != "chain":
            return
        loop = asyncio.get_running_loop()

        def step(remaining: int) -> None:
            if remaining:
                loop.call_soon(step, remaining - 1)
            else:
                task = loop.create_task(message.channel.send("chain done"))
                spawned.add(task)
                task.add_done_callback(spawned.discard)

        loop.call_soon(step, 4)

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "chain")
        assert channel.last_message is not None
        assert channel.last_message.content == "chain done"


@pytest.mark.asyncio
async def test_timeout_preserves_owned_work_for_later_settle():
    release = asyncio.Event()
    bot = create_bot()

    @bot.listen("on_message")
    async def delayed(message: discord.Message) -> None:
        if message.content == "stuck":
            await release.wait()
            await message.channel.send("recovered")

    async with simcord.run(bot, settle_timeout=0.01) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(asyncio.TimeoutError):
            await alice.send(channel, "stuck")
        release.set()
        await env.settle(timeout=1)
        assert channel.last_message is not None
        assert channel.last_message.content == "recovered"


@pytest.mark.asyncio
async def test_zero_settlement_timeout_is_valid_and_diagnostic():
    bot = create_bot()

    @bot.listen("on_message")
    async def never(message: discord.Message) -> None:
        if message.content == "wait":
            await asyncio.Future()

    async with simcord.run(bot, settle_timeout=0) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(asyncio.TimeoutError, match="timeout=0"):
            await alice.send(channel, "wait")


@pytest.mark.asyncio
async def test_overlapping_actor_rejects_before_second_mutation():
    release = asyncio.Event()
    bot = create_bot()

    @bot.listen("on_message")
    async def hold(message: discord.Message) -> None:
        if message.content == "first":
            await release.wait()

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        first = asyncio.create_task(alice.send(channel, "first"))
        await asyncio.sleep(0)
        second = asyncio.create_task(alice.send(channel, "second"))
        with pytest.raises(simcord.SetupError):
            await second
        assert [message.content for message in channel.history()] == ["first"]
        release.set()
        await first


@pytest.mark.asyncio
@pytest.mark.parametrize("composition", ["gather", "shield", "wait", "task_group"])
async def test_composed_wait_for_parks_and_resumes(composition: str):
    bot = create_bot()

    @bot.listen("on_message")
    async def composed_wait(message: discord.Message) -> None:
        if message.content != composition:
            return
        waiting = bot.wait_for("message", check=lambda item: item.content == "release")
        if composition == "gather":
            await asyncio.gather(waiting)
        elif composition == "shield":
            await asyncio.shield(waiting)
        elif composition == "wait":
            task = asyncio.create_task(waiting)
            await asyncio.wait({task})
        else:
            async with asyncio.TaskGroup() as group:
                group.create_task(waiting)
        await message.channel.send(f"resumed {composition}")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, composition)
        await alice.send(channel, "release")
        assert channel.last_message is not None
        assert channel.last_message.content == f"resumed {composition}"


@pytest.mark.asyncio
async def test_view_and_modal_waits_park():
    bot = create_bot()

    class WaitingModal(discord.ui.Modal, title="Waiting"):
        name = discord.ui.TextInput(label="Name")

    @bot.listen("on_message")
    async def view_wait(message: discord.Message) -> None:
        if message.content != "view":
            return
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Wait", custom_id="wait"))
        await message.channel.send("view parked", view=view)
        await view.wait()

    @bot.tree.command(name="modal-wait")
    async def modal_wait(interaction: discord.Interaction) -> None:
        modal = WaitingModal()
        await interaction.response.send_modal(modal)
        await modal.wait()

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "view")
        assert channel.last_message is not None
        assert channel.last_message.content == "view parked"
        result = await alice.slash(channel, "modal-wait")
        assert result.modal is not None
        assert result.modal["title"] == "Waiting"


@pytest.mark.asyncio
async def test_detached_task_errors_are_captured_only_when_unhandled():
    bot = create_bot()

    @bot.listen("on_message")
    async def spawn(message: discord.Message) -> None:
        async def fail() -> None:
            raise RuntimeError(message.content)

        task = asyncio.create_task(fail())
        if message.content == "handled":
            with pytest.raises(RuntimeError):
                await task

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "handled")
        assert env.errors == []
        await alice.send(channel, "unhandled")
        assert len(env.errors) == 1
        assert str(env.errors[0]) == "unhandled"


@pytest.mark.asyncio
async def test_completed_bot_tasks_are_collectable():
    bot = create_bot()
    completed: list[weakref.ReferenceType[asyncio.Task[None]]] = []

    @bot.listen("on_message")
    async def remember_task(_message: discord.Message) -> None:
        task = asyncio.current_task()
        assert task is not None
        completed.append(weakref.ref(task))

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "remember")
        await asyncio.sleep(0)
        gc.collect()
        assert completed[0]() is None


@pytest.mark.asyncio
async def test_restart_cancels_bot_work_but_not_caller_work():
    bot_release = asyncio.Event()
    caller_release = asyncio.Event()
    bot_cancelled = asyncio.Event()
    caller = asyncio.create_task(caller_release.wait())
    bot = create_bot()

    @bot.listen("on_message")
    async def parked(message: discord.Message) -> None:
        if message.content != "park":
            return
        try:
            await env.external_wait(bot_release.wait(), reason="restart probe")
        finally:
            bot_cancelled.set()

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "park")
        await env.restart_bot(create_bot())
        assert bot_cancelled.is_set()
        assert not caller.done()

    assert not caller.done()
    caller_release.set()
    await caller


@pytest.mark.asyncio
async def test_cancelled_actor_keeps_owned_work_recoverable():
    started = asyncio.Event()
    release = asyncio.Event()
    bot = create_bot()

    @bot.listen("on_message")
    async def delayed(message: discord.Message) -> None:
        if message.content != "cancel":
            return
        started.set()
        await release.wait()
        await message.channel.send("finished after cancellation")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        action = asyncio.create_task(alice.send(channel, "cancel"))
        await started.wait()
        action.cancel()
        with pytest.raises(asyncio.CancelledError):
            await action
        release.set()
        await env.settle()
        assert channel.last_message is not None
        assert channel.last_message.content == "finished after cancellation"


@pytest.mark.asyncio
async def test_external_wait_in_app_command_parks_and_resumes():
    release = asyncio.Event()
    bot = create_bot()

    @bot.tree.command(name="park-command")
    async def park_command(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("command parked")
        await env.external_wait(release.wait(), reason="command release")
        await interaction.followup.send("command resumed")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        result = await alice.slash(channel, "park-command")
        assert result.response is not None
        assert result.response.content == "command parked"
        release.set()
        await env.settle()
        assert channel.last_message is not None
        assert channel.last_message.content == "command resumed"


@pytest.mark.asyncio
async def test_startup_owned_sleep_parks_and_is_cancelled_on_teardown():
    bot = create_bot()
    original_setup = bot.setup_hook
    sleepers: list[asyncio.Task[None]] = []

    async def setup_hook() -> None:
        await original_setup()
        sleepers.append(asyncio.create_task(asyncio.sleep(3600)))

    bot.setup_hook = setup_hook
    async with simcord.run(bot):
        assert len(sleepers) == 1
        assert not sleepers[0].done()
    assert sleepers[0].cancelled()


@pytest.mark.asyncio
async def test_unrelated_parked_child_cannot_hide_unknown_wait():
    bot = create_bot()

    @bot.listen("on_message")
    async def masked_unknown_wait(message: discord.Message) -> None:
        if message.content != "mask":
            return
        parked_child = asyncio.create_task(
            bot.wait_for("message", check=lambda item: item.content == "release")
        )
        assert not parked_child.done()
        await asyncio.Event().wait()

    async with simcord.run(bot, settle_timeout=0.01) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(TimeoutError, match="masked_unknown_wait"):
            await alice.send(channel, "mask")


@pytest.mark.asyncio
async def test_cancellation_cleanup_error_is_reported():
    bot = create_bot()

    @bot.listen("on_message")
    async def fail_during_cleanup(message: discord.Message) -> None:
        if message.content != "park":
            return

        async def child() -> None:
            try:
                await env.external_wait(asyncio.Event().wait(), reason="teardown")
            except asyncio.CancelledError as error:
                raise RuntimeError("cleanup failed") from error

        child_task = asyncio.create_task(child())
        assert not child_task.done()

    with pytest.raises(ExceptionGroup, match="bot raised 1 error"):
        async with simcord.run(bot) as env:
            guild = env.create_guild()
            alice = guild.add_member(env.create_user("alice"))
            channel = guild.create_text_channel("general")
            await alice.send(channel, "park")


@pytest.mark.asyncio
async def test_bot_owned_work_cannot_call_test_operations():
    release = asyncio.Event()
    attempted = asyncio.Event()
    outcome: list[str] = []
    bot = create_bot()

    @bot.listen("on_message")
    async def attempt_builder(message: discord.Message) -> None:
        if message.content != "try":
            return
        await env.external_wait(release.wait(), reason="operation probe")
        try:
            env.create_user("intruder")
        except simcord.SetupError as error:
            outcome.append(str(error))
        finally:
            attempted.set()

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "try")
        release.set()
        await attempted.wait()
        assert outcome and "bot-owned" in outcome[0]


@pytest.mark.asyncio
async def test_continuous_progress_cannot_extend_settlement_deadline():
    bot = create_bot()

    @bot.listen("on_message")
    async def busy(message: discord.Message) -> None:
        if message.content != "busy":
            return
        while True:  # noqa: ASYNC110 - deliberately exercises perpetual progress
            await asyncio.sleep(0)

    async with simcord.run(bot, settle_timeout=0.01) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(TimeoutError, match="busy"):
            await alice.send(channel, "busy")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "eager",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                sys.version_info[:2] == (3, 11), reason="Python 3.11 has no eager task factory"
            ),
        ),
    ],
)
async def test_custom_and_eager_task_factories_are_supported(eager: bool):
    loop = asyncio.get_running_loop()
    original = loop.get_task_factory()

    def custom_factory(
        factory_loop: asyncio.AbstractEventLoop, coro: Any, **kwargs: Any
    ) -> asyncio.Task[Any]:
        return asyncio.Task(coro, loop=factory_loop, **kwargs)

    loop.set_task_factory(asyncio.eager_task_factory if eager else custom_factory)
    try:
        bot = create_bot()
        async with simcord.run(bot) as env:
            guild = env.create_guild()
            alice = guild.add_member(env.create_user("alice"))
            channel = guild.create_text_channel("general")
            await alice.send(channel, "!ping")
            assert channel.last_message is not None
            assert channel.last_message.content == "Pong!"
    finally:
        loop.set_task_factory(original)


@pytest.mark.asyncio
async def test_external_wait_in_prefix_and_component_callbacks():
    prefix_release = asyncio.Event()
    component_release = asyncio.Event()
    bot = create_bot()

    @bot.command(name="parkprefix")
    async def parkprefix(ctx: Any) -> None:
        await env.external_wait(prefix_release.wait(), reason="prefix release")
        await ctx.send("prefix resumed")

    class ParkView(discord.ui.View):
        @discord.ui.button(label="Park", custom_id="park")
        async def park(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
            await interaction.response.defer()
            await env.external_wait(component_release.wait(), reason="component release")
            await interaction.followup.send("component resumed")

    @bot.tree.command(name="park-component")
    async def park_component(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("component ready", view=ParkView(timeout=None))

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")

        await alice.send(channel, "!parkprefix")
        prefix_release.set()
        await env.settle()
        assert channel.last_message is not None
        assert channel.last_message.content == "prefix resumed"

        shown = await alice.slash(channel, "park-component")
        await alice.click(shown.response.message, custom_id="park")
        component_release.set()
        await env.settle()
        assert channel.last_message is not None
        assert channel.last_message.content == "component resumed"


@pytest.mark.asyncio
async def test_explicit_contexts_keep_bot_provenance():
    marker = contextvars.ContextVar("settlement_marker", default="missing")
    bot = create_bot()
    created: list[asyncio.Task[None]] = []

    @bot.listen("on_message")
    async def schedule_with_context(message: discord.Message) -> None:
        if message.content != "contexts":
            return
        loop = asyncio.get_running_loop()
        context = contextvars.Context()
        context.run(marker.set, "explicit")

        async def send_from_task() -> None:
            await message.channel.send(marker.get())

        def schedule_callback() -> None:
            created.append(loop.create_task(send_from_task(), context=context))

        created.append(loop.create_task(send_from_task(), context=context))
        loop.call_soon(schedule_callback, context=context)

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "contexts")
        assert [item.content for item in channel.history() if item.author.bot] == [
            "explicit",
            "explicit",
        ]
        assert all(task.done() for task in created)


@pytest.mark.asyncio
async def test_executor_threadsafe_descendant_is_joined():
    bot = create_bot()

    @bot.listen("on_message")
    async def executor_callback(message: discord.Message) -> None:
        if message.content != "executor-child":
            return
        loop = asyncio.get_running_loop()

        def spawn() -> None:
            loop.call_soon_threadsafe(lambda: loop.create_task(message.channel.send("executor child")))

        await loop.run_in_executor(None, spawn)

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "executor-child")
        assert channel.last_message is not None
        assert channel.last_message.content == "executor child"


@pytest.mark.asyncio
async def test_composed_unknown_future_does_not_park_parent():
    bot = create_bot()

    @bot.listen("on_message")
    async def mixed_wait(message: discord.Message) -> None:
        if message.content != "mixed-future":
            return
        loop = asyncio.get_running_loop()
        unknown = loop.create_future()
        await asyncio.gather(bot.wait_for("message"), unknown)

    async with simcord.run(bot, settle_timeout=0.01) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(asyncio.TimeoutError, match="unknown wait"):
            await alice.send(channel, "mixed-future")


@pytest.mark.asyncio
async def test_direct_awaited_wait_for_task_parks_and_resumes():
    bot = create_bot()

    @bot.listen("on_message")
    async def direct_wait(message: discord.Message) -> None:
        if message.content != "direct-wait":
            return
        waiter = asyncio.create_task(bot.wait_for("message", check=lambda item: item.content == "release"))
        await waiter
        await message.channel.send("direct resumed")

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "direct-wait")
        await alice.send(channel, "release")
        assert channel.last_message is not None
        assert channel.last_message.content == "direct resumed"


@pytest.mark.asyncio
async def test_detach_drains_descendants_spawned_by_cancellation_cleanup():
    bot = create_bot()
    started = asyncio.Event()
    release = asyncio.Event()
    spawned: list[asyncio.Task[None]] = []

    @bot.listen("on_message")
    async def parked(message: discord.Message) -> None:
        if message.content != "cancel-cleanup":
            return
        try:
            started.set()
            await env.external_wait(release.wait(), reason="cleanup release")
        finally:

            async def never_child() -> None:
                await asyncio.Future()

            spawned.append(asyncio.create_task(never_child()))

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "cancel-cleanup")
        await started.wait()
    assert spawned and spawned[0].cancelled()


@pytest.mark.asyncio
async def test_delayed_handled_task_exception_is_not_captured():
    bot = create_bot()

    @bot.listen("on_message")
    async def delayed_handler(message: discord.Message) -> None:
        if message.content != "delayed-handle":
            return

        async def fail() -> None:
            raise RuntimeError("handled later")

        task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="handled later"):
            await task

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        await alice.send(channel, "delayed-handle")
        assert env.errors == []


@pytest.mark.asyncio
async def test_timeout_zero_reports_immediate_callback_label_and_guidance():
    bot = create_bot()

    @bot.listen("on_message")
    async def callback_loop(message: discord.Message) -> None:
        if message.content != "callback-timeout":
            return
        loop = asyncio.get_running_loop()

        def pending_callback() -> None:
            loop.call_soon(pending_callback)

        loop.call_soon(pending_callback)

    async with simcord.run(bot, settle_timeout=0) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        channel = guild.create_text_channel("general")
        with pytest.raises(asyncio.TimeoutError) as caught:
            await alice.send(channel, "callback-timeout")
        text = str(caught.value)
        assert "pending_callback" in text
        assert "external_wait" in text
        assert "advance_time" in text
