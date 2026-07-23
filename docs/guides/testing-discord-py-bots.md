---
title: "How to test a discord.py bot with pytest"
description: "Test a real discord.py bot with pytest against an offline Discord simulator. Exercise commands, checks, converters, permissions, events, cache updates, and error handlers without a token."
---

# How to test a discord.py bot with pytest

The most useful discord.py tests run your real bot through the same command, event, cache, and permission paths it uses in production. SimCord provides the Discord side of that test entirely in memory.

You do not need a bot token, network connection, or Discord test server.

## Install the test dependencies

=== "pip"

    ```bash
    python -m pip install "simcord[pytest]"
    ```

=== "uv"

    ```bash
    uv add --dev "simcord[pytest]"
    ```

The pytest extra installs pytest and pytest-asyncio. SimCord requires Python 3.11 or newer and discord.py 2.7 or newer.

Enable automatic async test discovery in your project's `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## Make the bot constructible

Keep `bot.run(token)` outside the module path imported by tests. Expose a factory that returns a fresh bot:

```python
# mybot.py
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

Your production entry point can call `create_bot().run(token)`. Tests only call `create_bot()`.

## Connect the pytest fixture

```python
# tests/conftest.py
import pytest

from mybot import create_bot


@pytest.fixture
def simcord_bot():
    return create_bot()
```

The bundled plugin supplies `simcord_env`. It logs the bot in through discord.py's real login path, runs `setup_hook`, sends `READY`, and cleans up after the test.

## Exercise behavior as a user

```python
async def test_ping_command(simcord_env):
    guild = simcord_env.create_guild("Test Server")
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!ping")

    assert channel.last_message.content == "Pong!"
```

This test covers more than the command callback. Discord.py parses the message, resolves the prefix, converts arguments, runs checks, invokes the command, sends the HTTP request, parses `MESSAGE_CREATE`, and updates its cache.

## What belongs in a SimCord test

Use SimCord for behavior coupled to Discord:

- Prefix and application-command dispatch
- Converters and checks
- Permissions and role hierarchy
- Interactions, buttons, selects, and modals
- Gateway events and cache updates
- Cooldowns and view timeouts
- Bot error handling
- Shard routing

Keep pure calculations and application services in ordinary unit tests. A project normally benefits from both test styles.

## Fail loudly on missing behavior

SimCord raises `RouteNotImplemented` when a Discord operation is outside its implemented surface. It does not return an empty success value. Check the [parity matrix](../parity-matrix.md) before relying on a route or event.

## Continue

- [Test without a Discord token](test-without-token.md)
- [Test slash commands and interactions](testing-slash-commands.md)
- [Choose simulation or mocks](mocks-vs-simulation.md)
- [AI coding agent workflow](ai-coding-agents.md)
- [Five-minute quickstart](../quickstart.md)
