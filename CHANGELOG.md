# Changelog

This changelog is generated with [towncrier](https://towncrier.readthedocs.io/).

<!-- towncrier release notes start -->

## 3.0.0 (2026-10-05)

### Features

- Discord message parity now retains sticker items and upload metadata, TTS, and allowed-mention notification policy. Pin/thread/member/channel service messages are typed and affect channel history; application-command responses retain command and target metadata. Static PNG stickers can be previewed safely; animated formats and blocked Discord visual-reference captures are explicitly incomplete rather than represented as calibrated parity. ([#8](https://github.com/SilentHacks/simcord/issues/8))
- Preview now parses message, Text Display and embed text with field-specific safe Markdown, resolves authorized mentions and custom emoji, formats all Discord timestamp styles from the published time profile, and highlights fenced code using a pinned offline grammar set. Unknown languages remain plain code; custom emoji without authorized supplied bytes remain unavailable. ([#9](https://github.com/SilentHacks/simcord/issues/9))
- Preview media manifests now distinguish retained source bytes from validated display readiness and publish EXIF-oriented intrinsic dimensions. Shared normalized variants are charged once; browser media loading is generation-aware and uses only local blob URLs. Images keep their intrinsic ratio, spoilers and text previews are accessible, and an Escape/arrow-key lightbox restores focus. File cards provide safe text preview and explicit download actions without embedding SVG or HTML. Multi-image attachment mosaics remain uncalibrated: the available `ref-11-attachments-idle` evidence covers one image only, so the preview retains a bounded vertical-list fallback rather than inventing 2–10-image geometry. ([#10](https://github.com/SilentHacks/simcord/issues/10))
- Preview media now keeps original downloads separate from validated browser display and deterministic captures. Animated raster and licensed expression-free Lottie stickers play interactively; screenshots select `media_time`. Optional PyAV support validates audio/video, reports codec transformations, and provides accessible native players. Source/display/capture ceilings, a bounded serial worker, per-session memory accounting, and live authorization checks apply. Audio/video availability depends on platform decoder/encoder support; unsupported codecs remain download-only. ([#11](https://github.com/SilentHacks/simcord/issues/11))
- Redesign the browser Preview as a Discord-inspired workbench with a compact app bar, readable authorized message navigation, responsive drawers, and an on-demand tabbed inspector instead of the sprawling toolbar and vertical diagnostics strip. Present human-readable action results and recovery guidance, keep viewport and capture tools outside the simulated surface, and place multi-select draft helpers beside their menu. Preserve page-local layouts, real callbacks, authorization, and managed capture isolation; fix local channel rendering so select drafts and other local interaction state update visibly.

  Keep mobile inspector isolation across snapshot and resize updates, preserve panning access to oversized fixed viewports, restore owning select focus after draft helper actions, retain the avatar gutter in narrow focused-message views, and preserve custom dimension drafts while refreshed state is published.

  Fix line-scoped subtext, including multiline spoilers with a shared reveal, and highlighted declarations; conceal formatted spoiler descendants and exclude unrevealed container media from lightbox navigation. Preserve accessibility after failed spoiler media loads, expose original downloads for all inline attachments, honor legacy file spoilers, and wrap playback controls in narrow columns.

  Keep dropdown scrolling and modal search/selection focus stable, support clearing optional selections and minimum-length optional text, cancel keyboard focus-out drafts, and disable clear controls with their owner. Keep searched channel targets visible and keyboard navigation focus intact; reconcile delayed lost send receipts in both stored drafts and the visible composer without discarding newer typing. Honor small exact captures, distinguish duplicate summaries, clear corrected viewport warnings, offer safe clipboard recovery and visible callback outputs, and improve timestamp contrast. Extend the real dogfood gallery with Markdown/media boundaries, editable messages, replies, reactions, polls, and callback outcomes. ([#22](https://github.com/SilentHacks/simcord/issues/22))
- Add a local, human-operated Discord reference capture kit with setup instructions and a self-contained agent handoff. Reuse the shared gallery payloads in the official test bot, export exact fixture assets and input hashes, and capture prepared UI with explicit provenance, lossless crops, no-overwrite protection and private allowlisted evidence export. Login and all Discord interactions remain manual; new captures are unreviewed, not parity certification.

  Preserve the bot's actual guild display identity in exported provenance even when member context is absent from its cache.
- Make the error-prefix acknowledgement and detached mutable result observations introduced by the 2.3 bridge unconditional; remove the temporary `future_behavior` option and legacy aliasing paths. Result handles remain live, explicit `raise_errors()` checks all captured errors, and later/shutdown errors still fail teardown. Integrate the released 2.3 shared message validation, atomic forum preparation and Python 3.14.8 shield-wait correction while preserving 3.0 sticker, mention, webhook identity and system-message behavior. Complete structural schema coverage for all 17 protocol-3 action kinds without changing runtime authorization or replay checks. The maintainer approved releasing 3.0 with explicitly documented preview evidence gaps; this is not Discord calibration or screen-reader certification.
- Polish the local preview with a restrained, independently styled SimCord toolbar and compact message navigation around the Discord-like conversation. Add consistent accessible SVG action icons, an auto-growing Enter/Shift+Enter composer with IME protection, expanded safe text-attachment previews, and hover/focus/touch image-download controls. Replace the boxed image lightbox with a preview-scoped scene showing authorized author/time, fit/zoom, keyboard/drag/touch panning, spoiler-safe navigation, and original-byte download/open actions. Preserve actor callbacks, authorization, page-local state, and exact capture boundaries. The checkout gallery includes a media/composer walkthrough; this does not certify Discord visual or interaction parity.

  Late file selections remain visible and removable after a modal redraw.
  Keep viewer avatar loads and original-image links scoped to the active image draw across Refresh and delayed navigation.

  Record actor mutations before settlement so failed, timed-out and cancelled receipts remain truthful.
  Redact channel topics and history boundaries immediately on permission revocation. Keep Unicode
  paging tokens bounded, discard stale derived media on source replacement, and allow explicit retry
  after transient worker failures. Validate and freeze animated emoji during captures, respect code-span
  and escape boundaries in spoilers, require MarkdownIt 4.1+, and derive font metadata from its validated
  manifest instead of a second inventory.

  Keep browser readiness false while actions or page intents are pending, including resize
  reconfiguration, so consumers do not interact with a render about to be replaced.

  Restore newly-created thread event routing and forum parent updates. Render poll answer text as
  accessible voting choices. Reject lost candidate cursor anchors before admission and recover retained
  queries on publication. Apply the selected timezone and deterministic emoji capture time to modal text.
- Validate the returned private calibration pack against its immutable archive and full sidecars, register an uncertainty-aware Ash profile, and replay its bot messages and visible states through the actual local preview. Emit natural-size captures, geometry/font diagnostics and per-capture comparison/gap reports without resizing references or certifying parity.

  Correct shared embed thumbnail/field layout and image aspect, two-item gallery geometry, obscured spoiler pixels, button focus and channel hover contrast, and role-option decoration. Keep select constraints accessible without adding default helper rows to the bot message; preserve visible validation errors. Refresh channel entity menus when query loading completes.

  Shorten the reference text-modal feedback label to meet Discord's 45-character limit. Verify the real offline bot-to-modal dispatch and document the narrow live bot-log/recapture follow-up; offline success does not establish the live timeout cause.

  Keep non-image spoiler media concealed until reveal, and clear entity listbox loading state after unchanged queries in channel, message and modal layouts. Mark callback buttons unavailable while an action or its publication is pending, retaining their keyboard focus and declared disabled state.

  Validate fresh exporter packs with dynamic inventories and an optional externally supplied archive
  fingerprint, without inheriting historical visual judgments. Replay documented select and V2 states
  with explicit crop recipes, retain honest evidence gaps as dogfood status evolves, and correct the
  optional single-select modal label so the real launcher acknowledges and opens it.

### Bug fixes

- Resolve receipt targets only for target-dependent preview actions, so deleting a stale focused message does not block an unrelated composer send. Native service messages now expose their recipients and referenced messages, and channel/thread notifications carry their actual names while preview presentation remains separate.
- Reject non-canonical Base64 cursor encodings, including changes to unused padding bits, consistently across message and candidate queries.
- Legacy embed previews now compose mixed field runs, thumbnails, images, author/footer metadata, timestamps, and provider attribution without a fixed thumbnail reservation. Embed media plays only from authorized offline bytes; external-only media remains unavailable, suppressed embeds leave message content and attachments visible, and reference geometry stays blocked pending measurements. ([#12](https://github.com/SilentHacks/simcord/issues/12))
- V2 galleries now use a responsive intrinsic-ratio grid, retain image descriptions as alt text, support keyboard spoiler reveal, and render zero/nonzero container accents correctly. Browser coverage exercises 1-, 3-, and 10-item galleries, narrow width, and component/media suppression and reuse; backend validation tests reject unknown type 20 and illegal V2 message compositions.

  Reference geometry remains blocked: `tests/fixtures/preview/measurements.json` → `historical-family-v2` requires authorized Discord interaction notes and measured crop/region coordinates, including mixed-aspect gallery captures across counts 1–10 at wide and narrow message widths. No reference parity budget is claimed. ([#13](https://github.com/SilentHacks/simcord/issues/13))
- Button previews now preserve all six style colors, use responsive rows for long labels and emoji controls, and expose keyboard focus intentionally even though the dated reference showed button focus equal to idle. Disabled links cannot navigate; disabled controls and style-5/6 controls are not dispatched as callbacks.

  Premium details are strictly caller-supplied through `sku_presentations` (`name`, `price_text`, `locale`, optional offline `icon_url`); values are never inferred or formatted. Missing metadata reports `premium-sku-metadata-missing`. A click only reports that purchases belong to Discord outside the message surface. No proprietary shop icon or purchase backend is included.

  `historical-family-buttons` calibration remains blocked: `measurements.json` has no crop regions, region boxes, or wrap points, and no legitimate SKU price/icon observations. No Discord button geometry or premium price/icon parity is claimed. ([#14](https://github.com/SilentHacks/simcord/issues/14))
- Select previews now use a select-only combobox/listbox: focus stays on the trigger, keyboard navigation exposes the active option, entity selections keep identity decorations, and long lists are viewport-positioned and scroll the active option into view. Candidate updates prune stale local choices without dispatching.

  The uncalibrated input fallback is explicit: single choices commit immediately; multi choices toggle until Enter, trigger close, or outside click and commit only within min/max; Escape and blur cancel. Message selections use the existing callback only on valid commit, while modal choices remain local until submit. Clearing a required modal select validates locally instead of sending an invalid request. No search or Apply control is inferred.

  `tests/fixtures/preview/states.json` marks `select-commit` and `select-cancel` blocked pending authorized Discord client access, a private test server/application, permitted fixture assets, and an interaction trace. `historical-select-popup` has no measured popup region boxes in `measurements.json`. Exact per-variant pointer, keyboard, clear, and commit parity is not claimed. ([#15](https://github.com/SilentHacks/simcord/issues/15))
- Modal previews now render the projected application identity and disclosure, title, close control, and viewport backdrop. The dialog remains bounded by the preview viewport and scrolls normally; surface screenshots no longer expand modal content. Labels, legacy text rows, Text Display, text/select/radio/checkbox/file-upload controls, effective defaults, and local field-associated validation feed the real Python modal action. Python remains authoritative. Upload copy matches SimCord's 10 MiB per-file and 25 MiB per-action limits; this may be lower than Discord's current allowance.

  Modal visual and interaction parity remains uncertified. `historical-modal-window` is non-comparable because the original crop includes Discord navigation; `historical-family-modals` has no measured region boxes, spacing, or wrap points. The modal validation and select transitions in `tests/fixtures/preview/states.json` remain blocked pending authorized Discord client/application access, permitted assets, and interaction traces. The bounded accessible rendering is a functional fallback, not invented measured geometry. ([#16](https://github.com/SilentHacks/simcord/issues/16))
- The independent preview review corrected channel component actions targeting the wrong message, centered focused history and kept its paging cursor consistent, removed private pending draft content from the browser's public status, and reauthorized message-linked media against the live message before serving cached bytes. Capture recipes now block unsupported or unobserved states instead of writing mislabeled evidence; a real idle and select-popup capture exercise the corrected runner. ([#19](https://github.com/SilentHacks/simcord/issues/19))
- Preview Markdown emits safe absolute HTTP(S) bare links only in link-enabled fields; fuzzy domains/emails and unsupported protocols remain plain text. Shared summaries conceal spoiler bodies and truncate at grapheme boundaries. Native font fallback now uses the full pinned Noto Color Emoji build, including regional flags; rendered Unicode glyphs were exercised across text and controls in Chromium. Substitute artwork remains an explicit, uncertified Discord-fidelity deviation.

  Preview snapshot and action consumers now use breaking protocol 3, with bounded authorized message and control-scoped candidate pages, typed causal receipts, and no protocol-2 compatibility shim. Screenshot viewport/layout overrides are capture-local and reports carry versioned visible-crop geometry.

  Preview preserves unchanged native media players across select redraws, captures swallowed View/Modal callback errors in Env.errors, and reports failed settlement without retrying callbacks. Meaningful error/recovery announcements are deduplicated; downloaded support reports omit private content, drafts, identifiers, URLs and raw exceptions.

  Native interaction webhook fetch/edit/delete now require admitted original/followup ownership, rather than accepting any message in the interaction's channel. Behavioral core checks cover malformed mention/sticker payloads, external sticker authorization, callback rejection without consuming acknowledgement, and protection of ordinary channel messages.

  Preview summaries no longer reveal Text Display spoiler bodies, and their schema matches grapheme-safe truncation. Presentation-only changes retain history anchors; viewer changes clear prior private activity. Observed receipts recover transport diagnostics, Close can escape an unadmitted uncertain action without replay, cached select identities are reauthorized on publication, and replaced rendering surfaces no longer retain obsolete failures. ([#21](https://github.com/SilentHacks/simcord/issues/21))
- Preview readiness now waits for retained message/modal media, avatars, and premium icons across redraws; late failures remain incomplete while their DOM owner is present. Viewer changes and bot restarts reload media under the new authorization boundary rather than reusing disposed players. Browser status exposes the strict protocol-3 public fields without internal draft, close-probe, or rendering-owner bookkeeping. Active incomplete diagnostics survive the bounded historical log until their surface is repaired or removed.

  Channel captures pin a bounded history window containing the requested target and crop the rendered message rather than its navigation button. Every projected message, nested reply/system/context-menu reference, and attachment owner is reauthorized before rendering and atomic PNG installation, including text previews whose source bytes exceeded the retention budget. Retained asset bytes are also validated. Deletion, attachment replacement/removal, or revoked access invalidates the capture even with `allow_incomplete=True`.

  Animation captures exclude separate APNG default images, honor GIF repeat versus APNG/WebP total-play semantics, preserve fractional APNG timing, and read decoded WebP durations before enforcing the ten-minute per-cycle ceiling. Finite playback holds its final frame. Lottie capture times are relative to the composition in-point.

  The 3.0 release excludes local planning/review documents, review screenshots, and the contributor-only calibration prompt from published documentation and distributions. Existing changelog fragments now use recognized Towncrier categories, and migration notes consistently describe breaking protocol 3 without compatibility shims. Package metadata and the lockfile are versioned 3.0.0; the maintainer explicitly waived outstanding external preview-evidence publication gates while retaining the known calibration and accessibility limitations.

### Documentation

- Preview's 3.0 migration uses breaking protocol-3 snapshots (`messageIndex` summaries, full `messages` keyed by ID, `timeline` ordering), explicit message action targets, channel layout and virtual presentation time. Opening Preview now requires the complete optional runtime; modeled system events can add history messages; modal surface captures no longer expand scrollable content. The guide documents media ceilings, offline assets, structured capture diagnostics and the private-reference workflow.

  The fixture ledger tracks feature and comparison states separately. Licensed Noto fonts replace unspecified system-font rendering for supported scripts, while Discord's proprietary font, external purchase/provider services and visible keyboard focus remain deliberate differences. No Discord pixel parity or human screen-reader certification is claimed without authorized provenance-checked references, measurements, interaction traces and manual accessibility review. ([#18](https://github.com/SilentHacks/simcord/issues/18))

### Miscellaneous

- Preview browser verification now covers six message widths at both 700/900 px heights, long mixed-direction content and zoom, and delayed media during viewer revocation across two pages. Narrow diagnostics no longer overflow the viewport. Distribution checks require packaged runtime assets, licenses, schemas and fixture tools; the installed wheel runs outside the checkout with a sparse system-font set.

  These checks establish local functional behavior only. Visual calibration still requires authorized private reference captures with measured regions and reviewed comparisons, and accessibility certification still requires human screen-reader smoke for modal, select, reaction and poll flows. Neither prerequisite is represented by an automated pass. ([#17](https://github.com/SilentHacks/simcord/issues/17))
- Register D01–D12 and U01–U09 as 21 reusable dogfood scenarios with separate implementation, reference, and human-review evidence states. The local gallery supports message, channel, and DM launches plus a capability-free catalog check. Missing authorized Discord traces and measured references remain blocked, not passing parity claims. ([#21](https://github.com/SilentHacks/simcord/issues/21))


## 2.3.0 (2026-10-05)

### Features

- Add the temporary `future_behavior=False` migration bridge in SimCord 2.3. Opt in with `simcord.run(bot, future_behavior=True)` or the pytest marker to acknowledge only the current error prefix and detach nested mutable result observations. Legacy live error lists, lifetime acknowledgement and payload aliasing remain the defaults, with targeted deprecation warnings. SimCord 3.0 removes the flag and makes the opt-in semantics the defaults. Startup/restart READY and restart guild-replay settlement retain their independent five-second budgets.

### Bug fixes

- Describe all seven existing protocol-2 preview actions in the packaged schema, including required targets/controls and envelope fields. Preserve focus defaults, unknown metadata, multipart modal uploads, and runtime authorization/replay checks. Reuse the existing builder operation guard for actors without changing ownership or permissions.
- Share message validation across HTTP, actors and backend creation/editing: enforce the 10-embed count, combined 6000-character limit and individual embed text/field limits, excluding surrounding whitespace without changing stored text. Reject empty new user/bot messages while retaining attachment-, embed-, component- and poll-only sends, nullable/partial edits and empty interaction deferrals. Previously accepted invalid messages now fail with code 50035, and invalid interaction responses do not consume acknowledgement.
  Interaction update callbacks without a source message now fail before acknowledgement instead of silently succeeding.

  Prepare forum starter fields, components and every upload read before creating thread/message/CDN state, then publish the existing ordered events only after thread, starter and parent state are complete. Validation or upload-read failures no longer leave orphan posts or gateway events. Preparation consumes upload streams; an earlier successfully read stream must be rewound or replaced if a later read fails. Subscriber exceptions after publication begins are outside this atomicity boundary.
  Starter reference identifiers are also validated before publication, preventing malformed references from corrupting the forum's previous last-post pointer.
- Recognize shielded Discord waits on Python 3.14.8 without treating the caller captured by asyncio's cancellation cleanup as an additional dependency.


## 2.2.1 (2026-09-24)

### Bug fixes

- Fixed View/Modal expiry recognition for views that never reach the store's dispatch tables — a view whose dispatchable items are all `DynamicItem`s (e.g. a `LayoutView` built only from patterned custom IDs) registered a live expiry task that settlement never recognized, so its `timeout=` blocked actor operations and fired on the wall clock. Expiry tasks are now identified by the coroutine they run (`BaseView.__timeout_task_impl`), which covers every registered view regardless of how the store indexes it.


## 2.2.0 (2026-09-23)

### Features

- Add an optional local component preview with real callback interactions, authorized viewer contexts, offline media assets, and deterministic Playwright screenshot reports. The base package remains lazy and networkless; install `simcord[preview]` for the bridge or `simcord[screenshot]` plus Chromium for capture.
- Breaking in SimCord 2.2: remove `Env.http_log`; use `Env.http_requests` and its `HttpLogEntry` records instead.
- Extend the local component preview: `env.preview(port=...)` pins the loopback port for stable capability URLs and pre-created SSH forwards, `await preview.snapshot()` returns the detached JSON projection as the structured/agent read surface, `screenshot(path=None)` captures in memory with PNG bytes on `PreviewCapture.png`, `window.simcordPreview` exposes `viewerId`/`targetId`, localhost origins are accepted, and internal bridge methods are now private by default.
- Hardened the local preview with deterministic presentation diagnostics, generation-scoped media readiness, modal focus isolation, safe page release, and strict action-envelope validation.
- Preview snapshots now publish protocol 2: `messageIndex` is picker-only summary data, `messages` maps authorized IDs to full projections, and `targetId` replaces `selected`. Component and modal controls use scoped stable control keys, mutating browser actions carry an explicit target and published revision, and `presentation_time` is deterministic and timezone-aware. Snapshot consumers must migrate; protocol 1 keys are not supported.
- Settlement now recognizes intentional timer-bounded waits — store-registered View/Modal expiry timers, `wait_for` timeouts on listener waits, `discord.ext.tasks` loop intervals, and wake-up timers scheduled inside `env.external_wait` — and makes them virtual-only: they fire only via `env.advance_time()` or their awaited input, never the wall clock, so they no longer burn real settle time or expire mid-operation. Migration: tests that relied on a View expiring on the wall clock without `advance_time` must now call `await env.advance_time(<timeout>)`.

### Documentation

- Improve the preview demo onboarding: `examples/preview_example.py` gains a `--keep-open` flag
  that keeps the session live for clicking (it calls `preview.wait_closed()`), the URL and
  human-facing notes move to stderr so stdout carries only the JSON capture report, the
  quickstart's next-steps list links to the preview guide, and CONTRIBUTING documents which
  optional extras unlock the preview tests, example, and type-checking.

### Miscellaneous

- Removed redundant code in the preview snapshot projection.
- The local component preview renders the Discord dark theme only: the `theme=` option and the browser theme toggle were removed. `profile.theme` still reports `"dark"` in snapshots and capture reports.


## 2.1.0 (2026-09-19)

### Features

- Add `Env.http_requests` records with query parameters and audit reasons while preserving the deprecated live `Env.http_log` tuple list for 2.x compatibility.


## 2.0.2 (2026-09-18)

### Bug fixes

- Fixed ``create_role`` leaving stale ``Role.position`` values in the bot's cache: inserting a role bumps every existing role's position, and the backend now announces each shift with ``GUILD_ROLE_UPDATE`` after ``GUILD_ROLE_CREATE``, as real Discord does. ``Member.top_role``, ``Role`` comparisons, and hierarchy checks now see the true ordering after role creation.
- Fixed ``role.colour`` always reading ``0`` in the bot's cache: role payloads now carry the ``colors`` object (``primary_color``/``secondary_color``/``tertiary_color``) that discord.py reads, alongside the deprecated flat ``color`` field.
- Fixed guild role listings to match real Discord's ordering: ``GET``/``PATCH /guilds/{id}/roles`` responses and the ``roles`` array inside guild payloads (``GUILD_CREATE``/``GUILD_UPDATE`` events and guild REST responses) are now sorted by role id ascending instead of insertion order (observable when a guild is created with an explicit id, which can make ``@everyone``'s snowflake the largest).


## 2.0.1 (2026-09-10)

### Bug fixes

- Fixed a 2.0.0 crash where ``Env.start()`` raised ``ValueError: ... was created in a different Context`` whenever ``setup_hook`` suspended on a timer or socket — for example opening a database pool with ``asyncpg.create_pool``, the documented discord.py startup pattern. Task resumption callbacks are now scheduled in the task's own ``Context`` instead of a copy, so ``ContextVar`` tokens created before a suspension remain valid after it. ([#24](https://github.com/SilentHacks/simcord/issues/24))


## 2.0.0 (2026-09-08)

### Features

- Settlement is now deterministic in 2.0: handlers now join all runnable bot-owned work,
  including executor/thread-backed work (`run_in_executor`, `asyncio.to_thread`, aiosqlite),
  instead of returning while a reaction is still in flight. This is a breaking change for
  1.x tests that relied on implicit coroutine-name parking or reconstructed ownership.
  Intentional external waits must use `await env.external_wait(awaitable, reason="...")`;
  unknown waits time out with diagnostics, while ownership survives timeout or cancellation
  so a later operation can recover the work without replaying the action. Public operations
  reject overlap before mutating state, and caller tasks survive teardown.
- Python 3.11+ is required; CI exercises Python 3.11, 3.12, 3.13, and 3.14.
- Components V2 parity now covers `discord.ui.LayoutView` wire-tree nesting, stable component
  IDs, custom ID and component limits, V2 message invariants, deterministic uploaded media
  metadata, and incoming-webhook `with_components` and message-edit handling. Remote media URLs
  remain offline metadata only; `attachment://` references resolve to uploaded files.

## 1.2.2 (2026-07-23)

### Documentation

- Fix social preview metadata to use the deployed Read the Docs asset URL.
- Replace the static README illustration with an animated terminal recording generated from the real example test suite.


## 1.2.1 (2026-07-23)

### Documentation

- Improve technical search and AI-answer discoverability with structured metadata, social previews, crawler and agent indexes, intent-focused discord.py testing guides, and a working example demo. Remove the obsolete dpytest migration guide.


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
