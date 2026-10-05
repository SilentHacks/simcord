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

## Command visibility

In SimCord 3.0, `MemberActor.slash()`, `.autocomplete()`, `.context_menu()`, and the DM command helpers reject commands a real user could not see. They raise `simcord.SetupError` with `Command '/x' is not visible to this user here — a real user could not run it (reason: <code>)` and a note suggesting a fix. The visibility check follows these rules:

- Guild commands are limited to their guild. Global command `contexts` control guild and bot-DM use (`GUILD=0`, `BOT_DM=1`); when `contexts` is absent, legacy `dm_permission` controls bot DMs.
- NSFW commands require an age-restricted guild channel. Threads inherit the parent's setting; NSFW commands are never visible in DMs because the user's age setting is not modeled.
- In guilds, `Administrator` bypasses permission checks after scope, context, and NSFW checks. Other users need `USE_APPLICATION_COMMANDS`.
- Command-level permission overrides take precedence over application-level overrides. Seed them with `guild.set_command_permissions(command, {target: True})`; `@everyone` is the guild ID, All Channels is `guild.id - 1`, and thread channel overrides inherit from the parent. Explicit command-level user/role allows bypass `default_member_permissions`; application-level allows do not. A role allow beats a role deny. `default_member_permissions="0"` allows admins or explicit command-level overrides only.
- Discord leaves conflicting duplicate channel, user, or `@everyone` overrides for an identical target unspecified; SimCord conservatively hides the command. Role conflicts use allow-over-deny.

The refusal includes one of these reason codes: `scope`, `context`, `nsfw`, `use-application-commands`, `channel-denied`, `override-denied`, or `default-member-permissions`.

If `slash()` says a command is not visible, adjust the fixture or assert that refusal. Grant the required permission, seed an override with `guild.set_command_permissions(...)`, use an NSFW channel, or declare the intended contexts:

```python
@bot.tree.command()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_contexts(guilds=True, dms=True)
async def settings(interaction: discord.Interaction) -> None:
    ...

with pytest.raises(simcord.SetupError, match="reason: default-member-permissions"):
    await alice.slash(channel, "settings")
```

## Validate slash options

`slash()` validates supplied options against the synced command definition and raises `OptionError`, a `SetupError` subclass. It checks required and unknown options, Python value types, choices, numeric `min_value`/`max_value`, string `min_length`/`max_length` (Unicode code points), channel option `channel_types`, and entity-handle kinds. INTEGER values are limited to ±(2⁵³−1); NUMBER values must be finite. Autocomplete values may be free-form even when choices are declared. During autocomplete, invalid already-filled values are dropped rather than raising: a Discord client only submits values that passed its own validation.

`OptionError.code` identifies the failed check: `option-unknown`, `option-required`, `option-type`, `option-choice`, `option-range`, `option-length`, `option-integer-range`, `option-channel-type`, `option-file-type`, `option-entity`, or `command-not-leaf`. See the [API reference](../api.md#errors) for its import path and attributes.

For an ATTACHMENT option, pass `(filename, bytes)`; the callback receives a real, readable `discord.Attachment`:

```python
uploaded = await alice.slash(channel, "upload", file=("photo.png", b"image bytes"))
```

`file_types` accepts the `image`, `video`, and `audio` groups or dot-prefixed extensions. SimCord checks the filename extension only; it does not inspect MIME type or file contents.

## Bot-DM slash commands and visible command lists

`UserHandle.slash(name, **options)` and `UserHandle.autocomplete(name, option, value, **filled)` invoke global commands whose contexts allow `BOT_DM`. Their interactions have `context == 1`:

```python
user = simcord_env.create_user("alice")
await user.slash("help")  # The bot's callback receives interaction.context == 1.
```

Use `member.available_commands(channel)` or `user.available_commands()` to get a tuple of leaf invocation strings that are visible there and accepted by `slash()` (for example, `"config set"`):

```python
assert "ban" not in alice.available_commands(channel)
```

## Stable command IDs

Like Discord's bulk overwrite, `tree.sync()` preserves a command ID when its `(name, type)` is unchanged. Permission overrides keyed by that ID therefore survive a re-sync; renaming the command or changing its type creates a different identity.

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
