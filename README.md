<div align="center">

# discord-py-test

**Offline testing framework for [discord.py](https://github.com/Rapptz/discord.py) bots.**

Run your real, unmodified bot against a virtual, in-memory Discord —
no network, no tokens, no clicking through a test server.

[![CI](https://github.com/SilentHacks/discord-py-test/actions/workflows/ci.yml/badge.svg)](https://github.com/SilentHacks/discord-py-test/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-silenthacks.github.io-blue)](https://silenthacks.github.io/discord-py-test/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/discord-py-test/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2)](https://github.com/Rapptz/discord.py)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Quickstart](#quickstart) · [Documentation](https://silenthacks.github.io/discord-py-test/) · [Parity matrix](https://silenthacks.github.io/discord-py-test/parity-matrix/) · [Contributing](CONTRIBUTING.md)

</div>

---

Simulate users sending messages, invoking slash commands, clicking buttons and submitting
modals — then assert on exactly what your bot did:

```python
async def test_ping(dpt_env):
    channel = dpt_env.create_guild().create_text_channel("general")
    alice = dpt_env.guild.add_member(dpt_env.create_user("alice"))

    await alice.send(channel, "!ping")          # full gateway round trip

    assert channel.last_message.content == "Pong!"
```

> ⚠️ **Alpha.** The core surface (messages, prefix commands, slash commands, components,
> modals, permissions, reactions, threads, DMs, time control) works; see the
> [parity matrix](https://silenthacks.github.io/discord-py-test/parity-matrix/) for the
> long tail. Unimplemented routes always fail loudly — this library never silently fakes
> success.

## Why

Unit tests cover your business logic, but the bugs that bite Discord bots live in the
glue: converters, checks, permissions, forgotten `tree.sync()` calls, double-acknowledged
interactions, oversized embeds. Until now the only way to test that layer was manually,
in a real server. `discord-py-test` runs all of discord.py's real machinery — its
parsers, cache, command frameworks and views — against a faithful fake of Discord's REST
API and gateway, entirely in-process.

|  |  |
| --- | --- |
| 🎯 **Real semantics** | Server-side permission checks with authentic error codes (`50013 Missing Permissions`…), interaction lifecycle rules (`40060` on double-ack), role hierarchy, timeouts, ephemeral visibility, validation limits. |
| 🐛 **Real bugs caught** | Invoking a never-synced slash command fails your test, just like production. Clicking a disabled button is impossible, just like the client. Unhandled bot errors fail the test by default. |
| ⚡ **Fast & deterministic** | No sleeps, no network, reproducible IDs and timestamps. The framework tracks the bot's tasks and settles after every action. |
| ⏩ **Time control** | `env.advance_time(180)` fires view timeouts and resets cooldowns instantly — no real waiting. |
| 🔍 **Debuggable failures** | Failing tests automatically include a transcript of every gateway event and REST call — exactly what your bot did, in order. |
| 📢 **Loud gaps** | Anything not implemented raises `RouteNotImplemented` naming the route. Never silent fake success. |

## Install

```bash
pip install discord-py-test[pytest]
```

Requires Python 3.11+ and discord.py 2.7+. Zero dependencies beyond discord.py itself.

## Quickstart

Tell the bundled pytest plugin how to build your bot:

```python
# conftest.py
import pytest
from mybot import create_bot   # however your project builds its commands.Bot

@pytest.fixture
def dpt_bot():
    return create_bot()
```

Then write tests against the `dpt_env` fixture:

```python
import discord

async def test_ban_slash_command(dpt_env):
    guild = dpt_env.create_guild()
    channel = guild.create_text_channel("mod")
    mods = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
    mod = guild.add_member(dpt_env.create_user("mod"), roles=[mods])
    target = guild.add_member(dpt_env.create_user("spammer"))

    result = await mod.slash(channel, "ban", user=target, reason="spam")

    assert result.ephemeral
    assert result.response.content == f"Banned {target.mention}: spam"
    assert guild.get_ban(target) is not None

async def test_offer_expires(dpt_env):
    channel = dpt_env.create_guild().create_text_channel("general")
    alice = dpt_env.guild.add_member(dpt_env.create_user("alice"))

    result = await alice.slash(channel, "offer")    # bot replies with a View(timeout=180)
    await dpt_env.advance_time(180)                 # instant — the view times out

    assert "expired" in channel.last_message.content
```

Buttons, selects, modals, context menus, autocomplete, reactions, threads, DMs, fault
injection and more: see the [documentation](https://silenthacks.github.io/discord-py-test/).
Prefer explicit control? `async with discord_py_test.run(bot) as env:` works in any async
test framework.

## How it works

discord.py has two narrow seams: every REST call funnels through `HTTPClient.request`,
and every gateway event enters through `ConnectionState.parsers`. `discord-py-test`
replaces the first with a fake routed to an in-memory backend (a single source of truth
for guilds, channels, members, messages, commands and interactions) and injects
Discord-shaped payloads through the second. Everything between those seams — which is
everything your bot touches — is real discord.py code running unmodified.

```
test ──► builders/actors ──► virtual backend (single source of truth)
                                   │                     │
                  gateway payloads ▼                     ▼ REST responses
                  ConnectionState.parsers        FakeHTTPClient route table
                                   │                     ▲
                                   ▼                     │
                                  your real, unmodified bot
```

Details in the [architecture docs](https://silenthacks.github.io/discord-py-test/architecture/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with a failing test are gold; if your
bot hits an unimplemented route, the error names it — please open a
[parity gap issue](https://github.com/SilentHacks/discord-py-test/issues/new?template=parity-gap.md).

## License

[MIT](LICENSE). Unofficial — not affiliated with Discord Inc. or the discord.py project.
