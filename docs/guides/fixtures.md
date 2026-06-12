---
title: "Fixtures & configuration"
description: "Wire SimCord into pytest with the simcord_bot and simcord_env fixtures, or drive it manually with simcord.run(). Configure strict_sync and check_errors for your test style."
---

# Fixtures & configuration

SimCord works as a pytest plugin out of the box, and as a plain async context manager when
you want explicit control. This guide covers both, plus the configuration options.

## The pytest plugin

Installing the [`pytest` extra](../installation.md) registers the plugin automatically — no
`conftest` activation needed. It provides two fixtures.

### `simcord_bot` — you define this

You tell SimCord how to build your bot by defining a `simcord_bot` fixture that returns a
fresh, unstarted bot:

```python
# conftest.py
import pytest
from mybot import create_bot

@pytest.fixture
def simcord_bot():
    return create_bot()
```

A fresh bot per test keeps tests isolated. If you forget to define it, SimCord raises a
clear `UsageError` telling you to.

### `simcord_env` — you use this

The `simcord_env` fixture builds your `simcord_bot`, attaches it to a fresh virtual Discord,
runs the **real login flow** (including your `setup_hook`), drives it to `READY`, and hands
you the [`Env`](../api.md#simcord.Env):

```python
async def test_ping(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))
    await alice.send(channel, "!ping")
    assert channel.last_message.content == "Pong!"
```

At teardown it shuts the environment down cleanly and — unless you inspected them — re-raises
any [errors the bot swallowed](diagnostics.md).

!!! tip "Failing tests get a transcript for free"
    When a test fails, the plugin automatically attaches a
    [transcript](diagnostics.md#the-transcript) of every gateway event injected and REST
    call the bot made, in order, to the pytest report — so you can see exactly what the bot
    did without adding any logging.

### Async test setup

The `pytest` extra installs `pytest-asyncio`. Set `asyncio_mode = "auto"` so `async def`
tests run without a per-test decorator:

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## Without pytest

Don't use pytest, or want a second environment in one test? Use `simcord.run` directly — an
async context manager that yields the same `Env`:

```python
import simcord

async def main():
    bot = create_bot()
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))

        await alice.send(channel, "!ping")
        assert channel.last_message.content == "Pong!"
```

!!! note "The `dpt` alias"
    The package is conventionally imported as `dpt` (`import simcord as dpt`) so calls read
    as `dpt.run(...)`. Use whichever you prefer — they're the same module.

## Configuration options

Both `simcord.run(bot, **options)` and the underlying `Env` accept these keyword options:

| Option | Default | Effect |
| --- | --- | --- |
| `strict_sync` | `True` | Unsynced app commands can't be invoked — invoking one fails the test, catching forgotten `tree.sync()` calls. Set `False` to auto-register unsynced commands for isolated unit tests. |
| `check_errors` | `True` | At teardown, errors the bot raised but the test never inspected are re-raised as an `ExceptionGroup`, so bot bugs can't pass silently. Set `False` to opt out. |

```python
# An isolated unit test that doesn't care about sync, and inspects errors itself:
async with simcord.run(bot, strict_sync=False) as env:
    ...
```

### Overriding options with the fixture

To pass options through the `simcord_env` fixture, drive `simcord.run` yourself in a small
wrapper fixture for the tests that need it:

```python
import pytest_asyncio
import simcord

@pytest_asyncio.fixture
async def lenient_env(simcord_bot):
    async with simcord.run(simcord_bot, strict_sync=False) as env:
        yield env
```

## Next

- [Errors & diagnostics](diagnostics.md) — `env.errors`, `raise_errors()`, the transcript
  and the HTTP log.
- [Core concepts](../concepts.md) — what the `Env` gives you.
- [API reference](../api.md) — the full `Env` and `run` signatures.
