---
title: "Component preview and screenshots"
description: "Use SimCord's authenticated local browser preview to inspect real discord.py component callbacks, modal edits, authorized viewers, and deterministic screenshots offline."
---

# Component preview and screenshots

The optional Preview opens the messages produced by your real bot in a browser. Controls dispatch
through SimCord actors, so a click is a real callback and not a frontend mock. The bridge listens
on loopback only; it never connects to Discord.

Preview promises Discord-like presentation and SimCord-backed behavior, **not pixel-perfect Discord
emulation**. Functional coverage, completeness, and visual calibration are separate outcomes.

## Install only what you need

The base package remains lazy and networkless:

```bash
python -m pip install simcord
```

Install the browser bridge when you need a page:

```bash
python -m pip install "simcord[preview]"
```

The `preview` extra supplies `aiohttp`, `markdown-it-py`, `linkify-it-py`, `regex`, Pillow, and PyAV.
`markdown-it-py` parses link-enabled Markdown; `linkify-it-py` recognizes links for the safe absolute
HTTP(S)-only linkifier, and `regex` keeps summary truncation on grapheme boundaries. The text helpers
remain lazy and are not imported by `import simcord`. Installing the extra does not launch a browser.
For managed PNG capture, install Playwright and its pinned browser explicitly:

```bash
python -m pip install "simcord[screenshot]"
playwright install --with-deps chromium
```

`--with-deps` also installs the system libraries Chromium needs — required on bare Linux
containers; plain `playwright install chromium` suffices where those dependencies already exist.

`import simcord` and ordinary tests do not import these optional runtimes, read preview assets,
start a server, or download a browser. Entering a Preview now **requires** the complete `preview`
extra and packaged fonts; missing dependencies fail early with a `simcord[preview]` install
instruction instead of silently degrading Markdown or media. This is a breaking change from the
older partial-preview behavior. Calling `preview.screenshot(...)` without Playwright or its browser
gives a direct `install simcord[screenshot]` / `playwright install --with-deps chromium` error.

## Start and stop a session

Preview is an async context manager. Create it after the environment is running and close it before
the environment exits:

```python
import simcord

async with simcord.run(create_bot()) as env:
    guild = env.create_guild()
    channel = guild.create_text_channel("preview")
    alice = guild.add_member(env.create_user("alice"))
    await alice.send(channel, "!panel")

    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(channel.last_message)
        await preview.wait_closed()  # returns after browser Close or preview.close()
```

There is one active Preview per `Env`. `close()` is idempotent and is also called by context exit,
environment shutdown, and cancellation. Closing a browser tab releases only that page; it does
not stop Python or the Preview. **More → End preview session** closes the whole session. No browser
is launched automatically.

`layout="message"` is the default and preserves the focused-message surface.
`layout="channel"` opens a real channel viewport with an authorized 50-message history window,
older/newer paging, and a composer. Focusing a message moves its window into view. The composer sends
through the configured viewer's SimCord actor and may reply only to messages that viewer can access;
rejected or failed sends retain the draft. It does not navigate to another channel or create a new
Preview session. The inspector remains outside the emulated viewport, while a modal backdrop covers
only that viewport.

`display="responsive"` (default for human browser pages) fits the measured workspace with a logical
viewport minimum of 240×180. `presentation.host` retains the actual host dimensions; below the minimum,
the viewport clamps to 240×180, reports `host_below_minimum`, and the stage pans rather than claiming
an impossible fit. `"fixed"` preserves the exact `width`/`height` profile and never flex-shrinks it;
overflow remains visible for host panning. The inspector stays outside the simulated viewport and its
capture crop. Each browser page owns its layout, display and profile; reconfiguration does not change
other pages or the Python/session capture defaults.

`port=` pins the loopback origin: `None` or `0` keeps the OS-assigned port, while an integer in
1–65535 binds that exact origin so a pre-created SSH forward can use the same port on both ends.
It does not make the capability-bearing URL stable or safe to log. An out-of-range value raises
`SetupError`, and a port that is already occupied raises `SetupError` when the session is entered.

`env.restart_bot()` does **not** close a Preview — the session, pages, and URL survive the restart.
The restart advances the bot generation, so action envelopes sent by a page loaded before it are
rejected as `stale-generation` before admission, without consuming the sequence. Page-side recovery
is not possible on its own: reads only serve the last published snapshot, which still carries the
old generation. Call `await preview.refresh()` after restarting to republish every page under the
new generation, or reload the page to open a fresh context. A restart also invalidates a pinned
managed capture already in flight.

## Python and browser page ownership

`env.preview(...)` creates a Python presentation using the first viewer. `await preview.show(target)`
changes that Python presentation's focused message or modal and becomes the default for new pages.
An ordinary browser page receives its own server-issued context: viewer, focus, modal, theme,
viewport, drafts, dropdown state, focus, and scroll are page-local. Switching a viewer clears that
page's private drafts, uploads, assets, and pending modal. Existing pages are not retargeted by
`show()` or by another page's picker. A managed screenshot uses a separate read-only pinned context
and cannot submit callbacks or alter human pages.

