# discord-py-test

Offline testing framework for [discord.py](https://github.com/Rapptz/discord.py) bots.

Run your **real, unmodified bot** against a virtual, in-memory Discord — no network, no
tokens, no manual clicking through Discord, and no Terms of Service concerns (nothing ever
connects to Discord). Simulate users sending messages, invoking slash commands and clicking
buttons, then assert on exactly what your bot did.

> ⚠️ Alpha software. The core surface (messages, prefix commands, slash commands,
> components, modals, permissions, reactions, threads, DMs) works; see the
> [parity matrix](https://silenthacks.github.io/discord-py-test/parity-matrix/) for the
> long tail. Unimplemented routes always fail loudly — this library never silently fakes
> success.

## Why

Unit tests can cover your business logic, but the bugs that bite Discord bots live in the
glue: converters, checks, permissions, forgotten `tree.sync()` calls, double-acknowledged
interactions, oversized embeds. Until now the only way to test that layer was manually, in
a real server. `discord-py-test` runs all of discord.py's real machinery — its parsers,
cache, command frameworks and views — against a faithful fake of Discord's REST API and
gateway, entirely in-process.

- **Real semantics**: server-side permission checks with authentic error codes
  (`50013 Missing Permissions`…), interaction lifecycle rules (`40060` on double-ack),
  role hierarchy, timeouts, ephemeral visibility, validation limits.
- **Real bugs caught**: invoking a slash command that was never synced fails your test,
  just like it fails in production. Clicking a disabled button is impossible, just like
  in the client.
- **Fast and deterministic**: no sleeps, no network, reproducible IDs. The framework
  tracks the bot's tasks and settles after every action.

## Install

```bash
pip install discord-py-test[pytest]
```

Requires Python 3.11+ and discord.py 2.7+.

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

async def test_ping(dpt_env):
    channel = dpt_env.create_guild().create_text_channel("general")
    alice = dpt_env.guild.add_member(dpt_env.create_user("alice"))

    await alice.send(channel, "!ping")                    # full gateway round trip

    assert channel.last_message.content == "Pong!"

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
everything your bot touches — is real discord.py code running unmodified. Details in the
[architecture docs](https://silenthacks.github.io/discord-py-test/architecture/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with a failing test are gold; if your
bot hits an unimplemented route, the error names it — please open a parity gap issue.

## License

MIT. Not affiliated with Discord Inc. or the discord.py project.
