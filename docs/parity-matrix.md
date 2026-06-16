---
title: "Parity matrix"
description: "What SimCord's virtual Discord implements today — messages, slash commands, components, permissions, threads and more — plus the exact list of supported REST routes. Anything unimplemented fails loudly."
---

# Parity matrix

What the virtual Discord implements today. Anything not listed fails **loudly** — an
unimplemented route raises `RouteNotImplemented`, and an unrecognised request field
raises `UnsupportedField` rather than being silently dropped — so a passing test never
hides a parity gap. Please open a
[parity gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md)
if your bot needs one.

Both lists below are generated and verified in CI: implemented routes come from the route
table, the not-yet-implemented list is derived from `discord.http.HTTPClient`, and
serializer payloads are conformance-tested against discord.py's own model parsers.

| Area | Status | Notes |
| --- | --- | --- |
| Login / READY / setup_hook | ✅ | Real discord.py login flow, application info |
| Gateway intents | ✅ | Server-side gating, `message_content` censoring, member chunking, 4014 — see the [Intents guide](guides/intents.md) |
| Messages (send/edit/delete/fetch/history) | ✅ | Content & embed limits enforced (`50035`); `publish` (announcement crosspost) |
| Bulk delete (`purge` / `delete_messages`) | ✅ | 2–100 messages, single `MESSAGE_DELETE_BULK`, audit-logged |
| Embeds, attachments, replies, mentions | ✅ | In-memory CDN; `attachment.read()` works |
| Pins | ✅ | Current paginated endpoints |
| Typing | ✅ | Both directions |
| Reactions | ✅ | Add/remove/list, clear-all & clear-emoji, gateway events |
| DM channels | ✅ | User→bot and bot→user |
| Threads | ✅ | Create (standalone & from message), messaging within; join/leave, add/remove member, `fetch_members`, `archived_threads`, `Guild.active_threads`, `edit(archived=/locked=)` |
| Forum posts | ✅ | `ForumChannel.create_thread` (starter message + `applied_tags`); tag config via edit |
| Prefix commands (`ext.commands`) | ✅ | Converters, checks, cooldowns, error handlers |
| Permissions engine | ✅ | Overwrites, hierarchy, timeouts, owner/admin |
| Slash commands | ✅ | Options, choices, resolved data, subcommand groups |
| App command permissions | ✅ | `AppCommand.fetch_permissions`; seed via `guild.set_command_permissions` |
| Context menus (user & message) | ✅ | |
| Autocomplete | ✅ | |
| Interaction lifecycle | ✅ | Defer, followups, `@original` ops, `40060` on double-ack |
| Ephemeral semantics | ✅ | Visibility-aware history and component access |
| Buttons / selects / modals | ✅ | Real `View` dispatch; disabled/missing rejected |
| User/role/channel/mentionable selects | ✅ | Pass the handles a user could pick; resolved data built |
| Bot restart / persistent views | ✅ | `env.restart_bot()` replays the world; persistent views re-attach |
| Members (join/leave, kick/ban/unban, nick, roles, timeout) | ✅ | Hierarchy enforced; `fetch_members` listing; `bulk_ban`, `prune_members`/`estimate_pruned_members` (roleless = inactive); the bot's own nick (`guild.me.edit`) |
| Roles (create/edit/delete) | ✅ | `Guild.fetch_role`; reorder via `Guild.edit_role_positions` |
| Guilds (create/edit/delete) | ✅ | `Client.create_guild`, `Guild.edit`, `Guild.delete` (owner-only); `GUILD_UPDATE` audit; `Guild.leave`, `Client.fetch_guilds`, `ClientUser.edit` (bot username); `Guild.vanity_invite` (settable via `guild.set_vanity_url`) |
| Channels (create/edit/delete, overwrites) | ✅ | Runtime create + list; text, voice, stage, category & forum kinds; reorder/move (`Channel.move`); announcement `TextChannel.follow` |
| Webhooks | ✅ | Create, execute, fetch/edit/delete (by id or token), guild listing |
| Fault injection / HTTP log | ✅ | `env.inject_error`, `env.http_log` |
| Audit logs | ✅ | Recorded for ban/kick/role/member/channel/event actions; `guild.audit_logs()`, filtering |
| Polls | ✅ | Message-level poll object; `actor.vote`, expiry (route + `advance_time`), vote events |
| Scheduled events | ✅ | CRUD + subscribe/unsubscribe; auto status transitions via `advance_time` |
| Voice state | ✅ | State only — never audio; join/leave/move/mute, request-to-speak, `VOICE_STATE_UPDATE`; `Guild.fetch_voice_state` |
| Stage instances | ✅ | `StageChannel.create_instance`/`fetch_instance`, `StageInstance.edit`/`delete`, gateway events |
| Invites | ✅ | Create/list/fetch/delete, gateway events |
| Emojis & stickers | ✅ | Guild expression CRUD, update events; application-owned emojis (`Client.create_application_emoji`, `fetch_application_emojis`) |
| Auto-moderation | ✅ | Rule CRUD + keyword & mention-spam execution (block/alert) on send |
| View timeout fast-forward (`advance_time`) | ✅ | Virtual clock; fires view timeouts, cooldowns, sleep chains |
| Rate limit simulation | ❌ | Deliberate: tests stay fast; use `inject_error` for 429 paths |
| Sharding simulation | ❌ | Single virtual shard |