Pass a `discord.Message`, `ResponseMessage`, or `InteractionResult` to `show()`. Targets must be
from this environment and channel and visible to the selected viewer. A modal can be shown only to
its original opener; the source-message linkage remains available to `interaction.message` and
`edit_message()`.

## Viewers, access, and private data

`viewers` is a non-empty, unique allowlist of same-Env handles:

- Guild previews require `MemberActor` members of the selected guild, current `view_channel` and
  `read_message_history` access, and private-thread membership (or the documented owner/admin
  exception).
- DM previews require the owning `UserHandle`; a guild context is never manufactured for a DM.
- Ephemeral messages, referenced messages, mentions, entity-select candidates, and assets are
  authorized independently. A denied target is not distinguished from a missing target.
- Access is rechecked on every state, asset, and pinned-capture read. Revoked access or removed
  attachments invalidate cached content; already delivered pixels cannot be remotely recalled.

The capability holder may switch between supplied viewers. It is not authentication for unrelated
humans; use separate sessions for separate trust domains. Treat `preview.url`, terminal output,
shell/browser history, and agent transcripts containing it as credentials. Never paste it into CI
logs, reports, bug issues, or screenshots.

## Refresh and staleness

Preview uses HTTP commands and polling, not a websocket or always-running Python poller. Polling only
reads the latest published revision. Python actor calls, `advance_time()`, background callbacks, and
other scenario mutations become visible after an explicit refresh:

```python
await actor.click(message, custom_id="save")
await preview.refresh()
```

`refresh()` settles bot work and republishes every page while preserving each page's viewer, target,
channel-history anchor, modal, and drafts. Browser actions that settle also publish their resulting
edits/followups. A publication is labeled by `publishedRevision` and simulated time; it is not a live
synchronization promise. `messages` maps full authorized projections for the current window (at most
50 in channel layout, or the focused target in message layout), and `timeline` gives their visible
order. `history` reports window boundaries and whether authorized earlier/later messages remain. The
separate `messageIndex` is a bounded navigation-summary page; `navigation` carries its query and page
cursors. `targetId` identifies the focused projection; there is no `selected` alias. A failed or
timed-out action may already have mutated the backend: its last settled projection is retained and
marked stale, then a later successful refresh reconciles it without replaying the action.

## Controls, keyboard, and accessibility

The bundled page uses semantic HTML buttons, inputs, textareas, native checkboxes/radios/file
pickers, labels, focus rings, and select-only comboboxes with listbox popups for string/entity
selects. Select focus stays on the trigger and keyboard navigation exposes an active option; no
search field is emulated. Modal focus remains contained. It also renders the Discord dark theme,
bounded width/height controls, responsive wrapping, and spoiler reveal.

The SimCord workbench stays outside the emulated Discord viewport and managed screenshot crop:

- **Viewing as** selects the authorized actor used by message and component actions.
- **Messages** toggles a sidebar with readable previews, search, bounded result pages, and
  **Jump to message ID**. On narrow screens it opens a drawer; Escape closes it and restores focus.
  Selecting a result focuses that authorized message without changing the configured layout.
- **Refresh preview** publishes the latest scenario state; it is not guaranteed live synchronization.
- **Inspector** opens Activity, Diagnostics, and Capture tabs. Activity presents operation results
  and links to authorized outputs, with protocol receipts available under **Technical details**.
  Diagnostics show recovery instructions and an actionable warning/error count on the Inspector button.
  A callback response or follow-up also exposes **View response** in the app bar, without opening
  the Inspector. Message rows include their local message ID so duplicate summaries remain distinct.
- **Capture** opens viewport settings: **Fit window**, **Fixed size**, presets, custom dimensions,
  and **Isolate message** or **Conversation** layout. These settings belong to this browser page.
  The managed Python recipe does not inherit live browser drafts or open menus; use a browser
  screenshot tool to capture those transient states.

Tab/Shift+Tab navigates the workbench, arrow keys/Home/End switch inspector tabs, and Escape closes
an open inspector. Narrow-screen drawers isolate the background while open. Modal dialogs isolate
the workbench and return focus to their invoking control when dismissed. Multi-select **Apply/Cancel**
helpers sit beside their owning menu, are explicitly marked as SimCord controls, and are omitted
from managed captures. The workbench represents only the current preview channel, not a full
Discord client with server, voice, or cross-channel navigation.

Use Tab/Shift+Tab to move through a modal, Escape to close a select before the modal, and Arrow
keys/Home/End to navigate an open select. Enter/Space on an open single select commits its active
choice immediately. Multi-select pointer clicks and Space toggle a local draft; Enter, trigger-close,
or outside click commits only when min/max are satisfied. Escape and focus leaving the select cancel
the draft. Message choices use the existing callback; modal values stay local until submit. A required
message clear stays local, and a required modal clear is validated locally before dispatch; invalid
counts are never sent to the backend.

Optional single selects expose **Clear selection**. Optional modal fields may be left empty or
cleared even when a minimum length/count applies to nonempty answers; required fields still reject
empty answers. Entity search supports result navigation and selection without implicitly submitting
the modal, and keeps search focus when its results update.

