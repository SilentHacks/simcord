<div align="center">

<img src="docs/assets/simcord-transparent.webp" width="48" height="48" alt="" style="vertical-align: middle;"> <h1 style="display: inline-block; margin: 0; vertical-align: middle;">SimCord</h1>

**The discord.py testing framework — simulate Discord, test your bot offline.**

Test your discord.py bot with a full virtual Discord environment: no network, no token,
no test server, no Terms of Service concerns. SimCord is the missing testing library
for discord.py bots.

[![CI](https://github.com/SilentHacks/simcord/actions/workflows/ci.yml/badge.svg)](https://github.com/SilentHacks/simcord/actions/workflows/ci.yml)
[![Docs](https://app.readthedocs.org/projects/simcord/badge/?version=latest)](https://simcord.readthedocs.io/)
[![PyPI](https://img.shields.io/pypi/v/simcord)](https://pypi.org/project/simcord/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/simcord/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2)](https://github.com/Rapptz/discord.py)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Quickstart](#quickstart) · [Documentation](https://simcord.readthedocs.io/) · [Parity matrix](https://simcord.readthedocs.io/en/latest/parity-matrix/) · [Contributing](CONTRIBUTING.md)

</div>

---

SimCord is a **discord.py testing framework** that gives your bot a fake but faithful
Discord to run against. Simulate users sending messages, invoking slash commands, clicking
buttons and submitting modals — then assert on exactly what your bot did:

```python
async def test_ping(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!ping")          # full gateway round trip

    assert channel.last_message.content == "Pong!"
```

> ✅ **Stable (1.0).** The public API follows semantic versioning — see
> [Stability & versioning](https://simcord.readthedocs.io/en/latest/stability/). The
> [parity matrix](https://simcord.readthedocs.io/en/latest/parity-matrix/) records
> exactly what is implemented; the remaining routes are a deliberate, demand-driven
> backlog that always fails loudly — SimCord never silently fakes success.

## Why SimCord?

Unit tests cover your business logic, but the bugs that bite Discord bots live in the
glue: converters, checks, permissions, forgotten `tree.sync()` calls, double-acknowledged
interactions, oversized embeds. Until now the only way to test that layer was manually,
in a real server. SimCord runs all of discord.py's real machinery — its parsers, cache,
command frameworks and views — against a faithful mock of Discord's REST API and gateway,
entirely in-process.

|  |  |
| --- | --- |
| 🎯 **Real discord.py semantics** | Server-side permission checks with authentic error codes (`50013 Missing Permissions`…), interaction lifecycle rules (`40060` on double-ack), role hierarchy, timeouts, ephemeral visibility, validation limits. |
| 🐛 **Real bugs caught** | Invoking a never-synced slash command fails your test, just like production. Clicking a disabled button is impossible, just like the client. Unhandled bot errors fail the test by default. |
| ⚡ **Fast & deterministic** | No sleeps, no network, reproducible IDs and timestamps. The framework tracks the bot's tasks and settles after every action. |
| ⏩ **Time control** | `env.advance_time(180)` fires view timeouts and resets cooldowns instantly — no real waiting. |
| 🔍 **Debuggable failures** | Failing tests automatically include a transcript of every gateway event and REST call — exactly what your bot did, in order. |
| 📢 **Loud gaps** | Anything not implemented raises `RouteNotImplemented` naming the route. Never silent fake success. |

## Install

```bash
pip install simcord[pytest]
```

Requires Python 3.11+ and discord.py 2.7+. Zero dependencies beyond discord.py itself.

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

Then write tests against the `simcord_env` fixture:

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

Buttons, selects, modals, context menus, autocomplete, reactions, threads, DMs, fault
injection and more: see the [documentation](https://simcord.readthedocs.io/).
Prefer explicit control? `async with simcord.run(bot) as env:` works in any async
test framework.

## How it works

discord.py has two narrow seams: every REST call funnels through `HTTPClient.request`,
and every gateway event enters through `ConnectionState.parsers`. SimCord replaces the
first with a fake routed to an in-memory backend (a single source of truth for guilds,
channels, members, messages, commands and interactions) and injects Discord-shaped payloads
through the second. Everything between those seams — which is everything your bot touches
— is real discord.py code running unmodified.

```
test ──► builders/actors ──► virtual backend (single source of truth)
                                   │                     │
                  gateway payloads ▼                     ▼ REST responses
                  ConnectionState.parsers        FakeHTTPClient route table
                                   │                     ▲
                                   ▼                     │
                                  your real, unmodified bot
```

Details in the [architecture docs](https://simcord.readthedocs.io/en/latest/architecture/).

## discord.py testing — common use cases

SimCord covers the full range of discord.py bot testing scenarios:

- **discord.py unit testing** — test individual commands in isolation
- **discord.py integration testing** — test full command flows with permissions, roles and channels
- **discord.py mock events** — fire any gateway event (member join, reaction add, voice state…) without a real server
- **discord.py mock Discord** — a full in-memory Discord server your bot can't tell from the real thing
- **discord.py command testing** — prefix commands, slash commands, context menus, autocomplete
- **discord.py interaction testing** — buttons, selects, modals, ephemeral responses, deferred replies
- **discord.py bot testing without a token** — no `.env`, no test guild, no rate limits

## Comparison

| | SimCord | dpytest | Manual test server |
|---|---|---|---|
| No network / no token | ✅ | ✅ | ❌ |
| Real discord.py internals | ✅ | Partial | ✅ |
| Slash commands & components | ✅ | ❌ | ✅ |
| Authentic error codes | ✅ | ❌ | ✅ |
| Time control | ✅ | ❌ | ❌ |
| Failure transcripts | ✅ | ❌ | ❌ |
| Maintained for discord.py 2.x | ✅ | ❌ | — |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with a failing test are gold; if your
bot hits an unimplemented route, the error names it — please open a
[parity gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md).

## License

[MIT](LICENSE). Unofficial — not affiliated with Discord Inc. or the discord.py project.
