# Changelog

This changelog is generated with [towncrier](https://towncrier.readthedocs.io/).

<!-- towncrier release notes start -->

## 1.2.0 (2026-07-23)

### Features

- Support discord.py `AutoShardedClient` and `AutoShardedBot` with per-shard readiness, Discord-compatible event routing, partial shard workers, chunking, shard controls, and targeted test guild creation via `Env.create_guild(shard_id=...)`.


## 1.1.0 (2026-06-26)

### Features

- Simulate bot, system and webhook message sources. `env.create_user` gained `bot`, `system`, `global_name`, `discriminator` and `public_flags` keywords, so a simulated account posts with `message.author.bot` set the way an application does. `GuildHandle.create_webhook(channel)` returns a new `WebhookHandle` whose `send()` posts a message with `message.webhook_id` set (the distinct webhook source). `env.create_guild` also gained `owner`, `description`, `verification_level`, `notifications`, `content_filter`, `preferred_locale` and `afk_timeout` keywords for seeding guild settings up front. ([#18](https://github.com/SilentHacks/simcord/issues/18))


## 1.0.1 (2026-06-18)

### Documentation

- Expanded the bundled `examples/` into a realistic mixed-feature bot (prefix command, cooldown, permission-gated slash, modal, button confirm flow and a persistent select menu) with a test per feature, and added context-menu, deferred-followup and embed-assertion recipes to the cookbook.


## 1.0.0 (2026-06-17)

### Features

- Added a performance benchmark suite and a CI guard for the offline-speed value proposition. A generous absolute budget catches catastrophic regressions (a real network call, an accidental quadratic) and a same-run scaling ratio proves message-send latency stays constant in channel size, without the flakiness of baseline percentage gating. See the new [Performance](https://simcord.readthedocs.io/en/latest/performance/) page.
- Closed the honesty layer's remaining silent-drop blind spots and added property-based fuzzing to prove it stays closed. Message create, webhook execute, interaction responses and bulk message delete now route their bodies through `ctx.fields` like every edit, so an unmodelled key (e.g. `sticker_ids`) fails loudly with `UnsupportedField` instead of vanishing. As part of this, `Webhook.send(username=...)` is now modelled (the message posts under that per-message display name) rather than silently ignored; `avatar_url` is accepted (simcord models no avatars); and creating a forum thread via webhook (`thread_name`/`applied_tags`) is rejected with a reason, since it is unmodelled offline. A Hypothesis sweep proves that across every route whose body flows through `ctx.fields`/`list_fields`, an unrecognised request field always raises and a recognised one never does, and a drift guard fails loudly if a new write route is neither honesty-vetted nor explicitly exempted with a reason.


## 0.11.0 (2026-06-16)

### Features

- Added the final common-bot route sweep as the surface settles heading into 1.0: `Guild.delete` (owner-only; the discord.py wrapper is deprecated but the route is kept for parity), `Member.fetch_voice` (read a member's voice state), `TextChannel.follow` (follow an announcement channel into a destination webhook), and `Guild.vanity_invite` (with `guild.set_vanity_url(code)` to populate it in the world builder). Routes whose result would have to be a constant empty value — integrations, welcome screen, widget, onboarding — are deliberately left as loud `RouteNotImplemented` gaps rather than faked; the parity matrix documents this as the frozen gap surface heading into 1.0.

### Bug fixes

- Closed the last silent-fake gaps in the request layer: the bulk-ban, prune, channel-permission-overwrite, and voice-state handlers read the raw body directly and so silently dropped unrecognised fields. They now route through the same `RequestContext.fields` honesty check as every other handler, raising `UnsupportedField` on an unmodelled key. Role-filtered prune (`include_roles`) is now rejected loudly rather than pruning a different cohort than asked, and `compute_prune_count=false` correctly omits the count.

### Miscellaneous

- Marked SimCord's public API as stable ahead of 1.0: the package now declares `Development Status :: 5 - Production/Stable`, the README and docs landing page describe the semantic-versioning commitment that takes effect from the upcoming 1.0 (see Stability & versioning, which now also documents the supported discord.py range and the deprecation policy), and the test-coverage ratchet is raised to 95%.


## 0.10.0 (2026-06-15)

### Features

- Create handlers now reject unrecognised request fields with `UnsupportedField` instead of silently dropping them, extending field-level honest parity from edits to creates (channels, roles, threads, webhooks, invites, emojis, stickers, scheduled events, stage instances and auto-moderation rules). The same honesty now also covers the JSON-array bodies of the bulk reorder endpoints, so an unknown per-item key fails loudly there too. Channel creation honours an explicit `position` rather than always appending.
- Implemented several common-bot REST routes: `Guild.fetch_role`, role and channel reordering (`Guild.edit_role_positions`, `Channel.move`), editing the bot's own nickname (`guild.me.edit`), `Guild.leave`, `Client.fetch_guilds`, and bot username edits (`ClientUser.edit`). Role reordering enforces hierarchy — moving a role to or above the bot's own top role raises `Forbidden`, as on real Discord.

### Bug fixes

- Fixed sticker creation via the REST route: multipart scalar form fields (`name`/`description`/`tags`) are now reconstructed into the request body. Also fixed `create_role` to honour discord.py 2.7's gradient `colors` payload (previously the colour was silently dropped on create).

### Documentation

- Added a "Stability & versioning" reference page documenting which parts of the API are public and semver-covered versus deliberately internal.

### Miscellaneous

- The parity matrix now separates deliberately out-of-scope routes (actions a bot account cannot perform, e.g. creating group DMs) from not-yet-implemented gaps, with the new section generated and drift-guarded like the others.


## 0.9.0 (2026-06-15)

### Features

- Make parity honesty machine-checked. Edit handlers now reject unrecognised request fields loudly with the new public `simcord.UnsupportedField` instead of silently dropping them (and wire through the previously-dropped voice `bitrate`/`user_limit`/`rtc_region`, channel `parent_id`, and guild rules/public-updates channel pointers); serializer payloads are conformance-tested against discord.py's real model constructors; and the parity matrix's "not yet implemented" list is now derived from `discord.http.HTTPClient` and enforced in sync, so a discord.py upgrade that adds a route fails the build until triaged. **Behaviour change:** an edit that sends a field SimCord does not model now raises loudly rather than appearing to succeed, so a test that previously passed while exercising an unmodelled field (e.g. `Guild.edit(icon=...)`, forum channel settings, member flags) will now fail with `UnsupportedField` — surfacing a real parity gap. Catch `simcord.UnsupportedField` (or open a parity-gap issue) if your bot relies on one.


## 0.8.1 (2026-06-14)

### Miscellaneous

- Split the monolithic `Backend` class into a shared `BackendBase` kernel plus coupling-aligned subsystem mixins under `simcord.backend.ops`. Pure structural refactor — the `Backend` public surface and all behaviour are unchanged.


## 0.8.0 (2026-06-14)

### Features

- Support application-owned emojis (`Client.create_application_emoji`, `fetch_application_emojis`, `fetch_application_emoji`, edit/delete) and stage instances (`StageChannel.create_instance`/`fetch_instance`, `StageInstance.edit`/`delete`) with `STAGE_INSTANCE_*` gateway events.
- Add the high-frequency moderation and announcement calls: `Guild.bulk_ban`, `Guild.prune_members`/`Guild.estimate_pruned_members` (roleless members modelled as inactive), and `Message.publish` for announcement (news) channels, with a new `guild.create_news_channel(...)` builder.
- Complete the thread surface: `Thread.join`/`leave`, `add_user`/`remove_user`, `fetch_members`/`fetch_member`, `Guild.active_threads`, `TextChannel.archived_threads`, and `Thread.edit(archived=..., locked=..., auto_archive_duration=..., invitable=...)` — which previously returned `200` but silently dropped the change.

### Bug fixes

- Harden the new parity surface: a member who is kicked/banned/pruned now also leaves every thread (so `member_count` and thread-member listings stay correct), `bulk_ban` returns its `banned`/`failed` split instead of a spurious `Forbidden` when nobody could be banned (and rejects an empty `user_ids`), `estimate_pruned_members` enforces `kick_members`, deleting a stage channel closes its live stage instance, opening a second stage instance on a channel is rejected, re-publishing an already-crossposted message raises (`40033`), the thread-member endpoints fail with `50024` on non-thread channels, and an application-emoji edit that omits `name` no longer errors.

### Miscellaneous

- Drop the "multiple bots in one Env" non-feature from the docs (it is not a planned direction), and ratchet the coverage floor to 84%.


## 0.7.0 (2026-06-13)

### Features

- Added `Guild.edit()` (`PATCH /guilds/{id}`) — name, description, verification level, default notifications, explicit-content filter, AFK channel/timeout, system channel and preferred locale — and runtime guild creation `Client.create_guild()` (`POST /guilds`); guild edits record a `GUILD_UPDATE` audit entry.
- Added application command permission fetching: `AppCommand.fetch_permissions(guild)` reads per-guild overrides seeded by `GuildHandle.set_command_permissions(...)`, and returns `NotFound` when a command is unchanged from the guild default.
- Added reaction clearing: `Message.clear_reactions()` (`DELETE /channels/{id}/messages/{id}/reactions`, emits `MESSAGE_REACTION_REMOVE_ALL`) and `Message.clear_reaction(emoji)` (`DELETE .../reactions/{emoji}`, emits `MESSAGE_REACTION_REMOVE_EMOJI`).
- Added runtime channel management: bots can now `Guild.create_text_channel()` / `create_voice_channel()` / etc. (`POST /guilds/{id}/channels`) and `Guild.fetch_channels()` (`GET /guilds/{id}/channels`), reusing the same backend path as test-setup builders and recording a `CHANNEL_CREATE` audit entry.
- Added stage voice-state editing (`PATCH /guilds/{id}/voice-states/@me` and `/{user_id}`): `Member.request_to_speak()` and `Member.edit(suppress=...)` now work, emitting `VOICE_STATE_UPDATE`.
- Added the list-guild-members endpoint (`GET /guilds/{id}/members`) so `Guild.fetch_members()` pages through the full member list.
- Auto-moderation now evaluates mention-spam rules (`trigger_type` 5): a message whose user/role mention count exceeds `mention_total_limit` fires `AUTO_MODERATION_ACTION_EXECUTION` and is blocked, alongside the existing keyword triggers.
- Completed webhook management: fetch/edit/delete a webhook by id or token (`GET`/`PATCH`/`DELETE /webhooks/{id}` and the `/{token}` variants) and list a guild's webhooks (`GET /guilds/{id}/webhooks`), emitting `WEBHOOKS_UPDATE`.
- Forum channels are now usable end to end: `ForumChannel.create_thread(name=..., content=...)` creates a post (a public thread with its starter message) and `applied_tags`, and forum tags can be configured via `ForumChannel.edit(available_tags=...)`.
- Implemented bulk message deletion (`TextChannel.delete_messages` / `purge`): `POST /channels/{id}/messages/bulk-delete` removes 2–100 messages at once, emits a single `MESSAGE_DELETE_BULK`, and records a `MESSAGE_BULK_DELETE` audit-log entry.
- Scheduled events now auto-transition on the virtual clock: `advance_time()` moves an event from scheduled to active at its start time and to completed at its end time (emitting `GUILD_SCHEDULED_EVENT_UPDATE`), in addition to the existing manual status edits.


## 0.6.0 (2026-06-13)

### Features

- Audit logs are now recorded for the privileged actions the backend performs — bans, kicks, member nick/timeout/role updates, role and channel edits/deletes, member voice moves, and scheduled-event CRUD. Read them through `guild.audit_logs()` (with `user`/`action`/`before`/`after` filtering), the `on_audit_log_entry_create` event, or `env.guild.audit_log()`. Reasons passed via discord.py's `reason=` are captured (and now also stored on the ban record itself). ([#15](https://github.com/SilentHacks/simcord/issues/15))
- Polls are supported end to end: a bot sends `discord.Poll`, users cast and retract votes with `MemberActor.vote` / `remove_vote` (firing `MESSAGE_POLL_VOTE_ADD`/`REMOVE`), poll answer voters are fetchable, and polls finalize either via `Message.end_poll()` or when `env.advance_time` passes their deadline. ([#16](https://github.com/SilentHacks/simcord/issues/16))
- Guild scheduled events are supported: create/list/fetch/edit/delete plus subscriber listing, with `GUILD_SCHEDULED_EVENT_*` events. `MemberActor.subscribe_event` / `unsubscribe_event` mark interest, and `GuildHandle.create_scheduled_event` sets one up directly. Stage/voice/external entity types are validated. ([#17](https://github.com/SilentHacks/simcord/issues/17))
- Voice state is modeled (state only — never audio). Users join/leave/move with `MemberActor.join_voice`, `leave_voice` and `set_voice`; server mute/deaf and channel moves over `Member.edit`/`move_to` are reflected and audit-logged; all fire `VOICE_STATE_UPDATE` so `member.voice` and `on_voice_state_update` work. ([#18](https://github.com/SilentHacks/simcord/issues/18))
- Invites, custom emojis and stickers are supported: create/list/fetch/delete invites (`INVITE_CREATE`/`INVITE_DELETE`) and guild expression CRUD (`GUILD_EMOJIS_UPDATE`/`GUILD_STICKERS_UPDATE`), with builder helpers `GuildHandle.create_emoji` / `create_sticker` for setup. ([#19](https://github.com/SilentHacks/simcord/issues/19))
- Auto-moderation rules can be created, edited and deleted, and keyword rules are evaluated on message send: a matching `block_message` action drops the message and fires `AUTO_MODERATION_ACTION_EXECUTION`, honoring exempt roles and channels. ([#20](https://github.com/SilentHacks/simcord/issues/20))
- New channel kinds can be created from builders: voice, stage, category and forum channels (`GuildHandle.create_voice_channel`, `create_stage_channel`, `create_category`, `create_forum_channel`), and `create_text_channel` accepts a `category=`. ([#21](https://github.com/SilentHacks/simcord/issues/21))


## 0.5.0 (2026-06-13)

### Features

- Entity select menus are now fully supported. `MemberActor.select` accepts the handles a user could pick (`UserHandle`/`MemberActor`, `RoleHandle`, `ChannelHandle`) for user, role, channel and mentionable selects, building the resolved data so the bot's callback receives real `discord.Member`/`Role`/channel objects. Wrong handle kinds and out-of-range value counts fail with a `SetupError`. ([#12](https://github.com/SilentHacks/simcord/issues/12))
- Added `Env.restart_bot()` to simulate a bot restart while the virtual world persists. It detaches the current bot, attaches a freshly built one, and replays the existing guilds so the new client's cache repopulates — letting tests prove that persistent views (`bot.add_view` in `setup_hook`) re-attach to messages they never saw created. ([#13](https://github.com/SilentHacks/simcord/issues/13))

### Bug fixes

- Passing the wrong handle kind to a typed slash-command option (e.g. a `RoleHandle` to a `USER` option) now fails with a clear `SetupError` at the call site, instead of resolving into the wrong bucket and failing deep inside discord.py. ([#14](https://github.com/SilentHacks/simcord/issues/14))


## 0.4.0 (2026-06-13)

### Features

- Added assertion helpers — `assert_sent`, `assert_responded`, `assert_message`, `assert_error` and `assert_no_errors` — whose failure messages print what the bot actually did (the channel's recent history, the interaction result, the captured errors).
- `Env.create_guild` now accepts an optional `id=` so a guild can be pinned to a known id — e.g. to match a bot that syncs its commands to a hardcoded guild id, keeping `strict_sync` on.


## 0.3.0 (2026-06-13)

### Features

- Added a `@pytest.mark.simcord(...)` marker whose keyword arguments are forwarded to `simcord.run()` (e.g. `strict_sync=False`, `check_errors=False`), so the bundled `simcord_env` fixture can be configured per-test without writing a custom fixture.

### Bug fixes

- Fixed `member.context_menu(...)` failing to resolve context-menu commands whose names contain spaces (e.g. `"Report Member"`). The name is no longer split into a subcommand path — only slash commands nest.

### Documentation

- Documented the `@pytest.mark.simcord(...)` marker in the fixtures guide as the way to override `simcord.run` options per test.

### Miscellaneous

- Replaced remaining bare wire-protocol integer literals (channel types, message types, permission-overwrite target types, application-command types, modal component types) with `IntEnum` members, so the backend reads as the protocol does instead of scattering magic numbers.
- Typed the backend's permission-overwrite `type` field as the `OverwriteType` enum (and coerce it at the HTTP boundary), so overwrites carry a uniform enum value everywhere instead of a mix of enums and bare ints.


## 0.2.0 (2026-06-12)

### Features

- Gateway events outside the bot's declared intents are dropped, matching real Discord. Dropped events are recorded in the transcript so a mysteriously-quiet test can explain itself.
- Message content is censored without the `message_content` intent: `content`, `embeds`, `attachments`, `components` and `poll` are blanked on guild messages, with Discord's documented exemptions (DMs, bot-authored messages, messages mentioning the bot). Recorded as `CENSORED` in the transcript.
- `GUILD_CREATE` now inlines only the bot's own member. The rest arrive via authentic `GUILD_MEMBERS_CHUNK` responses, so `Guild.chunk()` and `Guild.query_members()` work as they would in production.
- `simcord.run(bot, approved_intents=...)` can simulate unapproved privileged intents raising `discord.PrivilegedIntentsRequired`, mirroring the Discord developer portal behaviour at connect time.
- New [Intents guide](https://simcord.readthedocs.io/en/latest/guides/intents/) covering event delivery, message content censoring, member chunking, and privileged intents.


## 0.1.0 (2026-06-12)

### Features

- Added `env.advance_time(seconds)`: fast-forward the virtual clock so view timeouts fire, cooldowns reset, and `asyncio.sleep` chains complete — instantly, with no real waiting. The event-loop clock and message timestamps advance together.
- Added `env.raise_errors()`, which re-raises everything the bot raised during the test (command handlers, app-command callbacks, event listeners) as an `ExceptionGroup` — a one-call way to assert the bot ran cleanly. Does nothing when no errors were captured.
- Failing tests now automatically include a transcript of everything that crossed the two seams — gateway events injected and REST calls the bot made, in order — attached by the pytest plugin. Also available programmatically as `env.transcript()`.
- Uninspected bot errors now fail the test: `simcord.run` re-raises captured errors as an `ExceptionGroup` at teardown unless the test read `env.errors` or called `env.raise_errors()`. Opt out with `simcord.run(bot, check_errors=False)`.

### Bug fixes

- A deferred component interaction (callback type 6) followed by `edit_original_response` now edits the clicked message in place, matching real Discord, instead of creating a new message. `@original` for a type-6 defer also resolves to the component's source message.
- An interaction is now marked acknowledged only after its callback is handled successfully, so a callback that 400s (e.g. an oversized embed) no longer consumes the interaction — a retried response gets through instead of a spurious `40060`.
- DMing a bot now opens the channel successfully and fails on send with `403 50007` (caught by `except discord.Forbidden`), matching Discord. Ephemeral messages no longer leak into the bot-facing channel history and pins endpoints, and webhook-authored messages now carry `webhook_id`.
- Serialized timestamps now come from the same virtual clock as snowflakes, so a message's `created_at` (derived from its id) and its `timestamp` agree instead of differing by months, and timestamps are deterministic across runs.
- Tightened server-side permission parity: the bot can no longer edit another user's message (`50005`), assign/edit roles at or above its own top role or grant permissions it lacks (`50013`), or delete the `@everyone` role. `mention_everyone` is only set when the author actually has the permission.
- Unimplemented routes now raise `RouteNotImplemented` directly rather than as a `discord.HTTPException`, so a bot's broad `except discord.HTTPException` can no longer silently swallow the "not implemented" signal. `history(around=...)` is now supported.
- `Env.settle()` now waits out `asyncio.sleep`-style pauses in handlers (cooldowns, backoff) instead of returning early, and only abandons tasks genuinely parked on a future. Assertions after an actor verb no longer race against a handler that paused before replying.

### Miscellaneous

- CI hardening: pinned the PyPI publish action to a commit SHA, added a test gate before release publishing, and set default `contents: read` permissions on the CI workflows.
- Centralised the Discord wire-protocol magic numbers (interaction, callback, option and component types) into `IntEnum`s in `simcord.enums`, replacing scattered bare integer constants in the actors, payload builders and interaction route.
- Dropped Python 3.10 support; the minimum is now Python 3.11.
- Every state mutation now lives on `Backend` paired with its own gateway emit (channel/overwrite edits, member field/role edits, role edits), and route handlers parse, permission-check via the new `ctx.require_channel_permissions`/`ctx.require_guild_permissions` helpers, then call a single backend method. This removes the copy-pasted permission preamble and the "mutate then remember to announce" pattern, so a write can't be announced inconsistently or forgotten as route coverage grows.
- Setup and not-implemented errors now attach supporting detail (available options, the parity-matrix pointer) as exception notes, keeping the primary message tight while still surfacing the context in tracebacks.
- The interaction response lifecycle is now a typed `Interaction` dataclass with a `ResponseKind` enum, replacing the untyped dict that was mutated across the route, actor, and result layers. `InteractionResult` no longer exposes the raw `.record` dict; use its typed properties (`acknowledged`, `deferred`, `ephemeral`, `modal`, `response`, `followups`) instead.
- The parity matrix's route inventory is now generated from the route table (`python -m simcord.parity`) and guarded by a sync test, so it is exact by construction. Also: `Backend` is no longer in `__all__` (still importable, documented as internal/unstable), and CI now enforces a coverage floor.