These select transitions are an uncalibrated accessible fallback: reference commit/cancel traces are
blocked, so no Discord search/Apply behavior or per-variant commit parity is claimed. See the
[parity matrix](../parity-matrix.md) for the reference-data blocker.

The browser submits complete effective modal state, including untouched defaults, explicit `false`,
permitted empty values, and genuine uploaded bytes. Python remains authoritative for validation and
dispatch. Link buttons navigate only after an explicit click and never dispatch a callback; premium
buttons use caller-supplied offline SKU presentation and disclose the external purchase boundary
without dispatching. Missing metadata produces an incomplete diagnostic. Unsupported component or
presentation fields stay visible as diagnostics rather than silently disappearing.

Modal fields retain required/optional semantics, defaults, and local drafts. Validation points to
the affected control and leaves drafts recoverable; Python validates every submitted value again
before the real callback runs. Optional untouched fields without defaults may be omitted, while
effective defaults, false checkbox values, and allowed empty text values remain represented.
Uploads are the selected browser files and bytes, never names synthesized by the preview. SimCord
enforces 10 MiB per file and 25 MiB total uploaded bytes per action; this local resource policy may
be lower than Discord's current upload allowance.

The modal dialog stays within its preview viewport and scrolls its body; changing capture mode never
expands it. The modal family's exact geometry and validation/select transition traces remain
uncertified because the available reference window has no comparable crop measurements and no
authorized interaction trace. See the [parity matrix](../parity-matrix.md).

In channel layout, history paging and message sends are admitted against the current publication.
The composer is available only when the selected viewer has send permission; replies are one level
deep and require an authorized referenced message. A successful send clears its draft. A lost
response is reconciled from its settled receipt without replaying the send or discarding newer
typing, even if polling already displayed the sent message. Rejected or confirmed failed sends
retain the draft for correction. The bot receives the ordinary actor message event, and replies it
creates appear after a new publication.

## Text, code and emoji

Preview parses message and Text Display bodies on the server into safe tokens. Message bodies and
embed descriptions/field values support block Markdown; embed titles and field names stay inline,
and embed footers, system text, labels and descriptions remain literal text. Mentions and command
references resolve only against the selected viewer's authorized users, roles, channels and known
chat-input commands. Code spans are literal, raw HTML is never inserted, and explicit links keep the
existing HTTP, HTTPS and mailto policy. Bare links are parsed by Markdown only in link-enabled fields,
and only absolute HTTP(S) URLs are linked; fuzzy domains/emails, protocol-relative URLs and other
schemes remain text. Escaped text, code, and literal fields are not browser-autolinked. Links never
trigger remote fetches or previews.

Message and Text Display subtext (`-# ` at line start) applies only to that line, including inside
multiline spoilers. Revealing one fragment reveals the other lines of the same spoiler, without
revealing adjacent spoilers.

Fenced code preserves its language label. Highlighting uses the pinned Highlight.js 11.11.1 ESM
build from `@highlightjs/cdn-assets` with these explicit grammars: Bash, C++, C#, CSS, diff,
Dockerfile, Go, INI, Java, JavaScript, JSON, Markdown, Python, Rust, SQL, TypeScript, XML and YAML.
There is no auto-detection; unsupported languages remain escaped code. The vendored license and
per-file SHA-256 values are in `static/vendor/highlight/LICENSE` and `SHA256SUMS`.

Discord timestamps use the page's locale, timezone and published `presentationTime`; relative
timestamps do not tick against the host clock, and the absolute time is available on hover.
Unicode emoji in explicit descriptors select the packaged Noto Color Emoji face; mixed text keeps
Latin or monospace primary and places emoji before broad script fallbacks. Native text shaping and
variation selectors remain intact. Packaged releases are Noto Sans/Italic v2.015, Sans Mono v2.014,
Arabic v2.012, Hebrew v3.001, Devanagari v2.007, Simplified Chinese (SC; Google Fonts commit
`e44c4b0`), and Noto Color Emoji v2.051 (COLRv1, commit `0612165`); unmodified asset sources and
hashes are listed in `static/fonts/manifest.json`. The fonts carry the SIL Open Font License 1.1;
the corresponding `OFL-1.1-Noto*.txt` notices ship beside the assets. They are licensed substitutes
for proprietary Discord fonts/artwork, not a claim of Discord fidelity. The full emoji build includes
regional-indicator flags; Latin font ranges leave their shaping to the native emoji fallback.
Chromium glyph inspection and visible flag rendering were exercised across the text/control corpus
with a sparse one-face system-font configuration. This is not platform-wide glyph, Discord artwork,
or human assistive-technology certification.

The installed metadata records `markdown-it-py` and `linkify-it-py` as MIT and `regex` as
Apache-2.0 AND CNRI-Python; each installed distribution supplies its own license files. These are
Python dependencies, not copied into SimCord's vendored assets.


## Offline assets and media

