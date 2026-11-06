---
title: "Permissions"
description: "SimCord enforces Discord's real permission algorithm server-side for both your bot and simulated users — role unions, channel overwrites, hierarchy and timeouts, with authentic error codes like 50013."
---

# Permissions

Permission bugs are the bugs SimCord exists to catch. The backend implements Discord's
documented permission algorithm and applies it **server-side** to both the **bot** and the
**simulated users** — with authentic error codes — so a missing overwrite or a hierarchy
mistake fails your test instead of shipping.

## The algorithm

SimCord computes effective permissions exactly as Discord documents:

1. **Base permissions** from the union of the member's roles, with two short-circuits:
   the guild **owner** and any role with **`administrator`** get everything.
2. **Channel overwrites**, applied in order: `@everyone` overwrite → the aggregated role
   overwrites → the member-specific overwrite.
3. **Timeout masking** — a timed-out member is stripped of everything except viewing
   channels and reading history.

## Setting up permissions

Grant permissions through roles, and lock down channels with overwrites — the same
`discord.Permissions` and `discord.PermissionOverwrite` objects you use in production:

```python
import discord

mods = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
mod = guild.add_member(simcord_env.create_user("mod"), roles=[mods])

locked = guild.create_text_channel(
    "locked",
    overwrites={guild.default_role: discord.PermissionOverwrite(send_messages=False)},
)
```

Overwrite targets can be `RoleHandle`, `MemberActor` or `UserHandle` objects, or native
`discord.Role`, `discord.Member`, `discord.User` and `discord.ClientUser` objects from the
same Env's SDK state. Roles and members must belong to the target guild. Foreign SDK
state and cross-guild role/member targets are rejected before channel creation.

!!! info "The bot's default permissions"
    By default the bot gets a managed integration role with broad permissions but **not**
    `administrator` — like a typical bot invite — so channel overwrites apply to it, just
    as in a real server. Each guild also gets a synthetic **owner** (never the bot), since
    owners bypass every check and you rarely want the bot to.

## What gets enforced

- **The bot sending where it can't speak** → `discord.Forbidden` with code `50013`,
  captured in [`env.errors`](diagnostics.md) if the command doesn't handle it. This is how
  you catch "forgot the channel overwrite" bugs.
- **A simulated user acting where they can't** (no view/send) → the actor raises a
  `SetupError`, clearly flagging it as a **test-setup** problem rather than a bot bug.
- **Role hierarchy** — the bot can't kick/ban/timeout members whose top role is at or above
  its own, can't assign or edit roles above its own, and can't grant permissions it lacks
  (`50013`). It also can't delete the `@everyone` role.
- **Editing others' messages** — the bot can't edit another user's message (`50005`).
- **Timeouts** — a timed-out member loses everything except viewing and reading history.
- **Bot reactions** — `add_reactions` is required to introduce a new emoji, not
  to join an existing reaction or remove your own. Read Message History is not
  required on these bot REST paths. Simulated users and Preview retain their
  separate history-access checks.
- **Pins** — pinning and unpinning require `pin_messages`, for both bots and
  simulated users. `manage_messages` does not grant this permission.

```python
async def test_cannot_ban_higher_role(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("mod")
    admins = guild.create_role("Admins")            # above the bot's role
    boss = guild.add_member(simcord_env.create_user("boss"), roles=[admins])
    mod = guild.add_member(
        simcord_env.create_user("mod"),
        roles=[guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))],
    )

    result = await mod.slash(channel, "ban", user=boss)
    # The bot tried, Discord refused with 50013, the command surfaced it:
    assert "can't ban" in result.response.content.lower()
    assert guild.get_ban(boss) is None
```

## Bot-visible channels

The bot's cache follows Discord's [channel-obfuscation contract](https://docs.discord.com/developers/resources/channel#channel-object-obfuscated-channels):
channels it cannot view have the name `___hidden___`, the obfuscated flag, neutral
sensitive fields and a synthetic `@everyone` overwrite denying `view_channel`.
Guild channel listings omit those channels; fetching one directly is denied.
Role and overwrite changes update this projection, including threads under a
hidden parent. Categories remain visible when a child is visible, and a connected
voice channel remains visible to the bot.

Builders, actors and backend state retain their authoritative data. Use a
`ChannelHandle` to inspect test setup, not the bot cache. Interaction wire payloads
remain unobfuscated; discord.py 2.7.1 may nevertheless resolve `Interaction.channel`
from its existing cache, and does not expose the wire obfuscation flag on every
channel class.

Our [live evidence](../parity-matrix.md#behavior-evidence) includes opt-in gateway
captures for text, voice, forum and category channels. Structural fields such as
slowmode and voice limits are retained. REST list filtering follows Discord's
documented mandatory behavior; it was not yet enforced in the dated live capture
and is an explicit known gap, not a claim of matching that pre-rollout response.
Uncaptured neutral-field details and category/voice visibility edge rules remain
documented simulator policy.

## Bot REST behavior

- Embeds, uploads and polls on ordinary messages require `embed_links`,
  `attach_files` and `send_polls`. Replies require history (`160002`); fetching a
  message, pinning and crossposting also check history. Listing pins without
  history succeeds with an empty list.
- Voice and stage text actions require `connect` (`50001`). Pinning there is
  refused with `50019`, even when `pin_messages` is granted.
- Archived-thread edit, delete, bulk delete, reaction and pin actions fail with
  `50083`. Sending automatically unarchives an unlocked thread after validating
  the payload. Locked sends need `manage_threads`; locked unarchive without it
  fails with `160005`.
- Thread creators may rename, archive and lock their threads; other members
  need `manage_threads`. Edits and unarchive also need
  `send_messages_in_threads`. **Deleting requires `manage_threads`, including
  for the creator**, as confirmed by our own recording. Leaving needs no view
  access.
- `AllowedMentions` controls the returned mention arrays. Missing TTS permission,
  unmentionable role pings, ignored ephemeral flags on normal sends and denied
  known external custom emojis are modeled as successful requests with reduced
  effects, reported through [`env.degradations`](diagnostics.md#silent-degradations).

These bot recordings do not establish the rules for simulated human actors.
Actor behavior is unchanged except for the documented pin-permission split and
the docs-based locked-thread send guard.


## Application command visibility

Before an actor dispatches a slash command, autocomplete request, or context menu, SimCord checks whether that user could see the command in that guild and channel. This is separate from callback checks such as `@app_commands.checks.has_permissions(...)`; hidden commands raise `SetupError` instead of dispatching an interaction. See [slash command visibility](testing-slash-commands.md#command-visibility) for scope/context rules, permission overrides, reason codes, and fixture setup.

## Client-side checks work too

Because the cache is populated through discord.py's real parsers, the client-side helpers
see correct values with no extra setup:

```python
@commands.has_permissions(ban_members=True)   # the check passes/fails correctly
...
interaction.permissions.ban_members           # correct in your callback
member.guild_permissions.manage_messages      # correct from the cache
```

So a command guarded by `@app_commands.checks.has_permissions(...)` is tested end to end:
the check reads real cached permissions, and if it passes, the *server-side* enforcement
backs it up.

## Next

- [Slash commands](interactions.md) — where most permission checks fire in modern bots.
- [Errors & diagnostics](diagnostics.md) — inspecting the `50013`/`50005` errors your bot
  raised.
- [Recipes](../cookbook.md) — a reusable "permission denied" test pattern.
