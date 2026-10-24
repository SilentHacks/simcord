"""Native executor lifetime is independent of the cancelable asyncio facade."""

import asyncio
import contextvars
import gc
import multiprocessing
import threading
import weakref
from concurrent import futures
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor, ThreadPoolExecutor

import discord
import pytest
from discord.ext import commands

import simcord
from fixtures.sample_bot import create_bot


async def _submit(env, executor, worker):
    async def submit():
        return asyncio.get_running_loop().run_in_executor(executor, worker)

    with env._bot_scope():
        task = asyncio.create_task(submit())
    return await task


@pytest.mark.parametrize("cancel_running", [False, True])
async def test_native_work_and_threadsafe_descendant_survive_timeout(cancel_running):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    descendant_entered = asyncio.Event()
    descendant_release = asyncio.Event()
    release = threading.Event()
    log = []
    env = simcord.Env(create_bot(), settle_timeout=0.02)
    await env.start()

    async def descendant():
        descendant_entered.set()
        await descendant_release.wait()
        log.append("descendant")

    def worker():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        loop.call_soon_threadsafe(lambda: asyncio.create_task(descendant()))
        log.append("worker")

    try:
        caller = await _submit(env, None, worker)
        await entered.wait()
        if cancel_running:
            caller.cancel()
        with pytest.raises(TimeoutError, match="native work"):
            await env.settle()
        release.set()
        await descendant_entered.wait()
        with pytest.raises(TimeoutError, match="unknown wait"):
            await env.settle()
        descendant_release.set()
        await env.settle(timeout=1)
        assert log == ["worker", "descendant"]
        assert caller.cancelled() is cancel_running
    finally:
        release.set()
        descendant_release.set()
        await env.shutdown()


async def test_queued_cancellation_does_not_join_foreign_running_work():
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    called = []
    executor = ThreadPoolExecutor(max_workers=1)

    def foreign():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)

    foreign_caller = loop.run_in_executor(executor, foreign)
    await entered.wait()
    try:
        async with simcord.run(create_bot(), settle_timeout=0.05) as env:
            caller = await _submit(env, executor, lambda: called.append(True))
            caller.cancel()
            await env.settle()
            assert not foreign_caller.done()
            assert called == []
        assert not foreign_caller.done()
        release.set()
        await foreign_caller
        # Env neither replaces nor shuts down a supplied/shared executor.
        assert await loop.run_in_executor(executor, lambda: 42) == 42
    finally:
        release.set()
        await foreign_caller
        executor.shutdown()


@pytest.mark.parametrize("pool_kind", ["process", "interpreter"])
async def test_isolated_native_submission_preserves_picklable_callable(pool_kind):
    loop = asyncio.get_running_loop()
    if pool_kind == "process":
        pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    else:
        factory = getattr(futures, "InterpreterPoolExecutor", None)
        if factory is None:
            pytest.skip("this Python has no isolated interpreter pool")
        pool = factory(max_workers=1)
    with pool as executor:
        async with simcord.run(create_bot()) as env:
            with env._bot_scope():
                caller = loop.run_in_executor(executor, pow, 2, 10)
            assert await caller == 1024
            await env.settle()
            assert not env.errors
            assert not env._work.executors


async def test_completed_callable_is_released_while_its_owner_remains_live():
    loop = asyncio.get_running_loop()
    finished = asyncio.Event()
    release = asyncio.Event()
    references = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        async with simcord.run(create_bot()) as env:

            async def owner():
                def worker():
                    return 42

                references.append(weakref.ref(worker))
                caller = loop.run_in_executor(executor, worker)
                assert await caller == 42
                del caller, worker
                finished.set()
                await env.external_wait(release.wait(), reason="keep the parent alive")

            with env._bot_scope():
                task = asyncio.create_task(owner())
            try:
                await finished.wait()
                await env.settle()
                # Let the same native worker discard its previous work item.
                await loop.run_in_executor(executor, id, None)
                gc.collect()
                assert not task.done()
                assert references[0]() is None
            finally:
                release.set()
                await task