Uploaded bytes are served from SimCord's in-memory CDN, and only through the message attachment
that owns them: an attachment URL copied into another message's embed or component never resolves
CDN bytes. Other URLs are rendered only when supplied in the explicit `assets={url: (filename,
bytes)}` mapping. The bridge has no arbitrary filesystem root, URL proxy, remote-media fetch,
Discord font download, or runtime documentation-image fetch. Assets are exposed as opaque,
page-authorized IDs and browser blob URLs; they are revoked on page replacement, viewer switch, and
close. Authorization is rechecked when bytes are served: the viewer must still have channel and
history access, the owning message must still be visible, and the attachment must still be present,
so deleting a message or removing an attachment immediately invalidates its assets. Asset IDs stay
stable across publications while the underlying asset remains referenced. Missing bytes show a
labeled unavailable tile and make a capture incomplete unless `allow_incomplete=True`.

## Premium button presentations

Supply optional `sku_presentations` to `env.preview(...)` to render caller-provided offline details.
It maps positive SKU snowflake strings to objects with exactly `name` (1–100 characters),
`price_text` (1–80 characters), and a supported Discord `locale`, plus optional `icon_url`.
`name` and `price_text` are displayed verbatim; SimCord does not look up, format, or infer commerce data.
The locale must be a supported Discord locale. If `icon_url` is
provided, its safe HTTP(S) URL must have matching raster bytes in the existing `assets` mapping;
the URL is never fetched or exposed to the browser. No proprietary shop icon is bundled.

A premium button without supplied details has a generic unavailable label and a structured
`premium-sku-metadata-missing` diagnostic, so captures are incomplete rather than displaying an
invented name or price. Activating any enabled premium button writes an external-purchase notice to
the preview toolbar: purchases are handled by Discord outside the message surface and nothing is
started here. It never dispatches a bot callback. Historical button geometry remains uncalibrated
because the reference catalog has no crop regions or measured wrap points, and it contains no
legitimate SKU price/icon observations.

Inline raster validation uses Pillow for PNG, JPEG, WebP, GIF, and APNG. PyAV validates audio and
video streams. Media over 10 MiB remains downloadable but is not decoded inline. `available` reports
retained source bytes; `displayReady` remains false until validation succeeds. Valid still images are
EXIF-oriented, metadata-stripped PNGs. Animated raster originals stay animated for interactive browser
playback; captures select a deterministic frame at `media_time` and serve a static PNG.

Audio/video sources remain downloadable separately from display transcodes and static video captures.
The validator preserves WebM with VP8/VP9 video and optional Opus/Vorbis audio, and Ogg Opus audio;
other supported tracks are transcoded to WebM VP9/Opus or Ogg Opus. The manifest reports actual
`sourceCodecs`, `displayCodecs`, transformation, and quality differences. PyAV/FFmpeg decoder and
encoder availability depends on the installed platform build; if required codecs are absent, media is
unavailable inline rather than silently faked. Interactive audio/video uses native browser playback;
managed screenshots pause at a deterministic `media_time`.

Each inline attachment also exposes its retained original-byte download, including when inline
validation fails. Spoiler attachments conceal both content and controls until reveal; the lightbox
does not navigate into unrevealed item or container spoilers.

Lottie stickers use the pinned, MIT-licensed, expression-free light Canvas runtime shipped locally.
Expressions, fonts/glyphs, and external or data-URL assets are rejected. SVG and HTML files remain
download-only; text previews use text nodes, and file cards expose an explicit original-byte download.
The accessible image lightbox uses only already-loaded local blob URLs; Escape closes it, arrow keys
navigate its loaded image group, and close restores focus to the opener. It makes no remote media
request. File size labels round up to KB or MB.

Attachment images keep their validated intrinsic ratio and are bounded to the message column and
viewport. They currently remain a responsive vertical list rather than a guessed mosaic: the local
`historical-family-attachments_media` measurement row is blocked, with only the single-image
`ref-11-attachments-idle` capture and no measured count/ratio cases for 2–10 images. This fallback
does not claim Discord multi-image layout parity; the measured mosaic remains evidence-dependent.

Managed captures honor exact positive viewport dimensions below the human workbench's responsive
minimum. Small viewports still crop content normally; they do not silently expand the requested size.

All limits below are **Preview resource limits**, not Discord protocol limits. Requests are rejected
before unbounded buffering; bytes are never silently truncated:

| Resource | Limit |
| --- | --- |
| JSON action/envelope (including multipart JSON) | 256 KiB |
| Entire multipart request, including boundaries | 26 MiB |
| One uploaded file / aggregate uploaded bytes per action | 10 MiB / 25 MiB |
| Multipart files / total parts | 10 files / 11 parts |
| Retained session media (source, normalized, pinned; shared blobs count once) | 128 MiB |
| Media decoder source / normalized display / still capture | 10 MiB / 10 MiB / 64 MiB |
| Raster/video dimensions / pixels per frame | 8,192 per axis / 16 megapixels |
| Animation/video frames / media duration | 18,000 / 10 minutes |
| Media jobs / worker / deadline / Linux address space | 8 queued / 1 child / 30 seconds / 512 MiB |
| Interactive pages / managed captures | 16 pages / one capture |
| Inactive page context lifetime | 10 minutes after the last request, unless an action is active; expired pages are reaped and their assets released |
| Screenshot raster axis / total pixels | 32,768 / 32 megapixels |
| Managed capture deadline | 30 seconds |

