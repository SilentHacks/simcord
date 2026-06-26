<div align="center">

<img src="docs/assets/simcord-transparent.webp" width="48" height="48" alt="" style="vertical-align: middle;"> <h1 style="display: inline-block; margin: 0; vertical-align: middle;">SimCord</h1>

**Test your discord.py bot against a simulated Discord — no network, no token, no test server.**

[![CI](https://github.com/SilentHacks/simcord/actions/workflows/ci.yml/badge.svg)](https://github.com/SilentHacks/simcord/actions/workflows/ci.yml)
[![Docs](https://app.readthedocs.org/projects/simcord/badge/?version=latest)](https://simcord.readthedocs.io/)
[![PyPI](https://img.shields.io/pypi/v/simcord)](https://pypi.org/project/simcord/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/simcord/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2)](https://github.com/Rapptz/discord.py)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Quickstart](#quickstart) · [Mental model](#the-mental-model) · [Documentation](https://simcord.readthedocs.io/) · [Parity matrix](https://simcord.readthedocs.io/en/latest/parity-matrix/) · [Contributing](CONTRIBUTING.md)

</div>

---

SimCord gives your bot a fake but faithful Discord to run against. Simulate users sending
messages, invoking slash commands, clicking buttons and submitting modals — then assert on
exactly what your bot did. It all runs in-process, with no network and no token.

```python
async def test_ping(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!ping")          # full gateway round trip

    assert channel.last_message.content == "Pong!"
```

## Why SimCord?

Your unit tests cover your business logic. The bugs that actually break Discord bots live in
the glue: converters, checks, permissions, a forgotten `tree.sync()`, a double-acknowledged
interaction, an oversized embed. That layer has historically only been testable by hand, in a
real server.

SimCord runs discord.py's real machinery — its parsers, cache, command frameworks and views —
against an in-memory model of Discord's REST API and gateway. Your bot code runs unmodified and
can't tell the difference.

|  |  |
| --- | --- |
| 🎯 **Authentic semantics** | Server-side permission checks with real error codes (`50013 Missing Permissions`), interaction lifecycle rules (`40060` on double-ack), role hierarchy, timeouts, ephemeral visibility and validation limits. |
| 🐛 **Catches real bugs** | Invoking a never-synced slash command fails your test, just like production. Clicking a disabled button is impossible, just like the client. An unhandled error in your bot fails the test by default. |
| ⚡ **Fast & deterministic** | No sleeps, no network, reproducible IDs and timestamps. SimCord tracks the bot's tasks and settles the event loop after every action, so assertions never flake. |
| ⏩ **Time control** | `env.advance_time(180)` fires view timeouts and resets cooldowns instantly — no real waiting. |
| 🔍 **Readable failures** | A failing test prints a transcript of every gateway event and REST call, in order: exactly what your bot did. |
| 📢 **No silent fakes** | Anything unimplemented raises `RouteNotImplemented` naming the route. Gaps fail loudly rather than returning a wrong answer. |

## Install

```bash
pip install simcord[pytest]
```

Requires **Python 3.11+** and **discord.py 2.7+**. No dependencies beyond discord.py itself.

## Quickstart

Tell the bundled pytest plugin how to build your bot:

```python
# conftest.py
import pytest
from mybot import create_bot   # however your project builds its commands.Bot

@pytest.fixture
def simcord_bot():
    return create_bot()
```

Then write tests against the `simcord_env` fixture — it hands you a running environment with
the bot already logged in and at `READY`:

```python
import discord

async def test_ban_slash_command(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("mod")
    mods = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
    mod = guild.add_member(simcord_env.create_user("mod"), roles=[mods])
    target = guild.add_member(simcord_env.create_user("spammer"))

    result = await mod.slash(channel, "ban", user=target, reason="spam")

    assert result.ephemeral
    assert result.response.content == f"Banned {target.mention}: spam"
    assert guild.get_ban(target) is not None

async def test_offer_expires(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    result = await alice.slash(channel, "offer")    # bot replies with a View(timeout=180)
    await simcord_env.advance_time(180)             # instant — the view times out

    assert "expired" in channel.last_message.content
```

Not using pytest? `async with simcord.run(bot) as env:` gives you the same `env` in any async
test framework.

## The mental model

Every SimCord test is three moves: **arrange** the world, **act** as a user, **assert** the
result. Three kinds of object map to those moves.

| | Role | Nature |
| --- | --- | --- |
| **Builders** | Arrange the scenario — guilds, channels, roles, members. | Synchronous and omnipotent: the *test* is the narrator, so no permission checks. |
| **Actors** | Act as a real human — send, click, run a command. | Async and permission-checked: an actor can only do what that user physically could in the client. |
| **Queries** | Assert what happened. | Return **real discord.py objects** from the bot's own cache, so you assert with plain `assert` — no DSL. |

```python
import discord

async def test_welcome_on_join(simcord_env):
    guild   = simcord_env.create_guild()                       # builder
    welcome = guild.create_text_channel("welcome")             # builder
    newbie  = guild.add_member(simcord_env.create_user("ann")) # builder — fires the join event

    assert f"Welcome {newbie.mention}" in welcome.last_message.content   # query
```

Two details that make tests robust:

- **Actors wait for the bot to finish reacting.** Each verb settles the loop — running
  callbacks, draining `asyncio.sleep` chains — before returning, so the reply is already there
  when the next line runs. No sleeps, no flakes. If a handler hangs, settling fails fast with
  the pending tasks listed.
- **Impossible setups raise `SetupError`, not a bot failure.** Speaking in a channel a user
  can't see, or clicking a disabled button, points at your *test*, distinct from a bug in the
  bot.

See [Core concepts](https://simcord.readthedocs.io/en/latest/concepts/) for the full picture.

## What you can test

| Area | Actor verbs | Covers |
| --- | --- | --- |
| **Messages & prefix commands** | `send` · `edit` · `delete` · `typing` | Content, embeds, attachments, mentions, the `commands.Bot` prefix framework. |
| **Slash commands** | `slash` · `autocomplete` | App command tree, `tree.sync()`, options, converters, checks, autocomplete. |
| **Context menus** | `context_menu` | User and message commands. |
| **Components & modals** | `click` · `select` · `submit_modal` | Buttons, selects, modals, `View` timeouts, persistent views across restarts. |
| **Reactions** | `react` · `unreact` | Reaction add/remove events and `wait_for`. |
| **Polls** | `vote` · `remove_vote` | Poll answers and results. |
| **Voice & events** | `join_voice` · `leave_voice` · `set_voice` · `subscribe_event` | Voice state, scheduled-event subscriptions. |
| **DMs** | `send_dm` | Direct-message channels and flows. |

Responses come back as a rich [`InteractionResult`](https://simcord.readthedocs.io/en/latest/api/)
exposing `acknowledged`, `deferred`, `ephemeral`, `response`, `followups` and `modal`. Threads,
permissions, role hierarchy, intents and audit logs are modelled too — the
[parity matrix](https://simcord.readthedocs.io/en/latest/parity-matrix/) records exactly what's
implemented.

## Configuration & diagnostics

Pass options to `simcord.run(bot, ...)`, or per-test via the `@pytest.mark.simcord(...)` marker
on the `simcord_env` fixture:

| Option | Default | Effect |
| --- | --- | --- |
| `strict_sync` | `True` | Invoking an unsynced slash command fails the test, as in production. |
| `check_errors` | `True` | Errors your bot swallowed are re-raised at test teardown unless inspected, so bugs can't pass silently. |
| `approved_intents` | all | Simulate the developer-portal privileged-intent toggles; a missing intent raises `PrivilegedIntentsRequired` on connect. |

```python
@pytest.mark.simcord(strict_sync=False)
async def test_unsynced_command(simcord_env):
    ...
```

When something goes wrong, the `env` tells you what happened:

- `env.transcript()` — the ordered log of gateway events and REST calls (auto-attached to
  failing pytest tests).
- `env.http_log` — every REST request the bot made, to assert on or inspect.
- `env.errors` — exceptions the bot swallowed.
- `env.inject_error("POST", "/channels/*/messages", status=500)` — make matching REST calls
  fail, to test your bot's error handling.
- `env.restart_bot()` — restart the bot while the virtual world persists, to prove persistent
  views re-attach.

## How it works

discord.py has two narrow seams: every REST call funnels through `HTTPClient.request`, and every
gateway event enters through `ConnectionState.parsers`. SimCord replaces the first with a fake
routed to an in-memory backend — a single source of truth for guilds, channels, members,
messages, commands and interactions — and injects Discord-shaped payloads through the second.
Everything between those seams, which is everything your bot touches, is real discord.py running
unmodified.

```
test ──► builders/actors ──► virtual backend (single source of truth)
                                   │                     │
                  gateway payloads ▼                     ▼ REST responses
                  ConnectionState.parsers        FakeHTTPClient route table
                                   │                     ▲
                                   ▼                     │
                                  your real, unmodified bot
```

More in the [architecture docs](https://simcord.readthedocs.io/en/latest/architecture/).

## SimCord vs. the alternatives

| | SimCord | dpytest | Manual test server |
|---|---|---|---|
| No network / no token | ✅ | ✅ | ❌ |
| Real discord.py internals | ✅ | Partial | ✅ |
| Slash commands & components | ✅ | ❌ | ✅ |
| Authentic error codes | ✅ | ❌ | ✅ |
| Time control | ✅ | ❌ | ❌ |
| Failure transcripts | ✅ | ❌ | ❌ |
| Maintained for discord.py 2.x | ✅ | ❌ | — |

Coming from dpytest? See the [migration guide](https://simcord.readthedocs.io/en/latest/migrating-from-dpytest/).

## Documentation

| | |
| --- | --- |
| 🚀 [Quickstart](https://simcord.readthedocs.io/en/latest/quickstart/) | Get a first test running. |
| 🧠 [Core concepts](https://simcord.readthedocs.io/en/latest/concepts/) | Builders, actors, queries — the model. |
| 📖 [Guides](https://simcord.readthedocs.io/) | Messages, interactions, components, permissions, threads, time control, diagnostics. |
| 🍳 [Recipes](https://simcord.readthedocs.io/en/latest/cookbook/) | Copy-paste patterns for common cases. |
| 📋 [Parity matrix](https://simcord.readthedocs.io/en/latest/parity-matrix/) | Exactly what's implemented. |
| 🔖 [API reference](https://simcord.readthedocs.io/en/latest/api/) | Every public object and verb. |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with a failing test are gold. If your bot
hits an unimplemented route, the error names it — please open a
[parity gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md).

## License

[MIT](LICENSE). Unofficial — not affiliated with Discord Inc. or the discord.py project.
