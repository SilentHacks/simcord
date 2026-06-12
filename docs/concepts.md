---
title: "Core concepts"
description: "Understand SimCord's model: the Env, omnipotent builders, permission-checked actors, and plain-Python queries. The mental model behind every discord.py test."
---

# Core concepts

Every SimCord test is built from four things: an **environment**, **builders**, **actors**,
and **queries**. Once these click, the rest of the docs are just detail.

## The environment (`Env`)

The `Env` is your handle on one running bot attached to one virtual Discord. You almost
never construct it directly — the [`simcord_env` fixture](guides/fixtures.md) or
[`simcord.run`](guides/fixtures.md#without-pytest) gives you one, already logged in and at
`READY`:

```python
async def test_something(simcord_env):
    env = simcord_env          # an Env
    guild = env.create_guild()
    ...
```

The `Env` owns the virtual backend, tracks the bot's background tasks, captures errors,
holds the virtual clock, and exposes the diagnostics (`env.http_log`, `env.transcript()`).
A fresh `Env` is created for each test, so tests never leak state into each other.

!!! info "One environment per event loop"
    `Env` monkeypatches `loop.create_task` and `time.monotonic` while it's live so it can
    [settle deterministically](#why-actors-await) and [control time](guides/time-control.md).
    Because of that, only one `Env` may be live on a given event loop at a time.

## Builders — arrange the world

**Builders are synchronous and omnipotent.** They write directly to the backend with no
permission checks, because the *test* is the omnipotent narrator setting up a scenario —
not a user doing things. Use them to build the guild, channels, roles and members your test
needs.

```python
guild   = env.create_guild("My Server")
general = guild.create_text_channel("general")
mods    = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
alice   = guild.add_member(env.create_user("alice"), roles=[mods])
```

Builders return **handles** (`GuildHandle`, `ChannelHandle`, `RoleHandle`, `UserHandle`)
— lightweight references you pass to actors and queries. They are not discord.py model
objects; queries are where you get those (see below).

Common builders:

| Builder | Creates |
| --- | --- |
| `env.create_user(name)` | A `UserHandle` — a human, independent of any guild. |
| `env.create_guild(name="Test Guild")` | A `GuildHandle`. |
| `guild.create_text_channel(name, *, overwrites=…, topic=…)` | A `ChannelHandle`. |
| `guild.create_role(name, *, permissions=…)` | A `RoleHandle`. |
| `guild.add_member(user, *, roles=…, nick=…)` | A `MemberActor` (the user, now in the guild). |

!!! tip "`env.guild` is a shortcut"
    The first guild you create is available as `env.guild`, for the common single-guild
    test. `env.guild.create_text_channel(...)` reads nicely.

## Actors — act as a real user

**Actors are async and permission-checked.** A `MemberActor` (returned by
`guild.add_member`) represents a human and can only do what that human physically could in
the Discord client. Every actor verb is checked against the permission engine and the
client's own constraints:

```python
await alice.send(channel, "!ping")                  # checks send_messages
await alice.slash(channel, "ban", user=target)      # checks use_application_commands + sync
await alice.click(message, label="Confirm")         # button must exist, be enabled, be visible
```

If the scenario is impossible — speaking in a channel they can't see, clicking a disabled
button, invoking an unsynced command — the actor raises a `SetupError` pointing at your
**test setup**, distinct from a bug in the bot. This is what makes permission-sensitive and
lifecycle bugs testable.

### Why actors await

Every actor verb is `async` and **waits for the bot to finish reacting** before returning.
SimCord tracks every task your bot spawns and "settles" the event loop — running callbacks,
awaiting handlers, even draining `asyncio.sleep` chains — until the bot is quiescent. So
this is race-free with no sleeps:

```python
await alice.send(channel, "!ping")
assert channel.last_message.content == "Pong!"   # the reply is already here
```

If a handler hangs, settling fails fast with the pending tasks listed, rather than your
assertion flaking. (Tasks genuinely parked waiting for *future* user input — like a `View`
in `wait_for` — are correctly left running.)

The actor verbs, by area:

| Area | Verbs |
| --- | --- |
| Text | `send`, `edit`, `delete`, `typing`, `send_dm` |
| Reactions | `react`, `unreact` |
| App commands | `slash`, `context_menu`, `autocomplete` |
| Components | `click`, `select`, `submit_modal` |

Each is covered in its [guide](guides/messages.md).

## Queries — assert what happened

Queries read the resulting state, returning **real discord.py objects** wherever possible —
straight from the bot's own cache, which doubles as a check that gateway dispatch populated
it correctly. You then assert with plain Python and pytest's normal introspection; there's
no verification DSL to learn.

```python
channel.last_message                 # discord.Message | None
channel.history()                    # list[discord.Message], oldest first
channel.history(viewer=mod)          # ephemeral-aware: what `mod` would see
channel.pinned_messages()            # list[discord.Message]
guild.get_ban(target)                # ban record | None
env.errors                           # exceptions the bot swallowed
env.http_log                         # every REST call the bot made
```

Interaction verbs return a richer [`InteractionResult`](api.md#simcord.InteractionResult) capturing
the full response — `acknowledged`, `deferred`, `ephemeral`, `response`, `followups`,
`modal` — see [Slash commands](guides/interactions.md).

## Putting it together

```python
import discord

async def test_welcome_on_join(simcord_env):
    # Builders: arrange
    guild = simcord_env.create_guild()
    welcome = guild.create_text_channel("welcome")

    # Actor: act (joining is a builder; here the bot reacts to the join event)
    newbie = guild.add_member(simcord_env.create_user("newbie"))

    # Query: assert
    assert f"Welcome {newbie.mention}" in welcome.last_message.content
```

Builders set the stage, actors take an action, queries check the outcome. Every guide in
these docs is a variation on that loop.

## Next

- [Fixtures & configuration](guides/fixtures.md) — `simcord_env`, `simcord.run`, and options
  like `strict_sync` and `check_errors`.
- [Messages & prefix commands](guides/messages.md) — start exercising your bot.
