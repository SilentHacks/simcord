# Parity matrix

What the virtual Discord implements today. Anything not listed fails **loudly** with a
`RouteNotImplemented` error naming the route — please open a
[parity gap issue](https://github.com/SilentHacks/discord-py-test/issues/new?template=parity-gap.md)
if your bot needs it.

| Area | Status | Notes |
| --- | --- | --- |
| Login / READY / setup_hook | ✅ | Real discord.py login flow, application info |
| Messages (send/edit/delete/fetch/history) | ✅ | Content & embed limits enforced (`50035`) |
| Embeds, attachments, replies, mentions | ✅ | In-memory CDN; `attachment.read()` works |
| Pins | ✅ | Current paginated endpoints |
| Typing | ✅ | Both directions |
| Reactions | ✅ | Add/remove/list, gateway events |
| DM channels | ✅ | User→bot and bot→user |
| Threads | ✅ | Create (standalone & from message), messaging within |
| Prefix commands (`ext.commands`) | ✅ | Converters, checks, cooldowns, error handlers |
| Permissions engine | ✅ | Overwrites, hierarchy, timeouts, owner/admin |
| Slash commands | ✅ | Options, choices, resolved data, subcommand groups |
| Context menus (user & message) | ✅ | |
| Autocomplete | ✅ | |
| Interaction lifecycle | ✅ | Defer, followups, `@original` ops, `40060` on double-ack |
| Ephemeral semantics | ✅ | Visibility-aware history and component access |
| Buttons / string selects / modals | ✅ | Real `View` dispatch; disabled/missing rejected |
| Members (join/leave, kick/ban/unban, nick, roles, timeout) | ✅ | Hierarchy enforced |
| Roles (create/edit/delete) | ✅ | |
| Channels (edit/delete, overwrites) | ✅ | Text channels; categories/forums planned |
| Webhooks | ✅ | Create + execute (channel webhooks) |
| Fault injection / HTTP log | ✅ | `env.inject_error`, `env.http_log` |
| User/role/channel selects | 🚧 | String selects only so far |
| Voice state | 🚧 | Planned (state only — never audio) |
| Scheduled events, polls, invites, emojis, stickers | 🚧 | Planned |
| Audit logs, auto-mod | 🚧 | Planned |
| Rate limit simulation | ❌ | Deliberate: tests stay fast; use `inject_error` for 429 paths |
| View timeout fast-forward (`advance_time`) | ❌ | Under design; use short real timeouts meanwhile |
| Sharding simulation | ❌ | Single virtual shard |
