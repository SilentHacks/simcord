---
title: "Threads, reactions & DMs"
description: "Test discord.py threads, reactions and direct messages with SimCord — create threads, message inside them, react and unreact, and exercise DM flows with real error codes."
---

# Threads, reactions & DMs

Beyond channel messages and interactions, bots lean on threads, reactions and DMs. SimCord
supports all three with the same real-machinery, permission-checked model.

## Reactions

A user can add and remove reactions, and the bot sees them through `on_reaction_add` /
`on_raw_reaction_add` and their removal counterparts:

```python
message = await alice.send(channel, "vote!")

await alice.react(message, "👍")     # checks add_reactions; emits the gateway event
await bob.react(message, "👍")
await alice.unreact(message, "👍")

fetched = channel.last_message
counts = {str(r.emoji): r.count for r in fetched.reactions}
assert counts["👍"] == 1
```

Reaction-driven flows — role menus, confirmations, reaction-paginated help — are testable
end to end: react as the user, then assert on what the bot did in response.

```python
async def test_reaction_role(simcord_env):
    ...
    panel = channel.last_message          # bot posted a "react 🎮 for the Gamer role"
    await alice.react(panel, "🎮")
    assert any(r.name == "Gamer" for r in alice.member.roles)
```

## Threads

Threads are created with builders and behave like channels for messaging. The
[parity matrix](../parity-matrix.md) covers standalone threads and threads created from a
message.

```python
# Thread handles surface under the parent channel:
for thread in channel.threads:
    ...

# Sending inside a thread checks send_messages_in_threads (not send_messages):
await alice.send(thread, "inside the thread")
assert thread.last_message.content == "inside the thread"
```

`channel.threads` lists the threads under a parent, and `thread.is_thread` is `True`. The
permission check automatically switches to `send_messages_in_threads` when you `send` into a
thread, matching Discord.

## Direct messages

A user can DM the bot, and the bot can DM back. A user→bot DM fires `on_message` with
`message.guild is None`:

```python
await alice.send_dm("hello bot")             # on_message, message.guild is None
reply = alice.user.dm_channel.history()      # what the bot DM'd back
assert reply[-1].content.startswith("Hi")
```

`alice.user.dm_channel` is the user's DM channel with the bot; `.history()` returns the
conversation as real `discord.Message` objects.

!!! info "DM failures are realistic"
    Opening a DM channel always succeeds, but a bot **sending** a DM to a user it can't
    message fails on send with `403 50007` — catchable with `except discord.Forbidden` —
    exactly like Discord. So "the bot assumed the DM went through" bugs are testable. (DM
    messages also never leak into guild channel history or pins.)

## Next

- [Messages & prefix commands](messages.md) — the core text verbs.
- [Permissions](permissions.md) — thread and channel permission rules.
- [Parity matrix](../parity-matrix.md) — exactly which thread and reaction routes are
  implemented today.
