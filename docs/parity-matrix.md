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

Both lists below are generated and verified in CI: implemented Discord routes come from the route
table, the not-yet-implemented list is derived from `discord.http.HTTPClient`, and serializer
payloads are conformance-tested against discord.py's own model parsers. Preview's `/api/*` bridge
routes are local presentation endpoints and are intentionally not part of the Discord route list.

| Area | Status | Notes |
| --- | --- | --- |
| Login / READY / setup_hook | ✅ | Real discord.py login flow, application info |
| Gateway intents | ✅ | Server-side gating, independent portal-authorized REST content projection, regular-reply exemptions, contextual interaction roots, member chunking, 4014 — see the [Intents guide](guides/intents.md) |
| Messages (send/edit/delete/fetch/history) | ✅ | Content & embed limits enforced (`50035`); immutable edit flags are retained; `publish` (announcement crosspost) |
| Bulk delete (`purge` / `delete_messages`) | ✅ | 2–100 messages, single `MESSAGE_DELETE_BULK`, audit-logged |
| Embeds, attachments, replies, mentions | ✅ | In-memory CDN; slash `ATTACHMENT` options accept `(filename, bytes)` uploads; `attachment.read()` works |
| Message forwarding | ⚠️ | Native `actor.forward(message, channel)` and SDK `message.forward(channel)`; immutable rich snapshots, permissions, content-access projection and Preview. Already-forwarded sources fail explicitly rather than flattening unverified nested semantics. |
| Pins | ✅ | Current paginated endpoints; `pin_messages`, not `manage_messages`, authorizes pin/unpin |
| Typing | ✅ | Both directions |
| Reactions | ✅ | Add/remove/list, clear-all & clear-emoji, gateway events |
| DM channels | ✅ | User→bot and bot→user; `UserHandle.slash()` / `.autocomplete()` exercise bot-DM commands |
| Threads | ✅ | Public, private and announcement threads; create (standalone & from message), messaging within; join/leave, add/remove member, `fetch_members`, `archived_threads`, `Guild.active_threads`, `edit(archived=/locked=)` |
| Forum posts | ✅ | `ForumChannel.create_thread` (starter message + `applied_tags`); tag config via edit |
| Prefix commands (`ext.commands`) | ✅ | Converters, checks, cooldowns, error handlers |
| Permissions engine | ✅ | Overwrites, hierarchy, timeouts, owner/admin |
| Bot channel visibility | ✅ | Hidden gateway channels are obfuscated, REST guild-channel listings are filtered, and role/overwrite changes refresh visibility. Authoritative setup state and interaction wire payloads stay unredacted; unspecified neutral fields remain docs-based simulator policy. |
| Slash commands | ✅ | Option constraints and choices, resolved data, subcommand groups; `available_commands()` lists visible leaf invocations; actors enforce command visibility with reason codes. NSFW registration metadata is preserved; visibility and invocation of NSFW-marked commands raise `NotImplementedError` because age gating is not modeled. |
| App command permissions | ✅ | `AppCommand.fetch_permissions`; seed via `guild.set_command_permissions` |
| Context menus (user & message) | ✅ | |
| Autocomplete | ✅ | Focused options must be declared and have autocomplete enabled |
| Interaction lifecycle | ✅ | Three-second initial deadline (`10062`), fetchable thinking originals, stable-ID completion, first-followup visibility inheritance, distinct component-update defers, `@original` ops, `40060` double-ack, `10008` deleted messages and `10015` expired continuation tokens |
| Ephemeral semantics | ✅ | Visibility-aware history and component access |
| Buttons / selects / modals | ✅ | Real `View` dispatch; disabled/missing rejected |
| User/role/channel/mentionable selects | ✅ | Pass the handles a user could pick; resolved data built |
| Components V2 / `LayoutView` | ✅ | Legal wire-tree nesting, 40-component limit, stable IDs, V2 flag/content invariants, media attachments and webhook `with_components`; arbitrary remote media is metadata-only offline |
| SKUs & entitlements | ⚠️ | Explicit subscription/group and durable/consumable catalog; six REST routes; CREATE/UPDATE/DELETE on shard zero; guild-first interaction access, consume, history/replay and restart. [Calibration and boundaries](guides/monetization.md#evidence-and-open-parity-checks) distinguish observed fields, errors, cross-tier conflicts and directional pagination from fixture policy and unverified lifecycle edges. No checkout, Subscription reporting/events, or application plans/credits/trials. |
| Local component preview | ✅ | Optional loopback page on the protocol-3 contract: bounded authorized message navigation, control-scoped candidate descriptors, typed causal action receipts, explicit refresh and page-local viewers. No protocol-2 compatibility reader; not a Discord client or network connection. |
| Preview screenshots | ✅ | Optional Playwright capture returns immutable `PreviewCapture` reports (`schema_version=1`, `protocol_version=3`) with exact requested logical viewport, visible-intersection geometry, readiness, diagnostics, completeness and calibration metadata. Surface captures never expand scroll content or include inspector chrome. |
| Preview embed presentation | ✅ | Safe title/author/provider links and offline-authorized media; adaptive field runs, thumbnail/image composition, author/footer icons and timestamps. External-only media stays unavailable; embed reference geometry remains blocked. |
| Preview buttons | ⚠️ | Styles 1–6, supplied emoji/icon content, safe disabled links, responsive wrapping and visible keyboard focus. The dated reference reported button focus equal to idle; the preview intentionally adds a focus ring. Premium names/prices/icons are caller-supplied offline data; activation only discloses the external Discord purchase boundary. `historical-family-buttons` remains blocked: measurements contain no reference crops, region boxes, or wrap points, and no legitimate SKU price/icon observations. |
| Preview selects | ⚠️ | Accessible select-only combobox/listbox with trigger focus, active-descendant navigation, identity-decorated selections, min/max-aware options, viewport-clamped menus, and stale-candidate pruning without dispatch. Uncalibrated fallback: singles commit on choice; multis toggle until Enter, trigger-close, or outside click and commit only within min/max; Escape/blur cancel. Message actions use the existing callback; modal values stay draft until submit and required clears validate locally. `tests/fixtures/preview/states.json` marks `select-commit`/`select-cancel` blocked; `historical-select-popup` has no measured popup regions in `measurements.json`. Search/Apply behavior and per-variant reference commit parity are not claimed. |
| Preview modals | ⚠️ | Real application identity/disclosure, bounded scrollable dialog, Labels and legacy inputs, Text Display, all select types, radio/checkbox groups, checkbox, and real File Upload; local field-associated validation and focus/inert behavior lead to the authoritative Python callback. Upload policy is 10 MiB/file and 25 MiB/action aggregate. Exact geometry and validation/select traces remain uncertified: `historical-modal-window` is non-comparable because Discord navigation remains in the crop, `historical-family-modals` has no region boxes/spacing/wrap points, and `states.json` blocks modal validation/select traces pending authorized client/app/assets and interaction traces. |
| Preview Components V2 | ⚠️ | Sections/accessories, Text Displays, container accents/separators, spoiler reveal and authorized offline media render with an intrinsic responsive gallery fallback. Discord gallery geometry is not certified; `historical-family-v2` remains blocked pending authorized 1–10-item mixed-aspect captures with measured wide/narrow crop and region coordinates. |
| Preview responsive and lifecycle verification | ⚠️ | Browser checks cover 320/360/420/640/960/1280 px message profiles at 700/900 px heights, long mixed-direction text and zoom, and delayed asset delivery during one viewer's revocation while another viewer stays authorized. These prove local behavior, not Discord geometry or human screen-reader usability. The private reference comparison and human modal/select/reaction/poll screen-reader smoke remain release blockers; no certified parity is claimed. |
| Preview presentation limits | ✅ | 16 pages, 128 MiB retained media, bounded multipart/raster decoding, 30-second capture deadline; see the [preview guide](guides/preview.md) |
| Preview fidelity boundary | ⚠️ | Packaged, pinned licensed Noto text/script/emoji faces substitute for proprietary Discord fonts; uncovered glyphs and platform-dependent codecs may fall back. Visible keyboard focus intentionally differs. Offline assets are explicit, premium/provider service actions stay external, and arbitrary captures remain uncalibrated. Browser playback is interactive; managed capture uses deterministic `media_time`. |
| Bot restart / persistent views | ✅ | `env.restart_bot()` hydrates current guild state and reattaches persistent views; entitlement state/schedules survive, without automatic entitlement event replay |
| Members (join/leave, kick/ban/unban, nick, roles, timeout) | ✅ | Hierarchy enforced; `fetch_members` listing; `bulk_ban`, `prune_members`/`estimate_pruned_members` (roleless = inactive); the bot's own nick (`guild.me.edit`) |
| Roles (create/edit/delete) | ✅ | `Guild.fetch_role`; reorder via `Guild.edit_role_positions` |
| Guilds (create/edit/delete) | ✅ | `Client.create_guild`, `Guild.edit`, `Guild.delete` (owner-only); `GUILD_UPDATE` audit; `Guild.leave`, `Client.fetch_guilds`, `ClientUser.edit` (bot username); `Guild.vanity_invite` (settable via `guild.set_vanity_url`) |
| Channels (create/edit/delete, overwrites) | ✅ | Runtime create + list; text, voice, stage, category & forum kinds; reorder/move (`Channel.move`); announcement `TextChannel.follow` |
| Webhooks | ✅ | Create, execute (with per-message `username` override), fetch/edit/delete (by id or token), guild listing |
| Fault injection / HTTP request log | ✅ | `env.inject_error`, structured `env.http_requests` records for every attempt, including faulted and unknown-route requests |
| Audit logs | ✅ | Recorded for ban/kick/role/member/channel/event actions; `guild.audit_logs()`, filtering |
| Polls | ✅ | Message-level poll object; `actor.vote`, expiry (route + `advance_time`), vote events |
| Scheduled events | ✅ | CRUD + subscribe/unsubscribe; auto status transitions via `advance_time` |
| Voice state | ✅ | State only — never audio; join/leave/move/mute, request-to-speak, `VOICE_STATE_UPDATE`; `Guild.fetch_voice_state` |
| Stage instances | ✅ | `StageChannel.create_instance`/`fetch_instance`, `StageInstance.edit`/`delete`, gateway events |
| Invites | ✅ | Create/list/fetch/delete, gateway events |
| Emojis & stickers | ✅ | Guild expression CRUD, update events; application-owned emojis (`Client.create_application_emoji`, `fetch_application_emojis`) |
| Auto-moderation | ✅ | Rule CRUD + keyword & mention-spam execution (block/alert) on send |
| Time control (`advance_time`) | ✅ | Shared UTC/monotonic clock, chronological resource deadlines and bot timers, SDK expiry/sleep/calendar loops in selected contexts; allocation does not advance time; one live Env per loop |
| Rate limit simulation | ❌ | Deliberate: tests stay fast; use `inject_error` for 429 paths |
| Sharding simulation | ✅ | `AutoShardedClient`/`AutoShardedBot`; full and partial shard sets, Discord guild routing, per-shard READY/chunking/presence/latency/controls; no multi-process IPC |

### Behavior evidence

The independently authored [behavior ledger](https://github.com/SilentHacks/simcord/blob/master/tests/fixtures/behavior/ledger.json)
contains **184 unique live scenarios**, recorded on **2026-11-06** with two bots
in an isolated Community server. The main 181-row recording completed in about
13 minutes; a six-row supplementary run adds populated-pin checks and explicit
permission-endpoint cross-checks. Three overlapping control rows agree. No code
or fixtures were copied from another project's truth table.

Each row preserves its immutable scenario, symbolic raw request, status, Discord
code, new-validator error tree and relevant response/state effects. Capture dates,
API version, gateway capabilities and separate capture-run membership live in
provenance. Replays rebuild each actual capture run independently; combining
supplementary recordings must not pool their state. Real IDs, absolute timestamps,
tokens and raw private receipts do not belong in published scenario rows.

**Comparison boundary:** this is semantic behavior evidence, not complete wire or
client certification. The replay compares the request and modeled message/channel
fields and effects; absent optional arrays and neutral defaults compare equally,
overwrite order is immaterial, and volatile server revisions, scanner metadata,
poll tallies/expiry and unmodeled cosmetic fields are not scored. Original durable
receipts remain private. Serializer/parser conformance checks cover their own
contracts separately. Bot recordings do not establish simulated-human policies.

The CI ratchet requires every row to replay. New mismatches, changed mismatch
signatures, changed requests and now-passing stale gaps fail. The only deliberate
[known gaps](https://github.com/SilentHacks/simcord/blob/master/tests/fixtures/behavior/known_gaps.json)
are:

- `file.text`: Discord sniffs `text/plain; charset=utf-8`; the offline CDN infers
  `text/plain` from the filename. Bytes, filename, size and permission behavior agree.
- `hidden.guild-list`: REST filtering had not yet been enforced on the capture
  date. SimCord deliberately implements the documented mandatory filtering without
  an opt-out. Capability-32768 gateway captures already confirmed obfuscation.

The generated Evidence column below distinguishes actual live observations from
the official documentation used to derive each hypothesis. Status/code describe
the live observation; the two exceptions above are not passing parity claims.

<!-- behavior:begin (generated — do not edit by hand) -->

| Scenario | Evidence | Status/code | Single-cause control |
| --- | --- | --- | --- |
| `send.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `send.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `send.text` |
| `send.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `send.text` |
| `send.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `send.no-connect` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `embed.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `embed.no-embed` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `embed.text` |
| `embed.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `embed.text` |
| `embed.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `embed.text` |
| `file.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `file.no-attach` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `file.text` |
| `file.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `file.text` |
| `file.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `file.text` |
| `poll.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 200/0 | `—` |
| `poll.no-polls` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 403/50013 | `poll.text` |
| `poll.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `poll.text` |
| `reply.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `reply.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/160002 | `reply.text` |
| `reply.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `reply.text` |
| `reply.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `reply.text` |
| `history.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `history.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `history.text` |
| `history.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `history.text` |
| `fetch.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `fetch.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `fetch.text` |
| `fetch.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `fetch.text` |
| `pins.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `pins.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `pins.text` |
| `pins.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `pins.text` |
| `pin.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `pin.no-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `pin.text` |
| `pin.no-manage` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `pin.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `pin.text` |
| `pin.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `pin.text` |
| `unpin.text` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `unpin.no-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `unpin.text` |
| `crosspost.own` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `crosspost.other` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `crosspost.own-no-manage` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `crosspost.other-no-manage` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `crosspost.other` |
| `crosspost.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `crosspost.own` |
| `crosspost.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `crosspost.own` |
| `reaction.new` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `reaction.no-add` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `reaction.new` |
| `reaction.existing-no-add` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `reaction.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `reaction.new` |
| `reaction.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `reaction.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `reaction.new` |
| `reaction.remove-own` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `reaction.remove-own-no-add` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.no-parent-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.no-thread-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.send` |
| `thread.no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.send` |
| `thread.archived-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.locked-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.archived-send` |
| `thread.locked-manager-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.archived-react` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `thread.active-react` |
| `thread.active-react` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.locked-react` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `thread.active-locked-react` |
| `thread.archived-edit` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `thread.active-edit` |
| `thread.active-edit` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.archived-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `—` |
| `thread.locked-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `—` |
| `thread.unarchive` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.locked-unarchive` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/160005 | `thread.unarchive` |
| `thread.manager-unarchive` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.owner-rename` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.other-rename` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.manager-rename` |
| `thread.manager-rename` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.owner-lock` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `thread.manager-lock` |
| `thread.manager-lock` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.leave` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.leave-no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.join` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.join-no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.join` |
| `news-thread.send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `news-thread.no-parent-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `news-thread.no-thread-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `news-thread.send` |
| `news-thread.archived-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `news-thread.locked-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `news-thread.archived-send` |
| `news-thread.leave-no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `voice.send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `voice.no-connect-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `voice.send` |
| `voice.no-view-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `voice.send` |
| `voice.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `voice.send` |
| `voice.pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50019 | `—` |
| `voice.no-connect-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `voice.pin` |
| `voice.no-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50019 | `voice.pin` |
| `voice.history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `voice.no-connect-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `voice.history` |
| `stage.send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `stage.no-connect-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `stage.send` |
| `stage.no-view-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `stage.send` |
| `stage.pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50019 | `—` |
| `stage.no-connect-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `stage.pin` |
| `stage.no-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50019 | `stage.pin` |
| `nickname.valid` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 200/0 | `—` |
| `nickname.max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 200/0 | `—` |
| `nickname.over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 400/50035 | `nickname.max` |
| `nickname.clear` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 200/0 | `—` |
| `thread.name-max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `thread.name-over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/50035 | `thread.name-max` |
| `thread.name-empty` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `thread.name-max` |
| `topic.max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `topic.over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/50035 | `topic.max` |
| `topic.clear` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `forum.topic-max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `forum.topic-over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/50035 | `forum.topic-max` |
| `slowmode.zero` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `slowmode.max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `slowmode.over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/50035 | `slowmode.max` |
| `slowmode.negative` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/50035 | `slowmode.zero` |
| `poll.duration-max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 200/0 | `—` |
| `poll.duration-over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 400/50035 | `poll.duration-max` |
| `poll.duration-zero` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 400/50035 | `poll.text` |
| `poll.one-answer` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 200/0 | `poll.text` |
| `timeout.short` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 200/0 | `—` |
| `timeout.over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 400/50035 | `timeout.short` |
| `timeout.past` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 200/0 | `—` |
| `timeout.admin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/guild#modify-guild-member) | 403/50013 | `timeout.short` |
| `target.category` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/50008 | `send.text` |
| `target.forum` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/50008 | `send.text` |
| `message.empty` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 400/50006 | `send.text` |
| `message.empty-content` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 400/50006 | `send.text` |
| `message.sticker-unknown` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 400/50081 | `send.text` |
| `message.content-max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `message.content-over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 400/50035 | `message.content-max` |
| `mentions.none` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `mentions.user` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `mentions.role-blocked` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `mentions.role-allowed` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `mentions.invalid-overlap` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 400/50035 | `mentions.user` |
| `tts.allowed` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `tts.no-permission` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `tts.allowed` |
| `flags.ephemeral-normal` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `flags.suppress-embeds` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `flags.silent` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `forum.post` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 201/0 | `—` |
| `forum.require-tag-missing` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 400/40067 | `forum.post` |
| `forum.require-tag-present` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 201/0 | `—` |
| `forum.no-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `forum.post` |
| `forum.no-thread-create` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 201/0 | `—` |
| `command.store` | Live (2026-11-06); [docs](https://docs.discord.com/developers/interactions/application-commands) | 201/0 | `—` |
| `command.options-25` | Live (2026-11-06); [docs](https://docs.discord.com/developers/interactions/application-commands) | 201/0 | `—` |
| `command.options-26` | Live (2026-11-06); [docs](https://docs.discord.com/developers/interactions/application-commands) | 400/50035 | `command.options-25` |
| `command.required-first` | Live (2026-11-06); [docs](https://docs.discord.com/developers/interactions/application-commands) | 201/0 | `—` |
| `command.required-after` | Live (2026-11-06); [docs](https://docs.discord.com/developers/interactions/application-commands) | 400/50035 | `command.required-first` |
| `hidden.fetch` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 403/50001 | `hidden.visible` |
| `hidden.visible` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `hidden.guild-list` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 200/0 | `—` |
| `hidden.gateway-create` | Live (2026-11-06); [docs](https://docs.discord.com/developers/events/gateway-events) | 201/0 | `—` |
| `hidden.gateway-update` | Live (2026-11-06); [docs](https://docs.discord.com/developers/events/gateway-events) | 200/0 | `—` |
| `hidden.creation-self-hide` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/channel) | 201/0 | `—` |
| `emoji.external-allowed` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `emoji.external-denied` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `emoji.external-allowed` |
| `poll.question-max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 200/0 | `—` |
| `poll.question-over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 400/50035 | `poll.question-max` |
| `poll.answer-max` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 200/0 | `—` |
| `poll.answer-over` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/poll) | 400/50035 | `poll.answer-max` |
| `crosspost.no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `crosspost.own` |
| `thread.active-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.archived-pin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `thread.active-pin` |
| `thread.active-unpin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.archived-unpin` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `thread.active-unpin` |
| `thread.active-bulk-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.archived-bulk-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 400/50083 | `thread.active-bulk-delete` |
| `thread.owner-archive` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.other-archive` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.manager-archive` |
| `thread.manager-archive` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.rename-no-thread-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.owner-rename` |
| `thread.unarchive-no-thread-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `thread.unarchive` |
| `thread.active-locked-send` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50001 | `—` |
| `thread.active-locked-react` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 204/0 | `—` |
| `thread.owner-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `thread.owner-manager-delete` |
| `thread.owner-manager-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `thread.other-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 403/50013 | `thread.manager-delete` |
| `thread.manager-delete` | Live (2026-11-06); [docs](https://docs.discord.com/developers/topics/permissions) | 200/0 | `—` |
| `hidden.gateway-voice` | Live (2026-11-06); [docs](https://docs.discord.com/developers/events/gateway-events) | 201/0 | `—` |
| `hidden.gateway-forum` | Live (2026-11-06); [docs](https://docs.discord.com/developers/events/gateway-events) | 201/0 | `—` |
| `hidden.gateway-category` | Live (2026-11-06); [docs](https://docs.discord.com/developers/events/gateway-events) | 201/0 | `—` |
| `pins.populated` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `—` |
| `pins.populated-no-history` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 200/0 | `pins.populated` |
| `pins.populated-no-view` | Live (2026-11-06); [docs](https://docs.discord.com/developers/resources/message) | 403/50001 | `pins.populated` |

<!-- behavior:end -->

#### Replaying or refreshing behavior evidence

Routine CI is offline:

```bash
python -m scripts.behavior_reference replay \
  --ledger tests/fixtures/behavior/ledger.json \
  --known-gaps tests/fixtures/behavior/known_gaps.json
python -m scripts.behavior_reference table \
  --ledger tests/fixtures/behavior/ledger.json
```

Live recording is manual, authorized work, never a CI prerequisite. Stop other
processes using the subject token first and restore them afterwards. Credentials
are environment-only (`BEHAVIOR_ADMIN_TOKEN`, `BEHAVIOR_SUBJECT_TOKEN` and
`BEHAVIOR_SUBJECT_ID`); never put tokens in arguments, logs or committed files.
The recorder refuses any guild other than the explicitly approved lab, verifies
two distinct bot identities and administrator/hierarchy access, paces requests,
and only mutates positively receipt-owned resources. Names alone are not ownership.
It never bulk-overwrites existing commands, edits managed roles or tests on humans.

Use distinct private directories for preflight and recording:

```bash
python -m scripts.behavior_reference preflight --directory /private/preflight
python -m scripts.behavior_reference record \
  --directory /private/behavior-run --ledger /private/behavior-live.json
```

`--case` selects hand-written scenarios and automatically includes their controls.
`crosscheck` uses the same arguments and compares known explicit permission bits
with Discord's member-permission endpoint (`implicit=false`); raw oracle receipts
remain private. The committed supplement spot-checks four such configurations,
not every ledger row.

Interrupted or ambiguous writes are never retried as if they had failed. Keep the
private journal, resolve any uncertain receipt, then run `cleanup` with the original
directory. Successful parent deletion proves deletion of positively correlated
children; unrelated objects are never inferred to be owned. Cleanup restores the
subject's original nickname/timeout and removes only owned resources.

```bash
python -m scripts.behavior_reference cleanup --directory /private/behavior-run
python -m scripts.behavior_reference publish \
  --directory /private/behavior-run --ledger /private/recovered-live.json
```

Publication is offline and requires receipt-proven cleanup plus unchanged scenario
snapshots. `publish --supplement /private/supplement.json` merges genuine captures
only when overlapping complete rows agree. Do not rewrite historical observations
or weaken error trees to make a replay pass; recapture changed scenarios instead.


### Preview feature and evidence matrix

The [fixture ledger](https://github.com/SilentHacks/simcord/blob/master/tests/fixtures/preview/coverage.json) defines 94 rows across
14 families (`profile`, `identity`, `message_context`, `reactions`, `polls`,
`stickers_system`, `markdown`, `attachments_media`, `embeds`, `buttons`, `selects`,
`modals`, `v2`, `access_lifecycle`) and registers 76 historical images by hash.
Its `referenceStatus`, `implementationStatus` and `comparisonStatus` are separate,
per-row facts; 18 references are blocked and 13 comparisons are not comparable.
See the [profile, transition and measurement recipes](https://github.com/SilentHacks/simcord/tree/master/tests/fixtures/preview)
and the [private comparison workflow](guides/preview.md#one-fixture-a-family-or-the-private-batch).

| Evidence state | Meaning | Current release claim |
| --- | --- | --- |
| Ready | Current local generation has finished rendering, fonts and authorized media or explicit diagnostics | Exercised by the local capture/browser checks; does not prove completeness or pixel parity |
| Complete | All in-scope information for that capture is present, subject to diagnostics and resource limits | Per-capture report only, never inferred from a saved PNG |
| Calibrated | Exact normalized fixture, profile, provenance-checked reference and reviewed comparison | **Not certified** without an authorized private pack, missing geometry/state traces and human screen-reader smoke |

Feature support above is implementation support, not a promise of matching every
Discord client state. Missing references, inaccessible service surfaces, resource
ceilings and intentional focus/font substitutions remain visible rather than
converted into a passing global pixel score.

## Implemented routes

This section is generated from the route table (`python -m simcord.parity`),
so it is exact by construction.

<!-- routes:begin (generated — do not edit by hand) -->

**144 routes implemented**, grouped by resource below. Anything else fails loudly with `RouteNotImplemented`.

???+ success "Applications · 20 route(s)"

    | Method | Route |
    | --- | --- |
    | `GET` | `/applications/{application_id}/commands` |
    | `PUT` | `/applications/{application_id}/commands` |
    | `GET` | `/applications/{application_id}/emojis` |
    | `POST` | `/applications/{application_id}/emojis` |
    | `GET` | `/applications/{application_id}/emojis/{emoji_id}` |
    | `PATCH` | `/applications/{application_id}/emojis/{emoji_id}` |
    | `DELETE` | `/applications/{application_id}/emojis/{emoji_id}` |
    | `GET` | `/applications/{application_id}/entitlements` |
    | `POST` | `/applications/{application_id}/entitlements` |
    | `GET` | `/applications/{application_id}/entitlements/{entitlement_id}` |
    | `DELETE` | `/applications/{application_id}/entitlements/{entitlement_id}` |
    | `POST` | `/applications/{application_id}/entitlements/{entitlement_id}/consume` |
    | `GET` | `/applications/{application_id}/guilds/{guild_id}/commands` |
    | `POST` | `/applications/{application_id}/guilds/{guild_id}/commands` |
    | `PUT` | `/applications/{application_id}/guilds/{guild_id}/commands` |
    | `GET` | `/applications/{application_id}/guilds/{guild_id}/commands/{command_id}` |
    | `PATCH` | `/applications/{application_id}/guilds/{guild_id}/commands/{command_id}` |
    | `DELETE` | `/applications/{application_id}/guilds/{guild_id}/commands/{command_id}` |
    | `GET` | `/applications/{application_id}/guilds/{guild_id}/commands/{command_id}/permissions` |
    | `GET` | `/applications/{application_id}/skus` |

???+ success "Channels · 40 route(s)"

    | Method | Route |
    | --- | --- |
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

???+ success "Guilds · 57 route(s)"

    | Method | Route |
    | --- | --- |
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

???+ success "Interactions · 1 route(s)"

    | Method | Route |
    | --- | --- |
    | `POST` | `/interactions/{interaction_id}/{token}/callback` |

???+ success "Invites · 2 route(s)"

    | Method | Route |
    | --- | --- |
    | `GET` | `/invites/{code}` |
    | `DELETE` | `/invites/{code}` |

???+ success "OAuth2 · 1 route(s)"

    | Method | Route |
    | --- | --- |
    | `GET` | `/oauth2/applications/@me` |

???+ success "Stage instances · 4 route(s)"

    | Method | Route |
    | --- | --- |
    | `POST` | `/stage-instances` |
    | `GET` | `/stage-instances/{channel_id}` |
    | `PATCH` | `/stage-instances/{channel_id}` |
    | `DELETE` | `/stage-instances/{channel_id}` |

???+ success "Users · 6 route(s)"

    | Method | Route |
    | --- | --- |
    | `GET` | `/users/@me` |
    | `PATCH` | `/users/@me` |
    | `POST` | `/users/@me/channels` |
    | `GET` | `/users/@me/guilds` |
    | `DELETE` | `/users/@me/guilds/{guild_id}` |
    | `GET` | `/users/{user_id}` |

???+ success "Webhooks · 13 route(s)"

    | Method | Route |
    | --- | --- |
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

This is the current intentionally incomplete gap surface: omissions are deliberate
and demand-driven, not unfinished work. Most are niche (monetization, soundboard,
guild templates) or have a working common path already — individual application
command CRUD is unlisted because `CommandTree.sync` (the bulk overwrite) is fully
modelled. Others (integrations, welcome screen, widget, onboarding) are omitted on
purpose: simcord has no settable state behind them, so a handler returning a
constant empty value would be a *silent fake* — exactly what this project refuses
to ship. They fail loudly until there is demand to model them properly. Open an
issue if your bot needs one.

<!-- gaps:begin (generated — do not edit by hand) -->

**43 discord.py REST routes are not yet implemented**, grouped by resource below; calling one fails loudly with `RouteNotImplemented` (path parameters shown as `{}`). Open an issue if your bot needs one.

???+ warning "Applications · 7 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `PATCH /applications/@me` | `edit_application_info` |
    | `POST /applications/{}/commands` | `upsert_global_command` |
    | `GET /applications/{}/commands/{}` | `get_global_command` |
    | `PATCH /applications/{}/commands/{}` | `edit_global_command` |
    | `DELETE /applications/{}/commands/{}` | `delete_global_command` |
    | `GET /applications/{}/guilds/{}/commands/permissions` | `get_guild_application_command_permissions` |
    | `PUT /applications/{}/guilds/{}/commands/{}/permissions` | `edit_application_command_permissions` |

???+ warning "Authentication · 1 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `POST /auth/logout` | `logout` |

???+ warning "Channels · 2 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `POST /channels/{}/send-soundboard-sound` | `send_soundboard_sound` |
    | `PUT /channels/{}/voice-status` | `edit_voice_channel_status` |

???+ warning "Guilds · 27 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `GET /guilds/templates/{}` | `get_template` |
    | `POST /guilds/templates/{}` | `create_from_template` |
    | `PUT /guilds/{}/incident-actions` | `edit_incident_actions` |
    | `GET /guilds/{}/integrations` | `get_all_integrations` |
    | `POST /guilds/{}/integrations` | `create_integration` |
    | `PATCH /guilds/{}/integrations/{}` | `edit_integration` |
    | `DELETE /guilds/{}/integrations/{}` | `delete_integration` |
    | `POST /guilds/{}/integrations/{}/sync` | `sync_integration` |
    | `POST /guilds/{}/mfa` | `edit_guild_mfa_level` |
    | `GET /guilds/{}/onboarding` | `get_guild_onboarding` |
    | `GET /guilds/{}/preview` | `get_guild_preview` |
    | `GET /guilds/{}/roles/member-counts` | `get_role_member_counts` |
    | `GET /guilds/{}/soundboard-sounds` | `get_soundboard_sounds` |
    | `POST /guilds/{}/soundboard-sounds` | `create_soundboard_sound` |
    | `GET /guilds/{}/soundboard-sounds/{}` | `get_soundboard_sound` |
    | `PATCH /guilds/{}/soundboard-sounds/{}` | `edit_soundboard_sound` |
    | `DELETE /guilds/{}/soundboard-sounds/{}` | `delete_soundboard_sound` |
    | `GET /guilds/{}/templates` | `guild_templates` |
    | `POST /guilds/{}/templates` | `create_template` |
    | `PUT /guilds/{}/templates/{}` | `sync_template` |
    | `PATCH /guilds/{}/templates/{}` | `edit_template` |
    | `DELETE /guilds/{}/templates/{}` | `delete_template` |
    | `PATCH /guilds/{}/vanity-url` | `change_vanity_code` |
    | `GET /guilds/{}/welcome-screen` | `get_welcome_screen` |
    | `PATCH /guilds/{}/welcome-screen` | `edit_welcome_screen` |
    | `PATCH /guilds/{}/widget` | `edit_widget` |
    | `GET /guilds/{}/widget.json` | `get_widget` |

???+ warning "Monetization · 2 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `GET /skus/{}/subscriptions` | `list_sku_subscriptions` |
    | `GET /skus/{}/subscriptions/{}` | `get_sku_subscription` |

???+ warning "Soundboard · 1 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `GET /soundboard-default-sounds` | `get_soundboard_default_sounds` |

???+ warning "Sticker packs · 2 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `GET /sticker-packs` | `list_premium_sticker_packs` |
    | `GET /sticker-packs/{}` | `get_sticker_pack` |

???+ warning "Stickers · 1 route(s)"

    | Route | discord.py `HTTPClient` method |
    | --- | --- |
    | `GET /stickers/{}` | `get_sticker` |

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
