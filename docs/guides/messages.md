# Messages & prefix commands

Everything below drives a real `commands.Bot` end to end: the simulated user's message
travels through the gateway into discord.py's real parser, cache, prefix handling,
converters and checks; the bot's reply travels back through the (fake) REST API and is
broadcast to the cache like on production Discord.

## Sending as a user

```python
message = await alice.send(channel, "!ping")
await alice.send(channel, "look at this", attachments=[("notes.txt", b"hello")])
await alice.send(channel, "replying!", reply_to=message)
await alice.edit(message, "edited content")
await alice.delete(message)
await alice.typing(channel)
await alice.react(message, "👋")
```

Converters work, because the cache is real:

```python
await alice.send(channel, f"!whois {alice.mention}")   # discord.Member converter resolves
```

## Direct messages

```python
await alice.send_dm("hello bot")                  # triggers on_message with message.guild is None
history = alice.user.dm_channel.history()         # what the bot DM'd back
```

## Asserting what the bot did

```python
assert channel.last_message.content == "Pong!"            # real discord.Message
assert channel.history()[-1].embeds[0].title == "Help"
assert channel.pinned_messages() != []
```

## Error capture

Unhandled errors from command handlers, app-command callbacks and event listeners are
collected into `env.errors`, so the classic "the bot silently failed" bug is assertable:

```python
await alice.send(channel, "!broken")
assert isinstance(env.errors[-1].original, discord.Forbidden)
assert env.errors[-1].original.code == 50013
```

To assert the opposite — that the bot ran cleanly — call `env.raise_errors()`. It
re-raises everything captured as an `ExceptionGroup` (even a single error), and does
nothing if there were none:

```python
await alice.send(channel, "!ping")
env.raise_errors()  # fails the test if the bot raised anything
```

## Validation limits

Real Discord limits are enforced with real error codes: message content over 2000
characters or embeds totalling over 6000 characters raise `discord.HTTPException` with
code `50035`, exactly as in production.
