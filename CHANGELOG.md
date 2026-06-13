# Changelog

This changelog is generated with [towncrier](https://towncrier.readthedocs.io/).

<!-- towncrier release notes start -->

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