async def test_worker_and_submission_errors_keep_native_semantics():
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    executor.shutdown()
    async with simcord.run(create_bot(), check_errors=False) as env:
        with env._bot_scope(), pytest.raises(RuntimeError, match="shutdown"):
            loop.run_in_executor(executor, lambda: None)
        assert not env._work.executors

        def fail():
            raise ValueError("native failure")

        caller = await _submit(env, None, fail)
        await env.settle()
        assert any(isinstance(error, ValueError) for error in env.errors)
        with pytest.raises(ValueError, match="native failure"):
            await caller


async def test_awaited_worker_failure_is_not_reported_as_unobserved():
    async with simcord.run(create_bot()) as env:

        def fail():
            raise ValueError("handled")

        async def handle():
            with pytest.raises(ValueError, match="handled"):
                await asyncio.get_running_loop().run_in_executor(None, fail)

        with env._bot_scope():
            handler = asyncio.create_task(handle())
        await handler
        await env.settle()
        assert not env.errors


@pytest.mark.parametrize("handled", [False, True])
async def test_failed_pool_initializer_retires_work_without_consuming_failure(handled):
    called = []

    def initialize():
        raise RuntimeError("initializer failed")

    executor = ThreadPoolExecutor(max_workers=1, initializer=initialize)
    try:
        async with simcord.run(create_bot(), check_errors=False) as env:
            caller = await _submit(env, executor, lambda: called.append("entered"))
            if handled:
                with pytest.raises(BrokenExecutor):
                    await caller
            else:
                # Wait for native failure without observing its exception.
                await asyncio.wait((caller,))
            await env.settle()
            assert not env._work.executors
            assert called == []
            if handled:
                assert not env.errors
            else:
                assert len(env.errors) == 1
                assert isinstance(env.errors[0], BrokenExecutor)
            with pytest.raises(BrokenExecutor):
                await caller
    finally:
        executor.shutdown()


@pytest.mark.parametrize("operation", ["restart", "shutdown"])
@pytest.mark.parametrize("sharded", [False, True])
async def test_lifecycle_timeout_retains_native_generation_and_hooks(operation, sharded):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    release = threading.Event()
    entered = asyncio.Event()
    if sharded:
        bot = commands.AutoShardedBot(command_prefix="!", intents=discord.Intents.all(), shard_count=2)

        @bot.command()
        async def ping(ctx):
            await ctx.send("Pong!")
    else:
        bot = create_bot()
    received = []

    @bot.listen("on_message")
    async def record_message(message):
        received.append(message.content)

    env = simcord.Env(bot, settle_timeout=0.02)
    await env.start()
    bot = env.bot
    generation = env._generation

    def worker():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)

    try:
        await _submit(env, None, worker)
        await entered.wait()
        with pytest.raises(TimeoutError):
            if operation == "restart":
                await env.restart_bot(create_bot())
            else:
                await env.shutdown()
        assert env._started
        assert env._generation == generation
        assert env.bot is bot
        assert env._work.executors
        assert loop.run_in_executor != original
        release.set()
        await env.settle(timeout=1)
        # A failed detach must leave the old SDK machinery usable, not merely
        # retain lifecycle flags. Populate a fresh cache entry through the feed.
        guild = env.create_guild("after timeout", **({"shard_id": 1} if sharded else {}))
        channel = guild.create_text_channel("still-connected")
        alice = guild.add_member(env.create_user("alice"))
        await env.settle(timeout=1)
        cached = bot.get_channel(channel.id)
        assert cached is not None and cached.name == "still-connected"
        assert bot.get_guild(guild.id).get_member(alice.id) is not None
        if sharded:
            assert bot.get_guild(guild.id).shard_id == 1
            await bot.change_presence(status=discord.Status.idle, shard_id=1)
            assert not bot.get_shard(1).is_closed()
        await alice.send(channel, "!ping")
        assert channel.last_message.content == "Pong!"
        assert received == ["!ping", "Pong!"]
        webhook = await cached.create_webhook(name="after timeout")
        posted = await webhook.send("adapter still attached", wait=True)
        await env.settle(timeout=1)
        assert posted.content == channel.last_message.content == "adapter still attached"
        assert received[-1] == "adapter still attached"
        if operation == "restart":
            await env.restart_bot(create_bot())
            assert env._generation == generation + 1
        await env.shutdown()
        await env.shutdown()
        assert loop.run_in_executor == original
    finally:
        release.set()
        await env.shutdown()


