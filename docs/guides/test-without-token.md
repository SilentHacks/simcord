---
title: "Test a Discord bot without a token or test server"
description: "Run discord.py bot tests offline with no Discord token, gateway connection, test guild, rate limits, or Terms of Service risk."
---

# Test a Discord bot without a token or test server

A discord.py bot can be tested without connecting to Discord. SimCord replaces the HTTP and gateway transport seams inside discord.py with an in-memory Discord implementation.

Your bot still uses real discord.py models, parsers, commands, interactions, checks, views, and cache. Only the network transport is replaced.

## Why no token is needed

A production bot normally follows this path:

```text
bot code -> discord.py -> Discord HTTP and Gateway
```

A SimCord test follows this path:

```text
bot code -> discord.py -> SimCord in-memory backend
```

`simcord.run` performs a fake login and injects Discord-shaped gateway payloads directly into discord.py's parsers. It never opens a Discord socket or sends an HTTP request to Discord.

## Minimal offline test

```python
import discord
from discord.ext import commands

import simcord


bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())


async def test_ready_without_discord():
    async with simcord.run(bot) as env:
        guild = env.create_guild()

        assert bot.is_ready()
        assert bot.get_guild(guild.id) is not None
```

No environment variable, secret, test application, or test guild is required.

## What this avoids

- Leaked bot tokens in local environments or CI
- Flaky network-dependent tests
- Gateway identify limits and HTTP rate limits
- Slow command registration against Discord
- Shared test-guild state
- Cleanup of messages, roles, and channels after a test
- Accidental automation of a user account

SimCord is not a self-bot and does not automate a Discord client. Tests remain local and deterministic.

## CI configuration

A normal GitHub Actions job is enough:

```yaml
- uses: actions/checkout@v7
- uses: astral-sh/setup-uv@v7
- run: uv sync
- run: uv run pytest
```

Do not add `DISCORD_TOKEN` to the job. If a test asks for one, the test is not using the offline path.

## Boundaries

An offline simulator cannot prove that Discord accepted a deployed command registration or that credentials are valid. Keep deployment checks separate and small. Use SimCord for the broad behavioral suite that should run on every change.

The [parity matrix](../parity-matrix.md) lists exactly which Discord operations are implemented and which gaps fail loudly.

## Continue

- [How to test a discord.py bot with pytest](testing-discord-py-bots.md)
- [Fixtures and configuration](fixtures.md)
- [Architecture](../architecture.md)
- [Errors and diagnostics](diagnostics.md)
