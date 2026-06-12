---
title: "SimCord — the discord.py testing framework"
description: "SimCord is a discord.py testing framework that simulates Discord so you can test your bot offline: no network, no token, no test server. Test prefix commands, slash commands, buttons, modals and permissions in-process and deterministically."
---

# SimCord

**The discord.py testing framework** — simulate Discord, test your bot offline.

Run your **real, unmodified bot** against a virtual, in-memory Discord — no network, no
tokens, and no Terms of Service concerns, because nothing ever connects to Discord.
Simulate users sending messages, invoking slash commands and clicking buttons, then assert
on exactly what your bot did.

```python
async def test_ping(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!ping")          # full gateway round trip

    assert channel.last_message.content == "Pong!"
```

[Get started in 5 minutes :material-arrow-right:](quickstart.md){ .md-button .md-button--primary }
[Browse the API :material-arrow-right:](api.md){ .md-button }

!!! warning "Alpha"
    The core surface — messages, prefix commands, slash commands, components, modals,
    permissions, reactions, threads, DMs and time control — works today. See the
    [parity matrix](parity-matrix.md) for the long tail. Unimplemented routes always fail
    **loudly**; SimCord never silently fakes success.

## Why SimCord?

Unit tests cover your business logic, but the bugs that bite Discord bots live in the
**glue**: converters, checks, permissions, forgotten `tree.sync()` calls,
double-acknowledged interactions, oversized embeds. Until now the only way to test that
layer was manually, in a real server. SimCord runs all of discord.py's real machinery —
its parsers, cache, command frameworks and views — against a faithful mock of Discord's
REST API and gateway, entirely in-process.

<div class="grid cards" markdown>

-   :dart: __Real discord.py semantics__

    ---

    Server-side permission checks with authentic error codes
    (`50013 Missing Permissions`…), interaction lifecycle rules (`40060` on double-ack),
    role hierarchy, timeouts, ephemeral visibility and validation limits.

-   :bug: __Real bugs caught__

    ---

    Invoking a never-synced slash command fails your test, just like production. Clicking
    a disabled button is impossible, just like the client. Unhandled bot errors fail the
    test by default.

-   :zap: __Fast & deterministic__

    ---

    No sleeps, no network, reproducible IDs and timestamps. The framework tracks the bot's
    tasks and settles after every action — there is never an `asyncio.sleep` in your tests.

-   :fast_forward: __Time control__

    ---

    [`env.advance_time(180)`](guides/time-control.md) fires view timeouts and resets
    cooldowns instantly — no real waiting.

-   :mag: __Debuggable failures__

    ---

    Failing tests automatically include a [transcript](guides/diagnostics.md) of every
    gateway event and REST call — exactly what your bot did, in order.

-   :loudspeaker: __Honest about gaps__

    ---

    Anything not implemented raises `RouteNotImplemented` naming the route. Never a silent
    fake success. See the [parity matrix](parity-matrix.md).

</div>

## How it works

discord.py has two narrow seams — every REST call goes through one `HTTPClient.request`,
and every gateway event enters through one `ConnectionState.parsers` dispatch. SimCord
swaps the transports behind those two seams for an in-memory backend and injects
Discord-shaped payloads. **Everything in between — models, converters, the command tree,
checks, views, the cache — is real discord.py code running unmodified.** Read the full
[architecture](architecture.md).

```text
test ──► builders / actors ──► virtual backend (single source of truth)
                                   │                     │
                  gateway payloads ▼                     ▼ REST responses
                  ConnectionState.parsers        FakeHTTPClient route table
                                   │                     ▲
                                   ▼                     │
                                  your real, unmodified bot
```

## Where to go next

<div class="grid cards" markdown>

- :material-rocket-launch: __[Installation](installation.md)__

    ---

    Requirements, the `pytest` extra, and version compatibility.

- :material-clock-fast: __[Quickstart](quickstart.md)__

    ---

    From `pip install` to your first passing test in five minutes.

- :material-cube-outline: __[Core concepts](concepts.md)__

    ---

    Builders, actors and queries — the whole mental model on one page.

- :material-book-open-variant: __[Guides](guides/messages.md)__

    ---

    Every feature, end to end, with runnable examples.

- :material-silverware-fork-knife: __[Recipes](cookbook.md)__

    ---

    Copy-paste patterns for the tests you actually need to write.

- :material-swap-horizontal: __[Migrating from dpytest](migrating-from-dpytest.md)__

    ---

    A direct concept-by-concept mapping.

</div>
