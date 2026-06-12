---
title: "Recipes"
description: "Copy-paste SimCord recipes for testing discord.py bots: prefix commands, slash commands with permissions, button confirm flows, modals, autocomplete, cooldowns, view timeouts, error paths and event listeners."
---

# Recipes

Ready-made patterns for the tests you'll actually write. Each assumes the
[`simcord_env` fixture](guides/fixtures.md) and a guild/channel/member set up at the top —
adapt the command and assertion names to your bot.

## A prefix command replies

```python
async def test_ping(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!ping")

    assert channel.last_message.content == "Pong!"
```

## A slash command, allowed and denied

```python
import discord

async def test_ban_permission_gate(simcord_env):
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
    assert guild.get_ban(target) is not None
```

## A confirm/cancel button flow

```python
async def test_confirm_flow(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    started = await alice.slash(channel, "delete-data")
    prompt = started.response.message

    confirmed = await alice.click(prompt, label="Confirm")
    assert confirmed.response.content == "Deleted."
```

## Only the invoker can click an ephemeral component

```python
import pytest
import simcord

async def test_other_user_cannot_click(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))
    bob = guild.add_member(simcord_env.create_user("bob"))

    started = await alice.slash(channel, "private-menu")   # responds ephemerally
    prompt = started.response.message

    with pytest.raises(simcord.SetupError):
        await bob.click(prompt, label="Confirm")           # not bob's message
```

## A modal collects input

```python
async def test_feedback_modal(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    shown = await alice.slash(channel, "feedback")
    submitted = await alice.submit_modal(shown, {"name": "Alice", "comment": "Nice!"})

    assert submitted.response.content == "Thanks Alice"
```

## Autocomplete offers the right choices

```python
async def test_tag_autocomplete(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    choices = await alice.autocomplete(channel, "tag", "name", "py")

    assert [c["value"] for c in choices] == ["python", "pytest"]
```

## A cooldown blocks, then resets

```python
async def test_daily_cooldown(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!daily")
    await alice.send(channel, "!daily")
    assert "wait" in channel.last_message.content.lower()

    await simcord_env.advance_time(60 * 60 * 24)   # a day, instantly
    await alice.send(channel, "!daily")
    assert "claimed" in channel.last_message.content.lower()
```

## A view times out

```python
async def test_offer_expires(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    await alice.slash(channel, "offer")            # posts a View(timeout=60)
    await simcord_env.advance_time(60)
    assert "expired" in channel.last_message.content
```

## The bot survives an API outage

```python
async def test_handles_send_failure(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    simcord_env.inject_error("POST", "/channels/*/messages", status=500, times=1)
    await alice.send(channel, "!report")

    # The bot caught the failure and didn't crash; assert your fallback behaviour.
    assert simcord_env.errors == [] or "retry" in channel.last_message.content.lower()
```

## A welcome listener fires on join

```python
async def test_welcome_message(simcord_env):
    guild = simcord_env.create_guild()
    welcome = guild.create_text_channel("welcome")

    newbie = guild.add_member(simcord_env.create_user("newbie"))   # fires on_member_join

    assert newbie.mention in welcome.last_message.content
```

## Assert the bot sent exactly one message

```python
async def test_no_double_post(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!announce")

    posts = [c for c in simcord_env.http_log if c[0] == "POST" and "/messages" in c[1]]
    assert len(posts) == 1
```

## Reaction roles

```python
async def test_reaction_role(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("roles")
    alice = guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!rolepanel")        # bot posts the panel
    panel = channel.last_message

    await alice.react(panel, "🎮")
    assert any(r.name == "Gamer" for r in alice.member.roles)
```

## See also

- [Messages](guides/messages.md) · [Slash commands](guides/interactions.md) ·
  [Components & modals](guides/components.md)
- [Permissions](guides/permissions.md) · [Time control](guides/time-control.md) ·
  [Errors & diagnostics](guides/diagnostics.md)
