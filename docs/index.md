# SimCord

**The discord.py testing framework** — simulate Discord, test your bot offline.

Run your **real, unmodified bot** against a virtual, in-memory Discord — no network, no
tokens, and no Terms of Service concerns, because nothing ever connects to Discord.
Simulate users sending messages, invoking slash commands and clicking buttons, then assert
on exactly what your bot did.

```python
async def test_ping(env):
    channel = env.guild.create_text_channel("general")
    alice = env.guild.add_member(env.create_user("alice"))

    await alice.send(channel, "!ping")

    assert channel.last_message.content == "Pong!"
```

## Why this exists

Unit tests cover business logic, but Discord bot bugs live in the glue: converters,
checks, permissions, forgotten `tree.sync()` calls, double-acknowledged interactions,
oversized embeds. Until now the only way to test that layer was manually, in a real
server. SimCord runs all of discord.py's real machinery — parsers, cache,
command frameworks, views — against a faithful mock of Discord's REST API and gateway,
entirely in-process.

- **Real semantics**: permission checks with authentic error codes
  (`50013 Missing Permissions`…), interaction lifecycle rules (`40060` on double-ack).
- **Real bugs caught**: invoking a slash command that was never synced fails your test,
  just like it fails in production.
- **Fast and deterministic**: no sleeps, no network, reproducible IDs. Fast-forward
  view timeouts and cooldowns with `env.advance_time()`.
- **Debuggable failures**: failing tests automatically include a transcript of every
  gateway event and REST call, and unhandled bot errors fail tests by default.
- **Honest about gaps**: anything not implemented fails loudly with the route name —
  SimCord never silently fakes success. See the [parity matrix](parity-matrix.md).

Start with the [quickstart](quickstart.md).
