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

!!! note "Import style"
    The package can be imported directly (`import simcord`) so calls read
    as `simcord.run(...)`. Use whichever style you prefer — it's the same module.

## Configuration options

Both `simcord.run(bot, **options)` and the underlying `Env` accept these keyword options:

| Option | Default | Effect |
| --- | --- | --- |
| `strict_sync` | `True` | Unsynced app commands can't be invoked — invoking one fails the test, catching forgotten `tree.sync()` calls. Set `False` to auto-register unsynced commands for isolated unit tests. |
| `check_errors` | `True` | At teardown, errors the bot raised but the test never inspected are re-raised as an `ExceptionGroup`, so bot bugs can't pass silently. Set `False` to opt out. |
| `approved_intents` | all | Simulates developer-portal privileged-intent toggles. |
| `shard_count` | client setting | Supplies the Get Gateway Bot recommendation when an `AutoShardedClient` does not configure `shard_count` itself. |

```python
# An isolated unit test that doesn't care about sync, and inspects errors itself:
async with simcord.run(bot, strict_sync=False) as env:
    ...
```

!!! tip "Bot syncs to a hardcoded guild id?"
    Some bots pin `tree.sync(guild=discord.Object(id=...))` to a specific guild id. Pass that
    same id to `create_guild` so the synced commands land in a guild that exists, and
    `strict_sync` can stay on:

    ```python
    MY_GUILD = 123456789012345678  # the id the bot syncs to
    guild = env.create_guild(id=MY_GUILD)
    ```

### Testing sharded bots

Use discord.py's production sharding configuration unchanged:

```python
bot = commands.AutoShardedBot(command_prefix="!", intents=intents, shard_count=4)

async with simcord.run(bot) as env:
    guild = env.create_guild("Shard two", shard_id=2)
    assert (guild.id >> 22) % bot.shard_count == 2
    assert bot.get_shard(2) is not None
```

`create_guild(shard_id=...)` generates a snowflake owned by that shard. An explicit `id`
remains authoritative and raises `ValueError` if it conflicts with `shard_id`. Manual workers
also keep their normal configuration:

```python
bot = commands.AutoShardedBot(
    command_prefix="!",
    intents=intents,
    shard_count=16,
    shard_ids=[4, 5, 6, 7],
)
```

Only owned guild events reach a partial worker. Non-guild events follow Discord routing and
reach shard `0`. If production leaves `bot.shard_count` unset for automatic discovery, make
the recommendation explicit in the test with `simcord.run(bot, shard_count=16)`.

### Overriding options per test

Mark a test with `@pytest.mark.simcord(...)` and its keyword arguments are forwarded to
`simcord.run`, so a single test can change its environment without a custom fixture:

```python
import pytest

@pytest.mark.simcord(strict_sync=False)
async def test_command_logic_in_isolation(simcord_env):
    # this env auto-registers unsynced commands; other tests stay strict
    ...
```

For an override shared by many tests, drive `simcord.run` yourself in a small wrapper fixture
instead:

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
