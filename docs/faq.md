---
title: "FAQ"
description: "Frequently asked questions about SimCord, the discord.py testing framework: offline testing, AI coding agents, Terms of Service, mocking vs. simulation, setup, and supported features."
---

# Frequently asked questions

## What is SimCord?

SimCord is a **testing framework for [discord.py](https://github.com/Rapptz/discord.py)
bots**. It gives your real, unmodified bot a faithful but entirely in-memory Discord to run
against, so you can test prefix commands, slash commands, buttons, modals, permissions and
events with no network, no bot token, and no test server. See the
[overview](index.md) and [quickstart](quickstart.md).

## Does it connect to Discord? Is it against the Terms of Service?

No, and no. SimCord **never opens a socket**. It replaces discord.py's two transport seams
(the REST client and the gateway parser) with an in-memory backend, so nothing reaches
Discord's servers. There is deliberately no "integration mode." Automating a real client
would violate Discord's Terms of Service, and this project exists precisely to make that
unnecessary. See the [architecture](architecture.md).

## Does SimCord collect telemetry?

No. The library makes no outbound network requests and collects no usage or runtime
telemetry. Package hosts and source platforms may publish their own aggregate statistics,
but SimCord does not transmit data from your tests.

## Is this a mock library?

It's more than a mock. It is a **simulator**. A mock returns canned responses; SimCord
maintains real, consistent state. When your bot sends a message, the same backend produces
both the REST response *and* the `MESSAGE_CREATE` gateway event, so your bot's cache and
listeners behave exactly as in production. Permissions, the interaction lifecycle and
validation limits are all computed, not faked.

## Can AI coding agents use SimCord?

Yes. An AI agent can run deterministic behavioral tests against the real bot instead of
inventing Discord mocks or requesting a bot token. Add the instructions from the
[AI coding agent guide](guides/ai-coding-agents.md) to your project, then require a SimCord
test for commands, interactions, permissions, views, events, cache behavior, or sharding.

## Do I have to change my bot to test it?

No. SimCord runs your **real, unmodified bot**. The only requirement is that you can
*construct* it from a function so tests can build a fresh instance. See the
[quickstart](quickstart.md). Your `setup_hook`, extension loading and `tree.sync()` all run
for real.

## Why do my async tests need no decorator?

The `pytest` extra installs `pytest-asyncio`. With `asyncio_mode = "auto"` set in your
`pyproject.toml`, `async def test_...` functions run automatically. See
[Fixtures & configuration](guides/fixtures.md#async-test-setup).

## Do I need `asyncio.sleep` to wait for the bot to reply?

No, and you shouldn't. Every [actor](concepts.md#actors-act-as-a-real-user) verb waits for
the bot to finish reacting before returning, so your assertions never race. SimCord tracks
the bot's tasks and settles the event loop deterministically. If you need to advance *time*
(for cooldowns or view timeouts), use [`env.advance_time`](guides/time-control.md), not a
real sleep.

## My test fails saying a command "was never synced". Why?

Because SimCord enforces sync, just like Discord: a command in your tree that you never
`tree.sync()`'d **can't be invoked**. This catches a very common real bug. Make sure your
`setup_hook` syncs, or pass
[`strict_sync=False`](guides/fixtures.md#configuration-options) for isolated unit tests.

## A test passes but I expected the bot to error?

By default, errors your bot raised but never inspected are
[re-raised at teardown](guides/diagnostics.md#captured-errors-enverrors). If you *expect*
errors, read `env.errors` (which marks them inspected) and assert on them; if you want to
assert the bot ran clean, call `env.raise_errors()`.

## What happens if my bot hits something SimCord doesn't support yet?

It raises `RouteNotImplemented` naming the exact route, never a silent fake success. Check
the [parity matrix](parity-matrix.md) for what's implemented, and
[open a parity-gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md)
for what you need.

## Does SimCord simulate sharding or rate limits?

Sharding is supported for discord.py's single-process `AutoShardedClient` and
`AutoShardedBot`, including partial `shard_ids`, readiness, routing, chunking, presence,
latency and shard controls. See [Testing sharded bots](guides/fixtures.md#testing-sharded-bots).
Multi-process/IPC clusters are not simulated.

Rate limits are deliberately out of scope so tests stay fast; use `inject_error` to exercise
`429` handling. See the [parity matrix](parity-matrix.md).

## Which Python and discord.py versions are supported?

Python **3.11+** and discord.py **2.7+**. See [Installation](installation.md).

## Still stuck?

- Browse the [guides](guides/messages.md) and [recipes](cookbook.md).
- Check the [API reference](api.md).
- [Open an issue](https://github.com/SilentHacks/simcord/issues) on GitHub.
