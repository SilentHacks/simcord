# Changelog

This changelog is generated with [towncrier](https://towncrier.readthedocs.io/).

<!-- towncrier release notes start -->

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
