---
title: "How to test discord.py slash commands and interactions"
description: "Test discord.py application commands, autocomplete, context menus, deferred responses, followups, buttons, selects, and modals with pytest and no Discord connection."
---

# How to test discord.py slash commands and interactions

SimCord invokes application commands through Discord-shaped `INTERACTION_CREATE` events. Discord.py performs command lookup, option conversion, checks, response handling, and error dispatch exactly as it does for a live bot.

## Define and sync the command

```python
import discord
from discord import app_commands
from discord.ext import commands


class TestBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.tree.sync()


def create_bot() -> commands.Bot:
    bot = TestBot(command_prefix="!", intents=discord.Intents.default())

    @bot.tree.command()
    @app_commands.describe(name="Person to greet")
    async def greet(interaction: discord.Interaction, name: str) -> None:
        await interaction.response.send_message(f"Hello, {name}!", ephemeral=True)

    return bot
```

Production bots commonly call `await bot.tree.sync()` in `setup_hook`. SimCord runs that hook against its fake HTTP implementation, so the test sees the same command tree.

## Invoke it as a guild member

```python
async def test_greet_slash_command(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    result = await alice.slash(channel, "greet", name="Ada")

    assert result.acknowledged
    assert result.ephemeral
    assert result.response.content == "Hello, Ada!"
```

`InteractionResult` exposes the initial response, followups, modal, acknowledgement state, and deferred state.

## Test deferred responses and followups

```python
result = await alice.slash(channel, "report")

assert result.deferred
assert result.followups[-1].content == "Report complete"
```

SimCord enforces the interaction lifecycle. Responding twice to the initial interaction raises Discord's `40060` error instead of silently succeeding.

## Test components and modals

```python
opened = await alice.slash(channel, "feedback")
submitted = await alice.submit_modal(
    opened,
    values={"feedback:text": "The search is slow"},
)

assert submitted.response.ephemeral
assert submitted.response.content == "Thanks for the feedback"
```

Buttons and selects use the same actor model:

```python
clicked = await alice.click(message, custom_id="confirm:delete")
selected = await alice.select(message, ["moderator"], custom_id="roles:choose")
```

Disabled components, unauthorized users, duplicate acknowledgements, and expired views fail according to the modeled Discord behavior.

## Strict command synchronization

SimCord is strict by default. Invoking a command that was never synchronized raises `SetupError`, which catches a common production failure.

For an isolated test that intentionally skips sync:

```python
async with simcord.run(bot, strict_sync=False) as env:
    ...
```

Prefer the strict default in integration tests.

## Continue

- [Interactions guide](interactions.md)
- [Components and modals](components.md)
- [Testing discord.py with pytest](testing-discord-py-bots.md)
- [Recipes](../cookbook.md)