Pillow rejects malformed streams and decompression-bomb warnings. Media decoding runs lazily in one
serial, killable subprocess, outside Env's event loop. Linux enforces a 512 MiB address-space ceiling;
other platforms retain queue, process, and deadline limits but cannot enforce that memory ceiling, so
adversarial-media memory safety is not certified there. These limits are not an operating-system
sandbox for malicious bot code.

## Readiness, generations, and action reconciliation

A browser exposes the immutable, deeply read-only `window.simcordPreview` status object (schema
version 1, protocol version 3) for agents and capture tooling:

```javascript
{
  schemaVersion: 1, protocolVersion: 3, contextId, contextGeneration, botGeneration,
  viewerId, targetId, activeControlKey, visibleMessageIds, projectedMessageIds,
  publishedRevision, publication, navigation, presentation, renderGeneration,
  renderState, selectDrafts, selectStates, lastAction, pendingAction, pendingQuery,
  ready, awaitingRevision, queryResultRevision, geometry, complete, diagnostics,
  activity, transport, authorized, calibration, profile
}
```

`ready` belongs to the current local `renderGeneration`. It becomes true only after the displayed
settled projection, DOM, fonts, authorized media, validated display dimensions (or explicit diagnostics),
and two animation frames are ready. It does not mean the callback succeeded, the output is
complete, or the capture is calibrated. `publishedRevision` is a settled publication;
`contextGeneration` changes on viewer or focus changes;
`botGeneration` changes on restart; render generations also cover local changes such
as dropdowns, spoiler reveal, modal drafts, validation, and profile edits. Old media/font/render
continuations cannot update a newer generation.

`projectedMessageIds` mirrors `snapshot["timeline"]`; `visibleMessageIds` lists only IDs currently
visible in the simulated viewport, not every projected window item. `geometry` reports measured app,
host, stage, focused-surface, timeline and modal-body geometry. `selectDrafts` and `selectStates` expose
page-local draft/commit state. Publication, transport health, `ready`, and `complete` are separate
facts, not one freshness or success flag.

Every browser action carries `protocol_version: 3`, positive `generation`, `bot_generation`, the
observed `published_revision`, a positive per-page `sequence`, a `request_id`, and its `kind`.
Malformed envelopes return a safe `bad-envelope`/`unsupported-protocol` receipt before admission;
correctly typed but stale context or generation values return `stale-context` or `stale-generation`.
Kind, control resolution, and values are validated before the sequence is admitted. Admission is
at-most-once: a duplicate latest request with the same payload returns its recorded outcome, a
changed payload conflicts, old sequences expire, and gaps are rejected. Busy, stale, unauthorized,
disabled, deleted, or invalid controls are rejected before admission and are never automatically
retried. A disconnected client does not cancel an admitted callback; delivery failure is separate
from callback settlement.

Revision-bound actions (`click`, `select`, `modal_submit`, `history`, `send_message`, `edit_message`,
`delete_message`, `set_reaction`, `set_poll_votes`, `set_pinned`, `browse_messages`,
`browse_candidates`, and `configure_presentation`) must echo the page's current
`publishedRevision`; a mismatch is rejected as `stale-revision`. Per-message actions also carry the
authorized message `target_id`.

Each receipt retains `requestId`, `sequence`, `expectedSequence`, `rejected`, `dispatch`,
`acknowledgement`, `settlement`, `presentation`, `revision`, typed safe `diagnostics`, `target`,
`uncertain`, server-generated `correlation`, and authorized `outcomes`. Outcomes distinguish
responses, followups, source edits, sent messages, modals, deferred interactions, no output, and
unavailable outputs. They are correlated to the admitted interaction/actor operation, never guessed
from a channel diff or last-message change. View and Back are explicit navigation: activating View
focuses an authorized outcome, and Back returns to the prior target and invoking control if it still
exists. Receipts never auto-focus a page. A pre-admission rejection does not consume the sequence;
the activity ledger holds at most 20 receipts and no message bodies, and references are reauthorized
on every read. Diagnostics use catalog-owned codes and remediation, never raw request or exception
text. Inspect the receipt and refresh after failures; never retry a consumed sequence to make a
screenshot look successful.

## Structured snapshots

`await preview.snapshot()` is the structured, agent-facing read surface: it settles bot work,
republishes the Python presentation, and returns the detached JSON projection dict the bundled page
renders — the same authorized state a browser would display, as plain data. It is private diagnostic
data, intended for assertions and text-only tooling, not for sharing. It raises `SetupError` when the
preview is not active:

```python
snapshot = await preview.snapshot()
target = snapshot["messages"].get(snapshot["targetId"])
assert target is not None
print(snapshot["diagnostics"], snapshot["lastAction"])
```

The detached payload is protocol-versioned diagnostic data, not pixel output. `protocolVersion` is
`3`; `runtimeVersion` and `publishedRevision` label the runtime and settled page state. `publication`
contains a real UTC `publishedAt`, its revision, and a publication reason. `presentation` describes
page-local display mode, layout, viewport, exact profile, measured host dimensions, and any fit
constraint.