## Implemented routes

This section is generated from the route table (`python -m simcord.parity`),
so it is exact by construction.

<!-- routes:begin (generated — do not edit by hand) -->

134 routes implemented. Anything else fails loudly with `RouteNotImplemented`.

| Method | Route |
| --- | --- |
| `GET` | `/applications/{application_id}/commands` |
| `PUT` | `/applications/{application_id}/commands` |
| `GET` | `/applications/{application_id}/emojis` |
| `POST` | `/applications/{application_id}/emojis` |
| `GET` | `/applications/{application_id}/emojis/{emoji_id}` |
| `PATCH` | `/applications/{application_id}/emojis/{emoji_id}` |
| `DELETE` | `/applications/{application_id}/emojis/{emoji_id}` |
| `GET` | `/applications/{application_id}/guilds/{guild_id}/commands` |
| `PUT` | `/applications/{application_id}/guilds/{guild_id}/commands` |
| `GET` | `/applications/{application_id}/guilds/{guild_id}/commands/{command_id}/permissions` |
| `GET` | `/channels/{channel_id}` |
| `PATCH` | `/channels/{channel_id}` |
| `DELETE` | `/channels/{channel_id}` |
| `POST` | `/channels/{channel_id}/followers` |
| `GET` | `/channels/{channel_id}/invites` |
| `POST` | `/channels/{channel_id}/invites` |
| `GET` | `/channels/{channel_id}/messages` |
| `POST` | `/channels/{channel_id}/messages` |
| `POST` | `/channels/{channel_id}/messages/bulk-delete` |
| `GET` | `/channels/{channel_id}/messages/pins` |
| `PUT` | `/channels/{channel_id}/messages/pins/{message_id}` |
| `DELETE` | `/channels/{channel_id}/messages/pins/{message_id}` |
| `GET` | `/channels/{channel_id}/messages/{message_id}` |
| `PATCH` | `/channels/{channel_id}/messages/{message_id}` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}` |
| `POST` | `/channels/{channel_id}/messages/{message_id}/crosspost` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}/reactions` |
| `GET` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}` |
| `PUT` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me` |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/{user_id}` |
| `POST` | `/channels/{channel_id}/messages/{message_id}/threads` |
| `PUT` | `/channels/{channel_id}/permissions/{target_id}` |
| `DELETE` | `/channels/{channel_id}/permissions/{target_id}` |
| `GET` | `/channels/{channel_id}/polls/{message_id}/answers/{answer_id}` |
| `POST` | `/channels/{channel_id}/polls/{message_id}/expire` |
| `GET` | `/channels/{channel_id}/thread-members` |
| `PUT` | `/channels/{channel_id}/thread-members/@me` |
| `DELETE` | `/channels/{channel_id}/thread-members/@me` |
| `GET` | `/channels/{channel_id}/thread-members/{user_id}` |
| `PUT` | `/channels/{channel_id}/thread-members/{user_id}` |
| `DELETE` | `/channels/{channel_id}/thread-members/{user_id}` |
| `POST` | `/channels/{channel_id}/threads` |
| `GET` | `/channels/{channel_id}/threads/archived/private` |
| `GET` | `/channels/{channel_id}/threads/archived/public` |
| `POST` | `/channels/{channel_id}/typing` |
| `GET` | `/channels/{channel_id}/users/@me/threads/archived/private` |
| `GET` | `/channels/{channel_id}/webhooks` |
| `POST` | `/channels/{channel_id}/webhooks` |
| `POST` | `/guilds` |
| `GET` | `/guilds/{guild_id}` |
| `PATCH` | `/guilds/{guild_id}` |
| `DELETE` | `/guilds/{guild_id}` |
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
| `POST` | `/guilds/{guild_id}/bulk-ban` |
| `GET` | `/guilds/{guild_id}/channels` |
| `POST` | `/guilds/{guild_id}/channels` |
| `PATCH` | `/guilds/{guild_id}/channels` |
| `GET` | `/guilds/{guild_id}/emojis` |
| `POST` | `/guilds/{guild_id}/emojis` |
| `GET` | `/guilds/{guild_id}/emojis/{emoji_id}` |
| `PATCH` | `/guilds/{guild_id}/emojis/{emoji_id}` |
| `DELETE` | `/guilds/{guild_id}/emojis/{emoji_id}` |
| `GET` | `/guilds/{guild_id}/invites` |
| `GET` | `/guilds/{guild_id}/members` |
| `PATCH` | `/guilds/{guild_id}/members/@me` |
| `GET` | `/guilds/{guild_id}/members/{user_id}` |
| `PATCH` | `/guilds/{guild_id}/members/{user_id}` |
| `DELETE` | `/guilds/{guild_id}/members/{user_id}` |
| `PUT` | `/guilds/{guild_id}/members/{user_id}/roles/{role_id}` |
| `DELETE` | `/guilds/{guild_id}/members/{user_id}/roles/{role_id}` |
| `GET` | `/guilds/{guild_id}/prune` |
| `POST` | `/guilds/{guild_id}/prune` |
| `GET` | `/guilds/{guild_id}/roles` |
| `POST` | `/guilds/{guild_id}/roles` |
| `PATCH` | `/guilds/{guild_id}/roles` |
| `GET` | `/guilds/{guild_id}/roles/{role_id}` |
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
| `GET` | `/guilds/{guild_id}/threads/active` |
| `GET` | `/guilds/{guild_id}/vanity-url` |
| `GET` | `/guilds/{guild_id}/voice-states/@me` |
| `PATCH` | `/guilds/{guild_id}/voice-states/@me` |
| `GET` | `/guilds/{guild_id}/voice-states/{user_id}` |
| `PATCH` | `/guilds/{guild_id}/voice-states/{user_id}` |
| `GET` | `/guilds/{guild_id}/webhooks` |
| `POST` | `/interactions/{interaction_id}/{token}/callback` |
| `GET` | `/invites/{code}` |
| `DELETE` | `/invites/{code}` |
| `GET` | `/oauth2/applications/@me` |
| `POST` | `/stage-instances` |
| `GET` | `/stage-instances/{channel_id}` |
| `PATCH` | `/stage-instances/{channel_id}` |
| `DELETE` | `/stage-instances/{channel_id}` |
| `GET` | `/users/@me` |
| `PATCH` | `/users/@me` |
| `POST` | `/users/@me/channels` |
| `GET` | `/users/@me/guilds` |
| `DELETE` | `/users/@me/guilds/{guild_id}` |
| `GET` | `/users/{user_id}` |
| `GET` | `/webhooks/{webhook_id}` |
| `PATCH` | `/webhooks/{webhook_id}` |
| `DELETE` | `/webhooks/{webhook_id}` |
| `GET` | `/webhooks/{webhook_id}/{token}` |
| `POST` | `/webhooks/{webhook_id}/{token}` |
| `PATCH` | `/webhooks/{webhook_id}/{token}` |
| `DELETE` | `/webhooks/{webhook_id}/{token}` |
| `GET` | `/webhooks/{webhook_id}/{token}/messages/@original` |
| `PATCH` | `/webhooks/{webhook_id}/{token}/messages/@original` |
| `DELETE` | `/webhooks/{webhook_id}/{token}/messages/@original` |
| `GET` | `/webhooks/{webhook_id}/{token}/messages/{message_id}` |
| `PATCH` | `/webhooks/{webhook_id}/{token}/messages/{message_id}` |
| `DELETE` | `/webhooks/{webhook_id}/{token}/messages/{message_id}` |

<!-- routes:end -->

## Not yet implemented

These discord.py REST routes have no handler yet, derived by comparing simcord's
route table against `discord.http.HTTPClient` (`python -m simcord.parity`), so the
list stays honest as discord.py evolves.

This is the **frozen gap surface heading into 1.0**: the omissions are deliberate
and demand-driven, not unfinished work. Most are niche (monetization, soundboard,
guild templates) or have a working common path already — individual application
command CRUD is unlisted because `CommandTree.sync` (the bulk overwrite) is fully
modelled. Others (integrations, welcome screen, widget, onboarding) are omitted on
purpose: simcord has no settable state behind them, so a handler returning a
constant empty value would be a *silent fake* — exactly what this project refuses
to ship. They fail loudly until there is demand to model them properly. Open an
issue if your bot needs one.

<!-- gaps:begin (generated — do not edit by hand) -->

53 discord.py REST routes are not yet implemented; calling one fails loudly with `RouteNotImplemented` (path parameters shown as `{}`). Open an issue if your bot needs one.

| discord.py `HTTPClient` method | Route |
| --- | --- |
| `edit_application_info` | `PATCH /applications/@me` |
| `upsert_global_command` | `POST /applications/{}/commands` |
| `get_global_command` | `GET /applications/{}/commands/{}` |
| `edit_global_command` | `PATCH /applications/{}/commands/{}` |
| `delete_global_command` | `DELETE /applications/{}/commands/{}` |
| `get_entitlements` | `GET /applications/{}/entitlements` |
| `create_entitlement` | `POST /applications/{}/entitlements` |
| `get_entitlement` | `GET /applications/{}/entitlements/{}` |
| `delete_entitlement` | `DELETE /applications/{}/entitlements/{}` |
| `consume_entitlement` | `POST /applications/{}/entitlements/{}/consume` |
| `upsert_guild_command` | `POST /applications/{}/guilds/{}/commands` |
| `get_guild_application_command_permissions` | `GET /applications/{}/guilds/{}/commands/permissions` |
| `get_guild_command` | `GET /applications/{}/guilds/{}/commands/{}` |
| `edit_guild_command` | `PATCH /applications/{}/guilds/{}/commands/{}` |
| `delete_guild_command` | `DELETE /applications/{}/guilds/{}/commands/{}` |
| `edit_application_command_permissions` | `PUT /applications/{}/guilds/{}/commands/{}/permissions` |
| `get_skus` | `GET /applications/{}/skus` |
| `logout` | `POST /auth/logout` |
| `send_soundboard_sound` | `POST /channels/{}/send-soundboard-sound` |
| `edit_voice_channel_status` | `PUT /channels/{}/voice-status` |
| `get_template` | `GET /guilds/templates/{}` |
| `create_from_template` | `POST /guilds/templates/{}` |
| `edit_incident_actions` | `PUT /guilds/{}/incident-actions` |
| `get_all_integrations` | `GET /guilds/{}/integrations` |
| `create_integration` | `POST /guilds/{}/integrations` |
| `edit_integration` | `PATCH /guilds/{}/integrations/{}` |
| `delete_integration` | `DELETE /guilds/{}/integrations/{}` |
| `sync_integration` | `POST /guilds/{}/integrations/{}/sync` |
| `edit_guild_mfa_level` | `POST /guilds/{}/mfa` |
| `get_guild_onboarding` | `GET /guilds/{}/onboarding` |
| `get_guild_preview` | `GET /guilds/{}/preview` |
| `get_role_member_counts` | `GET /guilds/{}/roles/member-counts` |
| `get_soundboard_sounds` | `GET /guilds/{}/soundboard-sounds` |
| `create_soundboard_sound` | `POST /guilds/{}/soundboard-sounds` |
| `get_soundboard_sound` | `GET /guilds/{}/soundboard-sounds/{}` |
| `edit_soundboard_sound` | `PATCH /guilds/{}/soundboard-sounds/{}` |
| `delete_soundboard_sound` | `DELETE /guilds/{}/soundboard-sounds/{}` |
| `guild_templates` | `GET /guilds/{}/templates` |
| `create_template` | `POST /guilds/{}/templates` |
| `sync_template` | `PUT /guilds/{}/templates/{}` |
| `edit_template` | `PATCH /guilds/{}/templates/{}` |
| `delete_template` | `DELETE /guilds/{}/templates/{}` |
| `change_vanity_code` | `PATCH /guilds/{}/vanity-url` |
| `get_welcome_screen` | `GET /guilds/{}/welcome-screen` |
| `edit_welcome_screen` | `PATCH /guilds/{}/welcome-screen` |
| `edit_widget` | `PATCH /guilds/{}/widget` |
| `get_widget` | `GET /guilds/{}/widget.json` |
| `list_sku_subscriptions` | `GET /skus/{}/subscriptions` |
| `get_sku_subscription` | `GET /skus/{}/subscriptions/{}` |
| `get_soundboard_default_sounds` | `GET /soundboard-default-sounds` |
| `list_premium_sticker_packs` | `GET /sticker-packs` |
| `get_sticker_pack` | `GET /sticker-packs/{}` |
| `get_sticker` | `GET /stickers/{}` |

<!-- gaps:end -->

## Out of scope

Some discord.py REST routes map to actions a bot account simply cannot perform.
These are deliberate non-goals — not backlog — so they are tracked separately
from the gap list above. Calling one still fails loudly with `RouteNotImplemented`.

<!-- out-of-scope:begin (generated — do not edit by hand) -->

1 discord.py REST route(s) are intentionally out of scope — actions a bot cannot perform, so they will not be implemented (calling one still fails loudly with `RouteNotImplemented`).

| discord.py `HTTPClient` method | Route |
| --- | --- |
| `start_group` | `POST /users/{}/channels` |

<!-- out-of-scope:end -->
