---
title: "Messages & prefix commands"
description: "Test discord.py prefix commands with SimCord: send messages as a user, exercise converters, checks and cooldowns, handle attachments and replies, and assert on what your bot sent."
---

# Messages & prefix commands

Everything below drives a real `commands.Bot` end to end: the simulated user's message
travels through the gateway into discord.py's real parser, cache, prefix handling,
converters and checks; the bot's reply travels back through the (fake) REST API and is
broadcast to the cache like on production Discord. Nothing is stubbed in between.

## Sending as a user

The core verb is `actor.send(channel, content)`. It returns the real `discord.Message` that
was created, already visible in the cache:

```python
message = await alice.send(channel, "!ping")
```

`send` supports the things a real client can attach to a message:

```python
# An attachment — (filename, bytes). attachment.read() works against the in-memory CDN.
await alice.send(channel, "look at this", attachments=[("notes.txt", b"hello")])

# A reply (message reference).
await alice.send(channel, "replying!", reply_to=message)
```

And the other text verbs mirror what a user can do to their own messages:

```python
await alice.edit(message, "edited content")   # only your own messages (else 50005)
await alice.delete(message)                    # your own, or with manage_messages
await alice.typing(channel)                    # triggers on_typing
```

!!! note "Permissions are enforced"
    `send` checks `send_messages` (or `send_messages_in_threads` inside a thread), `react`
    checks `add_reactions`, deleting someone else's message checks `manage_messages`, and so
    on. If your test setup denies the action, you get a `SetupError` — see
    [Permissions](permissions.md).

## Converters and checks run for real

Because the cache is populated through discord.py's actual parsers, converters resolve
against genuine state — no special setup:

```python
# The discord.Member converter resolves the mention against the real cache.
await alice.send(channel, f"!whois {alice.mention}")
```

The same is true for `@commands.has_permissions(...)`, `@commands.cooldown(...)`, custom
checks and error handlers — they are discord.py's own machinery running over real data.
Cooldowns are time-based, and you can fast-forward them with
[`env.advance_time`](time-control.md).

## Reactions

```python
await alice.react(message, "👋")     # checks add_reactions; emits the gateway event
await alice.unreact(message, "👋")

# The bot sees its own and others' reactions through on_reaction_add etc.
fetched = channel.last_message
assert any(str(r.emoji) == "👋" for r in fetched.reactions)
```

More on reaction-driven flows in [Threads, reactions & DMs](threads-reactions-dms.md).

## Direct messages

A user can DM the bot, and the bot can DM back:

```python
await alice.send_dm("hello bot")            # on_message fires with message.guild is None
history = alice.user.dm_channel.history()   # what the bot DM'd back
```

See [Threads, reactions & DMs](threads-reactions-dms.md) for the DM nuances (e.g. a bot
DMing a user who shares no mutual context fails with `403 50007`, matching Discord).

## Asserting what the bot did

Queries return real `discord.Message` objects, so assert with normal attribute access:

```python
assert channel.last_message.content == "Pong!"
assert channel.history()[-1].embeds[0].title == "Help"
assert channel.pinned_messages() != []
assert channel.last_message.reference.message_id == original.id
```

`channel.history()` returns every message oldest-first; `channel.last_message` is the most
recent or `None`. Pass `viewer=` to get an ephemeral-aware view — see
[Slash commands → Ephemeral visibility](interactions.md#ephemeral-visibility).

## Error capture

Unhandled errors from command handlers, app-command callbacks and event listeners are
collected into `env.errors`, so the classic "the bot silently failed" bug becomes
assertable:

```python
await alice.send(channel, "!broken")
assert isinstance(simcord_env.errors[-1].original, discord.Forbidden)
assert simcord_env.errors[-1].original.code == 50013
```

!!! warning "Errors fail tests by default"
    If the bot raised errors during a test and the test never inspected `env.errors` (or
    called `env.raise_errors()`), `simcord.run` re-raises them as an `ExceptionGroup` at
    teardown — a bot bug can't pass silently. Reading `env.errors` counts as inspecting
    (your assertions take over). Assert cleanliness explicitly with `env.raise_errors()`,
    or opt out entirely with `simcord.run(bot, check_errors=False)`.

Full treatment in [Errors & diagnostics](diagnostics.md).

## Validation limits

Real Discord limits are enforced with real error codes. Message content over 2000
characters, or embeds totalling over 6000 characters, raise `discord.HTTPException` with
code `50035` — exactly as in production:

```python
async def test_rejects_oversized(simcord_env):
    ...
    with pytest.raises(discord.HTTPException) as exc:
        await some_path_that_sends_too_much()
    assert exc.value.code == 50035
```

## Next

- [Slash commands](interactions.md) — app commands, context menus and the interaction
  lifecycle.
- [Components & modals](components.md) — buttons, selects and modals.
- [Recipes](../cookbook.md) — ready-made patterns built on these verbs.
