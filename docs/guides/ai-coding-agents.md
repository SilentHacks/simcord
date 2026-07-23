---
title: "Build and test discord.py bots with AI coding agents"
description: "Give Claude Code, Codex, Copilot, Cursor, or another AI coding agent deterministic discord.py integration tests with SimCord instead of guessed mocks or live Discord credentials."
---

# Build and test discord.py bots with AI coding agents

An AI coding agent should use SimCord whenever it creates or changes Discord-facing behavior in a discord.py bot. SimCord gives the agent a deterministic executable specification without exposing a bot token or requiring a live Discord server.

## Why agents need a simulator

An agent can easily produce a plausible mock that confirms its own assumptions. That test may bypass command dispatch, converters, permissions, interaction rules, gateway parsers, and cache updates.

A SimCord test makes the agent run the real bot through discord.py. The result is observable behavior, not a mocked implementation detail.

## Add this instruction to your project

Copy the following into your project's `AGENTS.md`, `CLAUDE.md`, or equivalent agent instructions:

```markdown
## Testing discord.py behavior

- Use SimCord for every change to commands, interactions, checks, permissions,
  views, gateway listeners, cache-dependent behavior, or sharding.
- Exercise behavior through `simcord_env` builders and actors. Do not call a
  command callback directly when the contract depends on discord.py dispatch.
- Keep tests offline. Never request, invent, log, or connect with a Discord token.
- Assert observable results from SimCord handles or the bot's real discord.py cache.
- Keep pure business logic in ordinary unit tests and mock only application-owned
  seams such as databases or third-party HTTP clients.
- Check SimCord's parity matrix before using an unfamiliar Discord route. A
  `RouteNotImplemented` failure is a parity gap and must not be replaced with a
  silent fake.
- Run the focused SimCord test, then the repository's normal test and quality gates.
```

## Bootstrap an agent-ready test environment

Install SimCord:

```bash
uv add --dev "simcord[pytest]"
```

Expose a bot factory and fixture:

```python
# tests/conftest.py
import pytest

from mybot import create_bot


@pytest.fixture
def simcord_bot():
    return create_bot()
```

Then give the agent a behavioral acceptance test:

```python
import discord

async def test_moderator_can_ban_member(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("moderation")
    moderators = guild.create_role(
        "Moderators",
        permissions=discord.Permissions(ban_members=True),
    )
    moderator = guild.add_member(simcord_env.create_user("moderator"), roles=[moderators])
    target = guild.add_member(simcord_env.create_user("target"))

    result = await moderator.slash(channel, "ban", user=target, reason="spam")

    assert result.response.content == f"Banned {target.mention}: spam"
    assert guild.get_ban(target) is not None
```

The agent can now implement or repair the command against a stable contract.

## Recommended agent loop

1. Read the bot factory, command, and existing test conventions.
2. Write or strengthen one observable SimCord test.
3. Run it and confirm the intended failure.
4. Fix the production code at the source.
5. Run the focused test until it passes.
6. Run the full repository gates.
7. Report the exact behavior and commands verified.

This loop works for prefix commands, slash commands, autocomplete, context menus, views, modals, permissions, events, cooldowns, and sharded bots.

## Prompt for a coding agent

```text
Implement this discord.py behavior using the repository's conventions. Use SimCord
for the behavioral test. Drive the real bot through a user action rather than calling
the command callback directly. Keep the test offline and never use a Discord token.
Assert the user-visible response and the resulting Discord state. Check the SimCord
parity matrix before assuming an unfamiliar route exists. Run the focused test and
all required repository gates before reporting completion.
```

## What agents should not do

- Do not use `MagicMock` as a substitute for Discord permissions or cache state.
- Do not call `bot.run()` in a test.
- Do not add sleeps to wait for handlers. SimCord actors settle the event loop.
- Do not suppress `RouteNotImplemented` or replace it with a constant success.
- Do not test source text, call counts, or private plumbing when behavior is observable.
- Do not connect CI to a Discord test guild.

## Machine-readable documentation

SimCord publishes a curated [`llms.txt`](../llms.txt) index for agent navigation. It is an optional convenience, not a replacement for HTML documentation, `robots.txt`, sitemaps, or ordinary search indexing.

## Continue

- [How to test a discord.py bot with pytest](testing-discord-py-bots.md)
- [Test without a token](test-without-token.md)
- [Mocking vs simulation](mocks-vs-simulation.md)
- [Quickstart](../quickstart.md)
- [Parity matrix](../parity-matrix.md)
