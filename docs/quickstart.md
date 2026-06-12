---
title: "Quickstart"
description: "Get SimCord running in your discord.py project in five minutes: install, wire up the pytest fixture, and write your first passing bot test."
---

# Quickstart

This page takes you from `pip install` to a passing test in about five minutes. You'll need
a discord.py bot whose construction you can call from a function — see
[Installation](installation.md) for requirements.

## 1. Install

```bash
pip install simcord[pytest]
```

## 2. Make your bot constructible

SimCord needs to *build* your bot without running it. If your bot is created inside a
function, you're already done. If it lives at module scope, extract a factory:

```python
# mybot/__init__.py
import discord
from discord.ext import commands

def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.command()
    async def ping(ctx: commands.Context) -> None:
        await ctx.send("Pong!")

    return bot
```

!!! tip "Your `setup_hook` runs for real"
    SimCord performs the real discord.py login flow, so anything in your `setup_hook` —
    loading extensions, `await bot.tree.sync()` — happens exactly as in production. That's
    a feature: a command you forget to sync is **not invocable** in tests, just like on
    Discord.

## 3. Wire in the fixture

The bundled pytest plugin provides a `simcord_env` fixture. Tell it how to build your bot
by defining a `simcord_bot` fixture in `conftest.py`:

```python
# conftest.py
import pytest
from mybot import create_bot

@pytest.fixture
def simcord_bot():
    return create_bot()
```

That's it — `simcord_env` now hands every test a fully logged-in bot attached to a fresh
virtual Discord. (Prefer explicit control or a different runner? Use
[`async with simcord.run(bot) as env:`](guides/fixtures.md#without-pytest) directly.)

## 4. Write a test

```python
# test_bot.py
async def test_ping(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!ping")

    assert channel.last_message.content == "Pong!"
```

Run it:

```bash
pytest
```

!!! note "Async tests just work"
    The `pytest` extra installs `pytest-asyncio`, and SimCord ships with
    `asyncio_mode = "auto"` in mind, so `async def test_...` functions run without an
    `@pytest.mark.asyncio` decorator. If you manage pytest-asyncio yourself, set
    `asyncio_mode = "auto"` in your `pyproject.toml`.

## A second, richer example

A permission-checked slash command, tested from both an allowed and a denied user:

```python
import discord

async def test_ban_requires_permission(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("mod")
    mods = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
    mod = guild.add_member(simcord_env.create_user("mod"), roles=[mods])
    rando = guild.add_member(simcord_env.create_user("rando"))
    target = guild.add_member(simcord_env.create_user("spammer"))

    denied = await rando.slash(channel, "ban", user=target)
    assert denied.response.content == "You can't do that."
    assert guild.get_ban(target) is None

    allowed = await mod.slash(channel, "ban", user=target, reason="spam")
    assert allowed.ephemeral
    assert allowed.response.content == f"Banned {target.mention}: spam"
    assert guild.get_ban(target) is not None
```

## The three kinds of objects

Every SimCord test is built from three object families. This is the whole mental model —
see [Core concepts](concepts.md) for the deep dive.

- **Builders** (synchronous, omnipotent) — `env.create_guild()`,
  `guild.create_text_channel()`, `guild.create_role()`, `guild.add_member()`. Arrange the
  world with no permission checks; the *test* is omnipotent.
- **Actors** (async, permission-checked) — `alice.send(...)`, `alice.slash(...)`,
  `alice.click(...)`, `alice.select(...)`, `alice.react(...)`, `alice.submit_modal(...)`.
  Do only what a real user physically could. Each one **waits for your bot to finish
  reacting** before returning — so there's never an `asyncio.sleep` in your tests.
- **Queries** — `channel.history()`, `channel.last_message`,
  [`env.errors`](guides/diagnostics.md) (errors your bot swallowed),
  [`env.http_log`](guides/diagnostics.md) (every REST call it made). Assert with plain
  Python against real `discord.Message` objects.

## Testing failure paths

Make Discord's REST API misbehave on demand to exercise your error handling:

```python
async def test_handles_api_outage(simcord_env):
    simcord_env.inject_error("POST", "/channels/*/messages", status=500)
    ...   # assert your bot degrades gracefully
```

More in [Errors & diagnostics](guides/diagnostics.md).

## Where to go next

- [Core concepts](concepts.md) — builders, actors and queries in depth.
- [Messages & prefix commands](guides/messages.md) — the first guide.
- [Recipes](cookbook.md) — ready-made patterns for common test scenarios.