async def test_failed_attach_retains_live_native_work_until_later_cleanup():
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    release = threading.Event()
    entered = asyncio.Event()
    bot = create_bot()

    def worker():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)

    async def setup_hook():
        loop.run_in_executor(None, worker)
        await entered.wait()
        raise ValueError("failed setup")

    bot.setup_hook = setup_hook
    env = simcord.Env(bot, settle_timeout=0.02)
    try:
        with pytest.raises(TimeoutError, match="native executor work remains tracked"):
            await env.start()
        assert env._work.executors
        assert loop.run_in_executor != original
        release.set()
        await env.settle(timeout=1)
        await env.shutdown()
        await env.shutdown()
        assert loop.run_in_executor == original
        env.bot = create_bot()
        await env.start()
        assert env.bot.is_ready()
    finally:
        release.set()
        await env.shutdown()


async def test_cancelled_running_future_does_not_hide_worker_error():
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    env = simcord.Env(create_bot(), check_errors=False, settle_timeout=0.02)
    await env.start()

    def worker():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        raise ValueError("cancelled worker failure")

    try:
        caller = await _submit(env, None, worker)
        await entered.wait()
        caller.cancel()
        with pytest.raises(TimeoutError, match="native work"):
            await env.settle()
        release.set()
        await env.settle(timeout=1)
        assert caller.cancelled()
        assert any(str(error) == "cancelled worker failure" for error in env.errors)
    finally:
        release.set()
        await env.shutdown()


def test_original_default_executor_policy_and_shutdown_validation():
    loop = asyncio.new_event_loop()
    supplied = ThreadPoolExecutor(max_workers=1)

    async def exercise():
        async with simcord.run(create_bot()) as env:
            first = await _submit(env, None, lambda: 42)
            assert await first == 42
            await env.settle()
            await loop.shutdown_default_executor()
            with env._bot_scope(), pytest.raises(RuntimeError):
                loop.run_in_executor(None, lambda: None)
            caller = await _submit(env, supplied, lambda: 43)
            assert await caller == 43
            await env.settle()

    try:
        loop.run_until_complete(exercise())
    finally:
        supplied.shutdown()
        loop.close()


async def test_debug_executor_validation_checks_original_callable():
    loop = asyncio.get_running_loop()
    debug = loop.get_debug()
    async with simcord.run(create_bot()) as env:

        async def invalid_worker():
            return 42

        try:
            loop.set_debug(True)
            with env._bot_scope(), pytest.raises(TypeError, match="coroutines cannot be used"):
                loop.run_in_executor(None, invalid_worker)
            coroutine = invalid_worker()
            try:
                with env._bot_scope(), pytest.raises(TypeError, match="coroutines cannot be used"):
                    loop.run_in_executor(None, coroutine)
            finally:
                coroutine.close()
            with env._bot_scope(), pytest.raises(TypeError, match="a callable object was expected"):
                loop.run_in_executor(None, 42)
            assert not env._work.executors
        finally:
            loop.set_debug(debug)


async def test_custom_executor_override_preserves_setup_and_caller_policy():
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    calls = []
    observed = []
    returned = []

    def custom(executor, func, *args):
        calls.append(func)
        caller = original(executor, func, *args)
        caller.add_done_callback(lambda done: observed.append(done.result()))
        returned.append(caller)
        return caller

    bot = create_bot()

    async def setup_hook():
        assert await loop.run_in_executor(None, lambda: "setup") == "setup"

    bot.setup_hook = setup_hook
    loop.run_in_executor = custom
    try:
        async with simcord.run(bot) as env:
            assert env.bot.is_ready()
            assert await loop.run_in_executor(None, lambda: "unowned") == "unowned"
            caller = await _submit(env, None, lambda: "owned")
            assert caller is returned[-1]
            assert await caller == "owned"
            await env.settle()
            assert observed == ["setup", "unowned", "owned"]
            assert len(calls) == 3
            assert not env.errors
        assert loop.run_in_executor is custom
    finally:
        loop.run_in_executor = original


