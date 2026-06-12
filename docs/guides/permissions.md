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

Overwrite targets can be roles (`guild.default_role`, any `RoleHandle`) or members
(`MemberActor`).

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
