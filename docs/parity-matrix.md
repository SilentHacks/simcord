# Parity matrix

What the virtual Discord implements today. Anything not listed fails **loudly** with a
`RouteNotImplemented` error naming the route — please open a
[parity gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md)
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
| View timeout fast-forward (`advance_time`) | ✅ | Virtual clock; fires view timeouts, cooldowns, sleep chains |
| Rate limit simulation | ❌ | Deliberate: tests stay fast; use `inject_error` for 429 paths |
| Multiple bots in one Env | ❌ | The backend broadcasts to N clients, but `Env` currently drives one bot |
| Sharding simulation | ❌ | Single virtual shard |

## Implemented routes

This section is generated from the route table (`python -m simcord.parity`),
so it is exact by construction.

<!-- routes:begin (generated — do not edit by hand) -->

52 routes implemented. Anything else fails loudly with `RouteNotImplemented`.

| Method | Route |
| --- | --- |
| `GET` | `/applications/{application_id}/commands` |
| `PUT` | `/applications/{application_id}/commands` |
| `GET` | `/applications/{application_id}/guilds/{guild_id}/commands` |
| `PUT` | `/applications/{application_id}/guilds/{guild_id}/commands` |
| `GET` | `/channels/{channel_id}` |
| `PATCH` | `/channels/{channel_id}` |
| `DELETE` | `/channels/{channel_id}` |
| `GET` | `/channels/{channel_id}/messages` |
| `POST` | `/channels/{channel_id}/messages` |
| `GET` | `/channels/{channel_id}/messages/pins` |
| `PUT` | `/channels/{channel_id}/messages/pins/{message_id}` |
| `DELETE` | `/channels/{channel_id}/messages/pins/{message_id}` |
| `GET` | `/channels/{channel_id}/messages/{message_id}` |
| `PATCH` | `/channels/{channel_id}/messages/{message_id}` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}` |
| `GET` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}` |
| `PUT` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/{user_id}` |
| `POST` | `/channels/{channel_id}/messages/{message_id}/threads` |
| `PUT` | `/channels/{channel_id}/permissions/{target_id}` |
| `DELETE` | `/channels/{channel_id}/permissions/{target_id}` |
| `POST` | `/channels/{channel_id}/threads` |
| `POST` | `/channels/{channel_id}/typing` |
| `GET` | `/channels/{channel_id}/webhooks` |
| `POST` | `/channels/{channel_id}/webhooks` |
| `GET` | `/guilds/{guild_id}` |
| `GET` | `/guilds/{guild_id}/bans` |
| `GET` | `/guilds/{guild_id}/bans/{user_id}` |
| `PUT` | `/guilds/{guild_id}/bans/{user_id}` |
| `DELETE` | `/guilds/{guild_id}/bans/{user_id}` |
| `GET` | `/guilds/{guild_id}/members/{user_id}` |
| `PATCH` | `/guilds/{guild_id}/members/{user_id}` |
| `DELETE` | `/guilds/{guild_id}/members/{user_id}` |
| `PUT` | `/guilds/{guild_id}/members/{user_id}/roles/{role_id}` |
| `DELETE` | `/guilds/{guild_id}/members/{user_id}/roles/{role_id}` |
| `GET` | `/guilds/{guild_id}/roles` |
| `POST` | `/guilds/{guild_id}/roles` |
| `PATCH` | `/guilds/{guild_id}/roles/{role_id}` |
| `DELETE` | `/guilds/{guild_id}/roles/{role_id}` |
| `POST` | `/interactions/{interaction_id}/{token}/callback` |
| `GET` | `/oauth2/applications/@me` |
| `GET` | `/users/@me` |
| `POST` | `/users/@me/channels` |
| `GET` | `/users/{user_id}` |
| `POST` | `/webhooks/{webhook_id}/{token}` |
| `GET` | `/webhooks/{webhook_id}/{token}/messages/@original` |
| `PATCH` | `/webhooks/{webhook_id}/{token}/messages/@original` |
| `DELETE` | `/webhooks/{webhook_id}/{token}/messages/@original` |
| `GET` | `/webhooks/{webhook_id}/{token}/messages/{message_id}` |
| `PATCH` | `/webhooks/{webhook_id}/{token}/messages/{message_id}` |
| `DELETE` | `/webhooks/{webhook_id}/{token}/messages/{message_id}` |

<!-- routes:end -->