`messageIndex` is one authorized navigation page of at most 50 summaries: ID, author identity, created
and edited times, safe text excerpt, content kinds, component labels, attachment count/kinds, and
ephemeral state. `navigation` carries the normalized query, filter, page boundaries and opaque cursors.
`browse_messages` queries authorized summaries only; it does not run bot callbacks or settle bot work.
Queries are plain NFC/case-folded text, limited to 128 Unicode code points before and after
normalization, and return no more than 50 rows. Numeric snowflakes perform exact-ID lookup; denied and
deleted IDs produce the same empty page and unavailable navigation shape. Cursors are opaque and bound
to the page generation, query, scope and stable ordering.

`candidates` is keyed by scoped `control_key`; each descriptor separates independently authorized
`selected` identities from at most 50 `entries`, and reports type, filter, state, query and cursors.
`browse_candidates` queries one currently authorized control (and `modal_handle` for modal controls).
Defaults/selections are resolved separately from the options page, count against select constraints,
and are not pruned when outside that page. Entity controls do not project raw
`component.default_values`; use the descriptor's `selected` entries. Browser drafts remain page-local
and are visible in `window.simcordPreview.selectDrafts`/`selectStates`. Querying does not run bot work
or dispatch an interaction.

`messages` contains full authorized projections, `timeline` gives projected order, and `history`
reports omitted history. `targetId` identifies the focused projection. `modal`, `assets`, `entities`,
`profile`, `status`, typed `diagnostics`, `lastAction`, and the at-most-20-entry `activity` ledger
describe the same page. Message references in receipts are reauthorized on each read. The bundled
schema is authoritative; field-level details may evolve under `protocolVersion`.

The private snapshot is not the Copy/Download support report. Those buttons produce only the
allowlisted schema-version-1, protocol-3 report: runtime/protocol/renderer versions, display and
viewport/host dimensions, publication revision, and at most 40 safe diagnostic codes, severities,
states, remediation values and approved structural paths/correlation IDs. It excludes viewer/channel/
message IDs, names, message or draft text, filenames, URLs, capabilities, raw exception/HTTP text,
and PNG data. Treat snapshots, executable recipes and screenshots as private even when they omit a
capability.

## Migrating preview consumers to protocol 3

Protocol 3 is the current schema and a breaking cutover: it changes the message navigation page and
candidate descriptors and adds typed receipts, publication and diagnostics state. Protocol-2 consumers
must migrate explicitly; there are no compatibility aliases or adapter. Do not silently accept an
older `protocolVersion`:

```python
snapshot = await preview.snapshot()
if snapshot["protocolVersion"] != 3:
    raise RuntimeError("migrate this consumer to preview protocol 3")

selected = snapshot["messages"].get(snapshot["targetId"])  # full authorized projection
summaries = snapshot["messageIndex"]                       # at most 50 authorized summaries
window = [snapshot["messages"][mid] for mid in snapshot["timeline"]]
candidate_pages = snapshot["candidates"]                       # authorized control_key -> descriptor
```

An action envelope includes `protocol_version: 3`, `generation`, `bot_generation`,
`published_revision`, `sequence`, `request_id`, and `kind`. For example, the bundled consumer sends
`browse_messages` with `{query, filter: "all", cursor}` or `browse_candidates` with
`{control_key, query, cursor, modal_handle?}`. The response contains a typed receipt and a `result`
with only the requested page. The `protocol.schema.json` file also defines the immutable browser-status
and allowlisted support-report shapes; there is no diagnostics/report upload endpoint.

## Channel layout, publication time, and system history

Use `layout="channel"` to see the authorized history window and composer rather than only the
focused message. A message action in this window names its own `target_id`; a custom ID or focused-page
target alone does not identify a message. `window.simcordPreview` remains a read-only status surface;
use the bundled controls for actions.

Relative labels and `<t:...>` tokens use `presentation_time` (timezone-aware), or the settled
Env virtual clock when omitted. Publications freeze that time until refresh, rather than following
the browser clock:

```python
async with env.preview(channel, viewers=[alice], layout="channel") as preview:
    before = await preview.snapshot()
    env.advance_time(3600)
    await preview.refresh()
    after = await preview.snapshot()
    assert before["profile"]["presentationTime"] != after["profile"]["presentationTime"]
```

Pin/thread/join and other modeled system events can now create real history entries, allocate
message IDs and change `channel.last_message` or bot event order. Existing assertions should target
the intended message by ID or type, not assume no intervening system message. A modal
`screenshot(..., mode="surface")` now captures the viewport-constrained dialog **without** expanding
its scroll contents; inspect top and bottom in a browser session rather than comparing a
formerly expanded image.

## Channel message actions

In channel layout, messages show only actions authorized for the selected viewer: reply, own-message
edit, permitted delete, manage-messages pin/unpin, reaction changes, and open polls. The toolbar
does not expose unsupported account or service actions. Sends include `content` and optionally
`reply_to_id`; message mutations use `target_id`. Reaction actions send an emoji and desired
`reacted` membership. Poll actions send the full desired `answer_ids` set, including an empty list
to remove all of the viewer's votes. Delete actions require `confirmed: true`; the browser asks
before sending them.

