# Quickstart

## Install

```bash
pip install discord-py-test[pytest]
```

Requires Python 3.11+ and discord.py 2.7+.

## Wire it into your project

The bundled pytest plugin provides a `dpt_env` fixture. Tell it how to build your bot by
defining a `dpt_bot` fixture:

```python
# conftest.py
import pytest
from mybot import create_bot   # however your project constructs its Bot

@pytest.fixture
def dpt_bot():
    return create_bot()
```

That's it — `dpt_env` now hands you a fully logged-in bot attached to a virtual Discord.
(If you prefer explicit control, use `async with discord_py_test.run(bot) as env:` directly.)

!!! tip "Your bot's `setup_hook` runs for real"
    Login is the real discord.py flow, so extension loading and `tree.sync()` in your
    `setup_hook` happen exactly as in production. That matters: commands you forget to
    sync are not invocable in tests, just like on Discord.

## Write a test

```python
import discord

async def test_ban(dpt_env):
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

## The three kinds of objects

- **Builders** (synchronous, omnipotent): `env.create_guild()`, `guild.create_text_channel()`,
  `guild.create_role()`, `guild.add_member()` — arrange the world without permission checks.
- **Actors** (async, permission-checked): `alice.send(...)`, `alice.slash(...)`,
  `alice.click(...)`, `alice.select(...)`, `alice.react(...)`, `alice.submit_modal(...)` —
  do only what a real user could. Each one waits for your bot to finish reacting before
  returning, so there is never an `asyncio.sleep` in your tests.
- **Queries**: `channel.history()`, `channel.last_message`, `env.errors` (unhandled
  command errors your bot swallowed), `env.http_log` (every REST call the bot made).

## Testing failure paths

```python
async def test_handles_api_outage(dpt_env):
    dpt_env.inject_error("POST", "/channels/*/messages", status=500)
    ...
```
