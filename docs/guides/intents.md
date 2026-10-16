---
title: "Intents"
description: "SimCord simulates the server side of gateway intents: events outside your declared intents are dropped, message content is censored without the message_content intent, and member lists arrive via real chunking — so the classic 'works in tests, deaf in production' bug fails your tests instead."
---

# Intents

Gateway intents have two halves. discord.py implements the **client half** (cache
policy, flag validation) — and because SimCord runs your real bot, that half always
worked. SimCord also implements the **server half**, the part real Discord does:

- Events outside your declared intents are **never delivered** to the bot.
- Without the `message_content` privileged intent, message **content is censored**.
- `GUILD_CREATE` carries only the bot's own member; the rest arrives via **member
  chunking** (`members` intent) exactly as on the real gateway.

That means the single most common intent bug — a prefix-command bot constructed with
`Intents.default()` that passes local testing and then silently ignores every command
in production — now fails your tests too:

```python
bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

async with simcord.run(bot) as env:
    guild = env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(env.create_user("alice"))
    await alice.send(channel, "!ping")
    assert channel.last_message.content == "Pong!"   # FAILS — and should!
```

Without `intents.message_content = True`, the bot receives the `MESSAGE_CREATE`
event with `content=""` — Discord's documented behaviour — so the command never
fires. The transcript explains why:

```
GATEWAY  MESSAGE_CREATE               author=alice content='!ping'
CENSORED MESSAGE_CREATE               'content hidden: requires the message_content intent'
```

## Event delivery

Every gateway event is gated on the matching intent from
[Discord's intent list](https://discord.com/developers/docs/events/gateway#list-of-intents),
with guild/DM context decided by the payload, just like the real gateway:

| Events | Intent |
| --- | --- |
| `GUILD_*`, `CHANNEL_*`, `THREAD_*`, role events | `guilds` |
| `GUILD_MEMBER_ADD/UPDATE/REMOVE` | `members` *(privileged)* |
| `MESSAGE_CREATE/UPDATE/DELETE` | `guild_messages` / `dm_messages` |
| `MESSAGE_REACTION_*` | `guild_reactions` / `dm_reactions` |
| `TYPING_START` | `guild_typing` / `dm_typing` |
| `PRESENCE_UPDATE` | `presences` *(privileged)* |

Dropped events are recorded in the transcript so a mysteriously-quiet test can
explain itself:

```
DROPPED  GUILD_MEMBER_ADD             'requires the members intent'
```

`READY`, `INTERACTION_CREATE` and `GUILD_MEMBERS_CHUNK` are always delivered —
interactions are not intent-gated on real Discord either.

## Message content censoring

Gateway delivery uses the client's requested `message_content` bit. Without it,
`content` becomes `""`, `embeds`, `attachments` and `components` become empty lists,
and `poll` is omitted. DMs, messages authored by this bot, direct app mentions
(including Components V2 text), and ping-enabled replies to this bot's **regular**
messages remain readable. A reply to an interaction response is not exempt merely
because it pings its author; a quiet reply is not exempt either. Role-only and
everyone mentions do not grant direct-app access.

REST uses the application's **portal approval**, independently of the Gateway bit.
This applies to fetches, every history window, pins and message-bearing mutation
responses. Referenced messages are assessed independently, even below readable bot
messages. Token-owned webhook responses retain their contextual root access only
after ownership validation; their references do not inherit it.

| Gateway requests content | Portal approves content | Gateway | REST |
| --- | --- | --- | --- |
| No | No | Redacted | Redacted |
| No | Yes | Redacted | Readable |
| Yes | Yes | Readable | Readable |
| Yes | No | Startup rejects with `PrivilegedIntentsRequired` | No attached session |

The table describes ordinary, non-exempt guild messages. To simulate the first row:

```python
bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
async with simcord.run(bot, approved_intents=discord.Intents.none()) as env:
    ...
```

Message-command targets and component/modal source messages remain readable in
their interaction context, but their references are assessed independently.
Selecting a message never grants subsequent arbitrary REST access. Slash arguments
and submitted modal values are not message-content fields.

Backend, actor returns and fixture queries retain authoritative content; inspect
the bot's event callbacks or SDK fetches to assert its restricted view. The transcript
also retains authoritative event data and is **not** a privacy-redacted export.
These contracts are covered offline; reply wire metadata and edit/deletion details
have not been calibrated against a live Discord capture.
Forward snapshots inherit their containing message's content access: redaction removes
content-bearing fields while retaining snapshot metadata.

## Member chunking

`GUILD_CREATE` inlines only the bot's own member — the real gateway never ships
the full member list there (except for small guilds when the `presences` intent
is enabled, which SimCord also honours). With the `members` intent, discord.py's
real chunking machinery kicks in and SimCord answers `REQUEST_GUILD_MEMBERS`
authentically: 1000 members per `GUILD_MEMBERS_CHUNK`, nonce echoing,
case-insensitive username/nick prefix queries, `user_ids` lookups with
`not_found`, and presences when requested. So all of these work — and keep
discord.py's own client-side intent guards:

```python
await guild.chunk()                          # needs intents.members
await guild.query_members(query="ali")       # prefix-matches usernames and nicks
await guild.query_members(user_ids=[user_id])
```

Without the `members` intent, members never enter the bot's cache via the
gateway — `guild.get_member(...)` returns `None` for everyone but the bot, just
like production. Fetch them over REST instead, or enable the intent.

## Privileged intents and the developer portal

Real Discord refuses the connection (close code 4014 →
`discord.PrivilegedIntentsRequired`) when the bot declares a privileged intent
(`members`, `presences`, `message_content`) that is not enabled in the developer
portal. The default `approved_intents=None` behaves as if every toggle is enabled,
including unrestricted REST content, preserving existing fixtures. An explicit
`Intents.none()` denies approval. Approval is resolved per bot attachment and refreshed
on restart; changing it while attached is not a hot-reconfiguration API. To simulate an
unapproved portal, pass `approved_intents`:

```python
bot = commands.Bot(command_prefix="!", intents=intents_with_members)

with pytest.raises(discord.PrivilegedIntentsRequired):
    async with simcord.run(bot, approved_intents=discord.Intents.default()):
        ...
```