@pytest.mark.parametrize("cancel_running", [False, True])
async def test_custom_forwarding_cancellation_keeps_actual_work_owned(cancel_running):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    executor = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    entered = asyncio.Event()
    calls = []
    native_calls = []

    def blocking():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)

    def custom(pool, func, *args):
        native = original(pool, func, *args)
        native_calls.append(native)
        caller = loop.create_future()

        def deliver(done):
            result = done.result()
            if not caller.done():
                caller.set_result(result)

        # This policy deliberately does not forward caller cancellation.
        native.add_done_callback(deliver)
        return caller

    foreign = None
    if not cancel_running:
        foreign = original(executor, blocking)
        await entered.wait()
    loop.run_in_executor = custom
    env = simcord.Env(create_bot(), settle_timeout=0.02)
    try:
        await env.start()

        def worker():
            calls.append("entered")
            blocking()

        caller = await _submit(env, executor, worker)
        if cancel_running:
            await entered.wait()
        caller.cancel()
        with pytest.raises(TimeoutError, match="native work"):
            await env.settle()
        release.set()
        if foreign is not None:
            await foreign
        await asyncio.gather(*native_calls)
        await env.settle(timeout=1)
        assert calls == ["entered"]
        assert caller.cancelled()
    finally:
        release.set()
        await env.shutdown()
        loop.run_in_executor = original
        if foreign is not None:
            await foreign
        await asyncio.gather(*native_calls)
        executor.shutdown()


@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_custom_error_policy_uses_caller_outcome_unless_cancelled(cancel_caller):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    observed = []
    entered = asyncio.Event()
    release = threading.Event()
    policy_completed = asyncio.Event()

    def custom(executor, func, *args):
        native = original(executor, func, *args)
        caller = loop.create_future()

        def recover(done):
            observed.append(str(done.exception()))
            if not caller.done():
                caller.set_result("recovered")
            policy_completed.set()

        native.add_done_callback(recover)
        return caller

    def fail():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        raise ValueError("policy handled this")

    loop.run_in_executor = custom
    try:
        async with simcord.run(create_bot(), check_errors=False) as env:
            try:
                caller = await _submit(env, None, fail)
                await entered.wait()
                if cancel_caller:
                    caller.cancel()
                release.set()
                await env.settle()
                if cancel_caller:
                    assert caller.cancelled()
                    assert [str(error) for error in env.errors] == ["policy handled this"]
                    # The canceled facade cannot expose its policy's hidden
                    # native Future. Observe that separate boundary explicitly.
                    await policy_completed.wait()
                else:
                    assert await caller == "recovered"
                    assert not env.errors
                assert observed == ["policy handled this"]
            finally:
                release.set()
    finally:
        loop.run_in_executor = original


@pytest.mark.parametrize("fail_submission", [False, True])
async def test_custom_synchronous_invocation_is_registered_before_entry(fail_submission):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    called = []
    env = simcord.Env(create_bot(), check_errors=False)

    def custom(executor, func, *args):
        result = func(*args)
        if fail_submission:
            raise RuntimeError("submission failed after execution")
        caller = loop.create_future()
        caller.set_result(result)
        return caller

    def worker():
        assert len(env._work.executors) == 1
        called.append(True)
        return 42

    loop.run_in_executor = custom
    try:
        await env.start()
        if fail_submission:
            with env._bot_scope(), pytest.raises(RuntimeError, match="after execution"):
                loop.run_in_executor(None, worker)
        else:
            caller = await _submit(env, None, worker)
            assert await caller == 42
        await env.settle()
        assert called == [True]
        assert not env._work.executors
        assert not env.errors
    finally:
        await env.shutdown()
        loop.run_in_executor = original


async def test_submission_failure_after_launch_keeps_worker_error_and_descendant():
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    release = threading.Event()
    entered = threading.Event()
    threads = []
    descendants = []
    env = simcord.Env(create_bot(), check_errors=False, settle_timeout=0.02)

    def custom(executor, func, *args):
        thread = threading.Thread(target=invoke, args=(func, args))
        threads.append(thread)
        thread.start()
        assert entered.wait(10)
        raise RuntimeError("submission failed after launch")

    def invoke(func, args):
        try:
            func(*args)
        except ValueError:
            pass

    def worker():
        entered.set()
        assert release.wait(10)
        loop.call_soon_threadsafe(lambda: descendants.append("callback"))
        raise ValueError("launched worker failed")

    loop.run_in_executor = custom
    try:
        await env.start()
        with env._bot_scope(), pytest.raises(RuntimeError, match="after launch"):
            loop.run_in_executor(None, worker)
        with pytest.raises(TimeoutError, match="native work"):
            await env.settle()
        release.set()
        await env.settle(timeout=1)
        assert descendants == ["callback"]
        assert any(str(error) == "launched worker failed" for error in env.errors)
        assert not env._work.executors
    finally:
        release.set()
        await env.shutdown()
        loop.run_in_executor = original
        for thread in threads:
            thread.join(timeout=10)


