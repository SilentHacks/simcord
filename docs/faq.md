---
title: "FAQ"
description: "Frequently asked questions about SimCord, the discord.py testing framework: how it compares to dpytest, whether it connects to Discord, Terms of Service, async setup, mocking vs. simulation, and supported features."
---

# Frequently asked questions

## What is SimCord?

SimCord is a **testing framework for [discord.py](https://github.com/Rapptz/discord.py)
bots**. It gives your real, unmodified bot a faithful but entirely in-memory Discord to run
against, so you can test prefix commands, slash commands, buttons, modals, permissions and
events — with no network, no bot token, and no test server. See the
[overview](index.md) and [quickstart](quickstart.md).

## Does it connect to Discord? Is it against the Terms of Service?

No, and no. SimCord **never opens a socket**. It replaces discord.py's two transport seams
(the REST client and the gateway parser) with an in-memory backend, so nothing reaches
Discord's servers. There is deliberately no "integration mode" — automating a real client
would violate Discord's Terms of Service, and this project exists precisely to make that
unnecessary. See the [architecture](architecture.md).

## How is it different from dpytest?

[dpytest](https://github.com/CraftSpider/dpytest) started this approach. SimCord covers
the **modern interaction surface** dpytest doesn't — slash commands, context menus,
components, modals, autocomplete — and enforces **real permissions with authentic Discord
error codes**. It also replaces dpytest's module-global API with explicit
[builders and actors](concepts.md), so messages are sent *by someone, from somewhere*, which
is what makes permission-sensitive bugs testable. There's a full
[migration guide](migrating-from-dpytest.md).

## Is this a mock library?

It's more than a mock — it's a **simulator**. A mock returns canned responses; SimCord
maintains real, consistent state. When your bot sends a message, the same backend produces
both the REST response *and* the `MESSAGE_CREATE` gateway event, so your bot's cache and
listeners behave exactly as in production. Permissions, the interaction lifecycle and
validation limits are all computed, not faked.

## Do I have to change my bot to test it?

No. SimCord runs your **real, unmodified bot**. The only requirement is that you can
*construct* it from a function (so tests can build a fresh instance) — see the
[quickstart](quickstart.md). Your `setup_hook`, extension loading and `tree.sync()` all run
for real.

## Why do my async tests need no decorator?

The `pytest` extra installs `pytest-asyncio`. With `asyncio_mode = "auto"` set in your
`pyproject.toml`, `async def test_...` functions run automatically. See
[Fixtures & configuration](guides/fixtures.md#async-test-setup).

## Do I need `asyncio.sleep` to wait for the bot to reply?

No — and you shouldn't. Every [actor](concepts.md#actors-act-as-a-real-user) verb waits for
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

It raises `RouteNotImplemented` naming the exact route — never a silent fake success. Check
the [parity matrix](parity-matrix.md) for what's implemented, and
[open a parity-gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md)
for what you need.

## Can I run more than one bot in a test?

Not yet — an `Env` currently drives a single bot (the backend can broadcast to multiple
clients, but the multi-bot driver isn't implemented). Sharding and rate-limit simulation are
also deliberately out of scope. See the [parity matrix](parity-matrix.md).

## Which Python and discord.py versions are supported?

Python **3.11+** and discord.py **2.7+**. See [Installation](installation.md).

## Still stuck?

- Browse the [guides](guides/messages.md) and [recipes](cookbook.md).
- Check the [API reference](api.md).
- [Open an issue](https://github.com/SilentHacks/simcord/issues) on GitHub.
