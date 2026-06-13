---
title: "Parity matrix"
description: "What SimCord's virtual Discord implements today — messages, slash commands, components, permissions, threads and more — plus the exact list of supported REST routes. Anything unimplemented fails loudly."
---

# Parity matrix

What the virtual Discord implements today. Anything not listed fails **loudly** with a
`RouteNotImplemented` error naming the route — please open a
[parity gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md)
if your bot needs it.

| Area | Status | Notes |
| --- | --- | --- |
| Login / READY / setup_hook | ✅ | Real discord.py login flow, application info |
| Gateway intents | ✅ | Server-side gating, `message_content` censoring, member chunking, 4014 — see the [Intents guide](guides/intents.md) |
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
| Buttons / selects / modals | ✅ | Real `View` dispatch; disabled/missing rejected |
| User/role/channel/mentionable selects | ✅ | Pass the handles a user could pick; resolved data built |
| Bot restart / persistent views | ✅ | `env.restart_bot()` replays the world; persistent views re-attach |
| Members (join/leave, kick/ban/unban, nick, roles, timeout) | ✅ | Hierarchy enforced |
| Roles (create/edit/delete) | ✅ | |
| Channels (edit/delete, overwrites) | ✅ | Text, voice, stage, category & forum kinds |
| Webhooks | ✅ | Create + execute (channel webhooks) |
| Fault injection / HTTP log | ✅ | `env.inject_error`, `env.http_log` |
| Audit logs | ✅ | Recorded for ban/kick/role/member/channel/event actions; `guild.audit_logs()`, filtering |
| Polls | ✅ | Message-level poll object; `actor.vote`, expiry (route + `advance_time`), vote events |
| Scheduled events | ✅ | CRUD + subscribe/unsubscribe; stage/voice/external |
| Voice state | ✅ | State only — never audio; join/leave/move/mute, `VOICE_STATE_UPDATE` |
| Invites | ✅ | Create/list/fetch/delete, gateway events |
| Emojis & stickers | ✅ | Guild expression CRUD, update events |
| Auto-moderation | ✅ | Rule CRUD + keyword execution (block/alert) on send |
| View timeout fast-forward (`advance_time`) | ✅ | Virtual clock; fires view timeouts, cooldowns, sleep chains |
| Rate limit simulation | ❌ | Deliberate: tests stay fast; use `inject_error` for 429 paths |
| Multiple bots in one Env | ❌ | The backend broadcasts to N clients, but `Env` currently drives one bot |
| Sharding simulation | ❌ | Single virtual shard |

## Implemented routes

This section is generated from the route table (`python -m simcord.parity`),
so it is exact by construction.

<!-- routes:begin (generated — do not edit by hand) -->

81 routes implemented. Anything else fails loudly with `RouteNotImplemented`.

| Method | Route |
| --- | --- |
| `GET` | `/applications/{application_id}/commands` |
| `PUT` | `/applications/{application_id}/commands` |
| `GET` | `/applications/{application_id}/guilds/{guild_id}/commands` |
| `PUT` | `/applications/{application_id}/guilds/{guild_id}/commands` |
| `GET` | `/channels/{channel_id}` |
| `PATCH` | `/channels/{channel_id}` |
| `DELETE` | `/channels/{channel_id}` |
| `GET` | `/channels/{channel_id}/invites` |
| `POST` | `/channels/{channel_id}/invites` |
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
| `GET` | `/channels/{channel_id}/polls/{message_id}/answers/{answer_id}` |
| `POST` | `/channels/{channel_id}/polls/{message_id}/expire` |
| `POST` | `/channels/{channel_id}/threads` |
| `POST` | `/channels/{channel_id}/typing` |
| `GET` | `/channels/{channel_id}/webhooks` |
| `POST` | `/channels/{channel_id}/webhooks` |
| `GET` | `/guilds/{guild_id}` |
| `GET` | `/guilds/{guild_id}/audit-logs` |
| `GET` | `/guilds/{guild_id}/auto-moderation/rules` |
| `POST` | `/guilds/{guild_id}/auto-moderation/rules` |
| `GET` | `/guilds/{guild_id}/auto-moderation/rules/{rule_id}` |
| `PATCH` | `/guilds/{guild_id}/auto-moderation/rules/{rule_id}` |
| `DELETE` | `/guilds/{guild_id}/auto-moderation/rules/{rule_id}` |
| `GET` | `/guilds/{guild_id}/bans` |
| `GET` | `/guilds/{guild_id}/bans/{user_id}` |
| `PUT` | `/guilds/{guild_id}/bans/{user_id}` |
| `DELETE` | `/guilds/{guild_id}/bans/{user_id}` |
| `GET` | `/guilds/{guild_id}/emojis` |
| `POST` | `/guilds/{guild_id}/emojis` |
| `GET` | `/guilds/{guild_id}/emojis/{emoji_id}` |
| `PATCH` | `/guilds/{guild_id}/emojis/{emoji_id}` |
| `DELETE` | `/guilds/{guild_id}/emojis/{emoji_id}` |
| `GET` | `/guilds/{guild_id}/invites` |
| `GET` | `/guilds/{guild_id}/members/{user_id}` |
| `PATCH` | `/guilds/{guild_id}/members/{user_id}` |
| `DELETE` | `/guilds/{guild_id}/members/{user_id}` |
| `PUT` | `/guilds/{guild_id}/members/{user_id}/roles/{role_id}` |
| `DELETE` | `/guilds/{guild_id}/members/{user_id}/roles/{role_id}` |
| `GET` | `/guilds/{guild_id}/roles` |
| `POST` | `/guilds/{guild_id}/roles` |
| `PATCH` | `/guilds/{guild_id}/roles/{role_id}` |
| `DELETE` | `/guilds/{guild_id}/roles/{role_id}` |
| `GET` | `/guilds/{guild_id}/scheduled-events` |
| `POST` | `/guilds/{guild_id}/scheduled-events` |
| `GET` | `/guilds/{guild_id}/scheduled-events/{event_id}` |
| `PATCH` | `/guilds/{guild_id}/scheduled-events/{event_id}` |
| `DELETE` | `/guilds/{guild_id}/scheduled-events/{event_id}` |
| `GET` | `/guilds/{guild_id}/scheduled-events/{event_id}/users` |
| `GET` | `/guilds/{guild_id}/stickers` |
| `POST` | `/guilds/{guild_id}/stickers` |
| `GET` | `/guilds/{guild_id}/stickers/{sticker_id}` |
| `PATCH` | `/guilds/{guild_id}/stickers/{sticker_id}` |
| `DELETE` | `/guilds/{guild_id}/stickers/{sticker_id}` |
| `POST` | `/interactions/{interaction_id}/{token}/callback` |
| `GET` | `/invites/{code}` |
| `DELETE` | `/invites/{code}` |
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