@pytest.mark.parametrize("early_error", [False, True])
async def test_early_outcome_does_not_abandon_later_execution(early_error):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    queued = []
    called = []
    env = simcord.Env(create_bot(), settle_timeout=0.02)

    def custom(executor, func, *args):
        queued.append((func, args))
        caller = loop.create_future()
        if early_error:
            caller.set_exception(BrokenExecutor("early policy error"))
        else:
            caller.set_result("early policy result")
        return caller

    loop.run_in_executor = custom
    try:
        await env.start()
        caller = await _submit(env, None, lambda: called.append("real work"))
        if early_error:
            with pytest.raises(BrokenExecutor):
                await caller
        else:
            assert await caller == "early policy result"
        with pytest.raises(TimeoutError, match="native work"):
            await env.settle()
        func, args = queued.pop()
        func(*args)
        await env.settle(timeout=1)
        assert called == ["real work"]
        assert not env._work.executors
    finally:
        for func, args in queued:
            func(*args)
        await env.shutdown()
        loop.run_in_executor = original


async def test_work_owner_retires_waits_callbacks_and_tasks_on_restart():
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = asyncio.Event()
    async with simcord.run(create_bot()) as env:
        work = env._work
        generation = env._generation

        async def waiting():
            entered.set()
            await env.external_wait(release.wait(), reason="restart ownership check")

        with env._bot_scope():
            task = asyncio.create_task(waiting())
            timer = loop.call_later(3600, lambda: None)
        await entered.wait()
        await env.settle()
        assert task in work.tasks
        assert task in work.external_waits
        assert any(record.handle is timer for record in work.callbacks)
        await env.restart_bot(create_bot())
        assert env._work is work
        assert env._generation == generation + 1
        assert task.cancelled()
        assert task not in work.tasks
        assert task not in work.external_waits
        assert timer.cancelled()
        assert not any(record.handle is timer for record in work.callbacks)