These operations use guarded actor APIs and report `acknowledgement: "not_applicable"`. Successful
message mutations republish every open authorized page. Reaction projections expose emoji, count,
and the viewer's own reaction state; poll projections expose answer counts, percentages, expiry,
and the viewer's selections, never voter lists. Failed actions retain recoverable drafts; refresh
the page after a stale or failed result rather than replaying the consumed request.

`preview.screenshot(path, ...)` settles and pins the requested viewer/target before releasing the
Env operation guard. It returns an immutable, versioned `PreviewCapture`, not just a path:

```python
capture = await preview.screenshot(
    "panel.png", viewer=alice, target=panel, mode="surface",
    viewport=(360, 640), layout="channel",
)
assert capture.ready
print(capture.complete, capture.geometry)
```

`viewport=(width, height)` requests those exact logical dimensions for this capture; `layout` is
`"message"` or `"channel"`. Both override only the managed capture pin and do not resize or retarget
human pages or change session defaults. If omitted, dimensions and layout come from the Python
presentation. The report identifies `schema_version=1` and `protocol_version=3`; `geometry` records
the requested logical viewport, content extent, visible crop, scroll offset, overflow, scope and output
dimensions.

`path` is `str | os.PathLike | None`. Pass `None` to render entirely in memory: `capture.path` is then
`None` and `capture.png` holds the PNG bytes (`bytes | None`, `None` when a filesystem path was given),
which suits agents and diff tooling that never touch disk.

The effective profile records theme, viewport width/height, locale, timezone, device scale,
reduced motion, Playwright/browser versions, packaged font faces and any actual platform fallback,
emoji fallback, and animation policy. `mode="surface"` captures only the visible intersection of the
focused message or modal dialog with its owning viewport (`scope="visible"`); it never expands,
stitches or scrolls content to simulate a full-scroll image. `mode="viewport"` captures the exact
logical preview viewport, including its modal backdrop and dialog but excluding the outside inspector.
Use an interactive browser session to inspect modal content at both scroll extremes. Width and height
are positive bounded integers and are checked against the screenshot raster limits above.

The report includes viewer, channel, target and modal IDs, published and render generations, geometry,
media metadata (effective frame times, codec selection, transformations), readiness, calibration,
action status and diagnostics. Captures reject unsettled actions, unavailable authorization,
incomplete output (unless explicitly opted in), concurrent capture, a bot restart during capture, and
invalid destinations. Output is written atomically, so cancellation or a failed capture does not leave
a partial PNG. A pinned capture cannot follow later focus, viewer or backend changes; access and
attachment membership are rechecked before bytes are served.

The Capture panel's executable recipe is Python for the real active scenario, with `preview` in scope;
it is not a standalone scenario definition. Managed captures use a separate pinned page and do not
inherit a human page's open menus, select/modal drafts, or other transient UI state. To capture those
states, use the browser/agent screenshot tool on that live page after checking
`window.simcordPreview.ready`, the action/publication revision and render generation. The recipe and
live screenshot are private artifacts, unlike the allowlisted support report above.

## Reference fixture catalog

The checkout keeps the reusable Discord gallery factories in
`tests/fixtures/preview/catalog.py`. The authorized reference bot imports those factories and
still supports its offline check:

```bash
python scripts/discord_reference_bot.py --check
```

For a fresh human-operated Discord capture batch, use the
[local calibration kit](discord-visual-calibration.md) and its
[local-agent handoff](discord-calibration-handoff.md). It exports exact bot fixture assets,
records observed provenance, and keeps Discord actions manual.

`tests/fixtures/preview/coverage.json` is the single evidence ledger. Its rows retain fixture
recipes, canonical payload hashes, aliases, crop/profile metadata, expected outcomes, and separate
reference/implementation/comparison statuses. Profiles, state-transition recipes, and measured
regions live beside it in `profiles.json`, `states.json`, and `measurements.json`. Validate the
ledger without opening any image:

```bash
python scripts/compare_visual_reference.py \
  --check-manifest tests/fixtures/preview/coverage.json
```

Historical captures are registered by filename, dimensions, and SHA-256 only. Private Discord
images, identities, credentials, and capability URLs stay in the ignored local reference pack.
Unknown provenance or unavailable authorized observations are recorded as deterministic `blocked`
rows with their prerequisite and owner; they are never treated as passing comparisons. A
whole-window modal image is `not_comparable` until every compared region belongs to the product
surface—do not add a synthetic shell or fixture-specific CSS to make it pass.

### One fixture, a family, or the private batch

