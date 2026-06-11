# Permissions

The backend implements Discord's documented permission algorithm: base permissions from
role union (with owner and `administrator` short-circuits), then channel overwrites
(@everyone → aggregated roles → member), then timeout masking. Both the **bot** and
**simulated users** are checked server-side, with authentic error codes.

## Setting up permissions

```python
mods = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
mod = guild.add_member(env.create_user("mod"), roles=[mods])

locked = guild.create_text_channel(
    "locked",
    overwrites={guild.default_role: discord.PermissionOverwrite(send_messages=False)},
)
```

By default the bot gets a managed integration role with broad permissions but **not**
administrator — like a typical bot invite — so channel overwrites apply to it. Guilds get
a synthetic owner (never the bot), since owners bypass every check.

## What gets enforced

- The bot sending into a channel it can't speak in → `discord.Forbidden` with code
  `50013`, captured in `env.errors` if the command doesn't handle it.
- A simulated user acting where they can't see/speak → the test fails with a clear
  "your test setup denies this" error (`dpt.BackendError`), distinguishing setup mistakes
  from bot bugs.
- Role hierarchy: the bot cannot kick/ban/timeout members whose top role is at or above
  its own, exactly like real Discord.
- Timeouts: a timed-out member loses everything except viewing and reading history.

## Client-side checks work too

Because the cache is populated through discord.py's real parsers,
`@commands.has_permissions(...)`, `interaction.permissions`, and
`member.guild_permissions` all see correct values without any extra setup.