@pytest.mark.parametrize("operation", ["settle", "shutdown"])
async def test_cancelled_facade_does_not_own_hidden_executor_postprocessing(operation):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    original_threadsafe = loop.call_soon_threadsafe
    user_entered = asyncio.Event()
    user_release = threading.Event()
    policy_release = threading.Event()
    policy_completed = asyncio.Event()
    observed = []
    descendants = []
    native_calls = []

    class PostprocessingExecutor(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            def invoke():
                try:
                    return fn(*args, **kwargs)
                finally:
                    # Executor policy runs after the owned callable exits.
                    # The original asyncio native Future is still pending.
                    assert policy_release.wait(10)

            return super().submit(invoke)

    executor = PostprocessingExecutor(max_workers=1)

    def custom(pool, func, *args):
        native = original(pool, func, *args)
        native_calls.append(native)
        caller = loop.create_future()

        def recover(done):
            observed.append(str(done.exception()))
            if not caller.done():
                caller.set_result("recovered")
            policy_completed.set()

        native.add_done_callback(recover)
        return caller

    def worker():
        original_threadsafe(user_entered.set)
        assert user_release.wait(10)
        loop.call_soon_threadsafe(lambda: descendants.append("before completion"))
        raise ValueError("owned callable failed")

    loop.run_in_executor = custom
    env = simcord.Env(create_bot(), check_errors=False, settle_timeout=1)
    try:
        await env.start()
        caller = await _submit(env, executor, worker)
        await user_entered.wait()
        caller.cancel()
        # The drain begins while user code is still running. Only then may it
        # enqueue its descendant and complete; no turn-count assumption.
        drain = asyncio.create_task(getattr(env, operation)())
        loop.call_soon(user_release.set)
        await drain
        assert descendants == ["before completion"]
        assert not env._work.executors
        assert [str(error) for error in env.errors] == ["owned callable failed"]
        assert not native_calls[0].done()
        assert observed == []
        if operation == "settle":
            await env.shutdown()
        assert loop.run_in_executor is custom
        # Original executor/Future policy remains intact after hook restoration.
        policy_release.set()
        with pytest.raises(ValueError, match="owned callable failed"):
            await native_calls[0]
        await policy_completed.wait()
        assert observed == ["owned callable failed"]
        assert caller.cancelled()
    finally:
        user_release.set()
        policy_release.set()
        await env.shutdown()
        loop.run_in_executor = original
        await asyncio.gather(*native_calls, return_exceptions=True)
        executor.shutdown()


async def test_future_policy_registration_context_owns_descendants():
    loop = asyncio.get_running_loop()
    original_call_soon = loop.call_soon
    entered = asyncio.Event()
    release = asyncio.Event()
    policy = loop.create_future()
    async with simcord.run(create_bot(), settle_timeout=0.02) as env:

        async def descendant():
            entered.set()
            await release.wait()

        with env._bot_scope():
            policy.add_done_callback(lambda _: asyncio.create_task(descendant()))
        # Native executor state transfer runs without a bot scope, but the
        # policy callback retains the context in which it was registered.
        original_call_soon(policy.set_result, None, context=contextvars.Context())
        try:
            await entered.wait()
            with pytest.raises(TimeoutError, match="unknown wait"):
                await env.settle()
        finally:
            release.set()
        await env.settle(timeout=1)


@pytest.mark.parametrize("kind", ["task", "executor"])
@pytest.mark.parametrize("handled", [False, True])
async def test_live_owner_gets_first_refusal_of_child_failure(kind, handled):
    loop = asyncio.get_running_loop()
    ready = asyncio.Event()
    release = asyncio.Event()
    children = []
    failure = ValueError("child failure")
    async with simcord.run(create_bot(), check_errors=False) as env:

        def fail():
            raise failure

        async def fail_async():
            fail()

        async def owner():
            child = (
                loop.run_in_executor(None, fail) if kind == "executor" else asyncio.create_task(fail_async())
            )
            children.append(child)
            ready.set()
            await env.external_wait(release.wait(), reason="wait before observing the child")
            if handled:
                with pytest.raises(ValueError) as observed:
                    await child
                assert observed.value is failure
            return "parent completed"

        with env._bot_scope():
            parent = asyncio.create_task(owner())
        try:
            await ready.wait()
            await asyncio.wait(children)
            await env.settle()
            assert not env.errors
            release.set()
            assert await parent == "parent completed"
            await env.settle()
            assert env.errors == ([] if handled else [failure])
        finally:
            release.set()
            await asyncio.gather(parent, return_exceptions=True)


async def test_owned_callback_failure_is_reported_once_and_retires_work():
    loop = asyncio.get_running_loop()
    reached = asyncio.Event()
    failure = ValueError("scheduled callback failure")
    async with simcord.run(create_bot(), check_errors=False) as env:

        def fail():
            reached.set()
            raise failure

        with env._bot_scope():
            loop.call_soon(fail)
        await reached.wait()
        await env.settle()
        assert env.errors == [failure]
        continued = []
        with env._bot_scope():
            loop.call_soon(continued.append, "continued")
        await env.settle()
        assert continued == ["continued"]


@pytest.mark.parametrize("handled", [False, True])
async def test_cancelled_native_job_does_not_consume_later_facade_error(handled):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    entered = asyncio.Event()
    release = threading.Event()
    native_calls = []
    called = []
    failure = ValueError("deferred caller policy failure")
    with ThreadPoolExecutor(max_workers=1) as executor:

        def foreign():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)

        foreign_caller = original(executor, foreign)
        await entered.wait()

        def custom(pool, func, *args):
            native = original(pool, func, *args)
            native.cancel()
            native_calls.append(native)
            return loop.create_future()

        loop.run_in_executor = custom
        caller = None
        try:
            async with simcord.run(create_bot(), check_errors=False) as env:
                caller = await _submit(env, executor, lambda: called.append("executed"))
                await env.settle()
                assert not foreign_caller.done()
                assert not caller.done()
                caller.set_exception(failure)
                if handled:
                    with pytest.raises(ValueError) as observed:
                        await caller
                    assert observed.value is failure
                await env.settle()
                assert env.errors == ([] if handled else [failure])
                assert called == []
        finally:
            release.set()
            await foreign_caller
            await asyncio.gather(*native_calls, return_exceptions=True)
            if caller is not None and caller.done() and not caller.cancelled():
                caller.exception()
            loop.run_in_executor = original