Install `simcord[screenshot]`, install Playwright Chromium, and use the example above to
create a world, `show()` its target and inspect `snapshot()["diagnostics"]`; inspect the
`PreviewCapture` report's separate `ready`, `complete`, `calibrated`, `diagnostics`, and
`geometry` fields. The [executable example](https://github.com/SilentHacks/simcord/blob/master/examples/preview_example.py) prints a
scrubbed JSON report, not its capability-bearing URL (except in explicitly interactive
`--keep-open` mode on stderr). From a checkout, maintainers can then run:

```bash
python scripts/capture_visual_reference.py --check
python scripts/capture_visual_reference.py --fixture historical.ref.00.index.idle
python scripts/capture_visual_reference.py --family buttons
python scripts/capture_visual_reference.py --all
python scripts/compare_visual_reference.py \
  --manifest tests/fixtures/preview/coverage.json \
  --reference-dir .discord-reference-captures/private \
  --actual-dir .discord-reference-captures/current \
  --output-dir .discord-reference-captures/diff --required
```

For one comparison add `--fixture ID`, or `--family buttons` for a family; omit
both for the entire private batch. Use a separate clean `--actual-dir` per selection:
unexpected PNGs from other rows correctly invalidate a batch. Capture writes
`capture-report.json` and per-row metadata to the ignored output directory. Unsupported
state recipes or controls that do not reach their registered state are `blocked`, never
silently captured as idle.
Comparison writes `comparison.json` and a
contact sheet there, without resizing the images. Only a provenance-checked private
`reference-pack.json` and authorized local images can support certification; the
historical image registrations alone cannot. No credentials, images, identities,
capability URLs or raw source bytes belong in commits, CI logs or shared reports.

Exit `0` means the selected runnable captures completed, or all comparable selected
rows passed comparison; `1` means a comparable visual difference; `2` means invalid,
blocked or non-comparable rows (capture uses `2` for blocked). Do not turn `2` into
success by lowering a threshold or supplying a fabricated reference. Complete missing
measurements/interaction traces with the authorized reference owner, review deviations
and perform human screen-reader smoke before claiming parity.

## Loopback security and SSH forwarding

The server binds only to `127.0.0.1` on a loopback port. Every API and asset request requires the
random capability header and (for pages) the opaque context header. Host and Origin are checked,
CORS is not enabled, and responses use no-store, no-referrer, `nosniff`, clickjacking protection,
and a restrictive same-origin CSP. Static files come from an explicit packaged-file map. The bridge
provides no Python invocation, expression evaluation, route forwarding, shell command, or arbitrary
file-write API.

For a remote development host, keep the same port on both ends so Host validation still succeeds.
If the remote process reports `http://127.0.0.1:PORT/#CAPABILITY`, run from your local machine:

```bash
ssh -N -L PORT:127.0.0.1:PORT user@remote-host
```

Then open `http://127.0.0.1:PORT/#CAPABILITY` locally. The SSH account and local browser now hold
the capability; use a private tunnel, never `-g` or a public bind. If `PORT` is already occupied
locally, stop the conflicting listener rather than changing only one side of the forward. Forwarding
is a trusted-user workflow, not multi-user authentication or a sandbox.

With an OS-assigned port the forward can only be created after the URL is known. Pinning `port=`
reverses that order — create the tunnel first, then start the session on the port it already
forwards:

```bash
ssh -N -L 8765:127.0.0.1:8765 user@remote-host
```

```python
async with env.preview(channel, viewers=[alice], port=8765) as preview:
    ...
```

A busy pinned port raises `SetupError` on entry rather than silently moving, so a stale forward
never points at the wrong listener.

## Fidelity, completeness, and calibration

These are intentionally independent:

- **Rendered/ready:** the current local DOM finished its generation.
- **Complete:** no in-scope component, media, or presentation behavior is missing; diagnostics can
  make a capture incomplete.
- **Calibrated:** a legitimate, dated Discord reference exists for the exercised profile and its
  comparison has been reviewed.

The renderer covers legacy messages, embeds, action rows, buttons, string/entity selects, text
inputs, V2 sections/text/media/files/separators/containers, labels, file uploads, radio groups,
checkbox groups, checkboxes, channel history/composer/replies, reaction and poll state, and supplied
stickers, voice attachments, and bounded animated media. Purchasing, arbitrary remote media,
server/channel navigation, login, and native-mobile Discord remain outside this preview surface.
Components and Markdown use controlled DOM nodes; their line wrapping, glyph outlines, geometry,
and platform codec availability may differ from Discord. Licensed Noto fonts and Noto Color Emoji
are packaged for covered scripts; proprietary Discord fonts and assets are not bundled. Their
per-face versions and SIL OFL 1.1 notices are listed in `static/fonts/manifest.json` and its adjacent
license files; this does not certify actual browser glyph selection.

No certification-grade Discord reference pack is bundled, so arbitrary captures remain uncalibrated.
The ledger names the exact blocked profiles and fixture prerequisites. A repeated PNG proves local
reproducibility, not Discord parity. A release additionally requires authorized private comparison
and human screen-reader smoke for modal, select, reaction, and poll flows; automated roles alone do
not certify usability. Record reviewed client/platform/date, theme, density/font scale, viewport,
locale, timezone, font/browser versions and explicit deviations without publishing private images.

See [Components & modals](components.md) for actor-level callback tests, the
[parity matrix](../parity-matrix.md) for backend support, the
[dated evidence attestation](../preview-attestation.md) for exact status counts and
blocked prerequisites, and the [API reference](../api.md) for public signatures.
