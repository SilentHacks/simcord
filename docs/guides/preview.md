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

The `preview` extra supplies `aiohttp`, `markdown-it-py`, and `Pillow`. It does not launch a browser.
For managed PNG capture, install Playwright and its pinned browser explicitly:

```bash
python -m pip install "simcord[screenshot]"
playwright install --with-deps chromium
```

`--with-deps` also installs the system libraries Chromium needs — required on bare Linux
containers; plain `playwright install chromium` suffices where those dependencies already exist.

`import simcord` and ordinary tests do not import these optional runtimes, read preview assets,
start a server, or download a browser. `aiohttp` already ships as a discord.py dependency, so a
Preview without the rest of the `preview` extra still serves — it degrades instead of refusing:
Markdown fields render as plain text, and inline image media reports a diagnostic advising
`install simcord[preview]` (the explicit download path still serves the original bytes). Calling
`preview.screenshot(...)` without Playwright or its browser gives a direct
`install simcord[screenshot]` / `playwright install --with-deps chromium` error.

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
        print(preview.url)  # sensitive loopback capability URL
        await preview.wait_closed()  # returns after browser Close or preview.close()
```

There is one active Preview per `Env`. `close()` is idempotent and is also called by context exit,
environment shutdown, and cancellation. Closing a browser tab releases only that page; it does
not stop Python or the Preview. The toolbar's **Close** action closes the whole session. No browser
is launched automatically.

`port=` pins the loopback port: `None` or `0` keeps the OS-assigned default, while an integer in
1–65535 binds that exact port so the capability URL is stable across reruns and pre-created SSH
forwards. The URL stays capability-gated either way. An out-of-range value raises `SetupError`, and
a port that is already occupied raises `SetupError` when the session is entered.

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
modal, and drafts. Browser actions that settle also publish their resulting edits/followups. A
publication is labeled by `publishedRevision` and simulated time; it is not a live synchronization
promise. Each publication's `messages` list carries detached summaries (`id`, `author_name`, and an
`excerpt` of roughly the first hundred content characters); the full message projection lives in
`selected`. A failed or timed-out action may already have mutated the backend: its last settled
projection is retained and marked stale, then a later successful refresh reconciles it without
replaying the action.

## Controls, keyboard, and accessibility

The bundled page uses semantic HTML buttons, inputs, textareas, native checkboxes/radios/file
pickers, labels, focus rings, and a styled listbox for string/entity selects. It renders the Discord
dark theme only — no legitimate light-mode reference exists to validate a second theme against —
plus bounded width/height controls, responsive wrapping, spoiler reveal, and accessible modal
focus containment. Use Tab/Shift+Tab to move through a modal, Escape to cancel a modal or close a
select first, Arrow keys/Home/End to navigate an open select, and Enter/Space to select or commit.
Single-select commits immediately; multi-select keeps a local draft until Enter/Apply semantics,
while Escape/outside click cancels it. Invalid min/max selections stay local and show a diagnostic.

The browser submits complete effective modal state, including untouched defaults, explicit `false`,
permitted empty values, and genuine uploaded bytes. Python remains authoritative for validation and
dispatch. Link buttons navigate only after an explicit click and never dispatch a callback; premium
purchase buttons are shown as unavailable. Unsupported component or presentation fields stay visible
as diagnostics rather than silently disappearing.

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

Inline validation uses Pillow for PNG, JPEG, WebP, and GIF; without the `preview` extra, image
media fails validation with the `install simcord[preview]` diagnostic while original bytes remain
downloadable. Audio/video playback, SVG/HTML, and unvalidated codecs are unsupported inline;
authorized original bytes may remain downloadable and a validated poster can represent unsupported
media without hiding its diagnostic. Display always serves a deterministic first frame re-encoded
as PNG without source metadata, so animated media
never stays animated in place. Non-image assets always serve the authorized original bytes; for
images, the explicit `?download=1` asset request (the page's open/download actions) is the path
that serves the original bytes rather than the normalized first frame.

All limits below are **Preview resource limits**, not Discord protocol limits. Requests are rejected
before unbounded buffering; bytes are never silently truncated:

| Resource | Limit |
| --- | --- |
| JSON action/envelope (including multipart JSON) | 256 KiB |
| Entire multipart request, including boundaries | 26 MiB |
| One uploaded file / aggregate uploaded bytes per action | 10 MiB / 25 MiB |
| Multipart files / total parts | 10 files / 11 parts |
| Retained session media (source, normalized, pinned; shared blobs count once) | 128 MiB |
| Raster width or height / pixels per frame | 8,192 / 16 megapixels |
| Animated frames / decoded RGBA bytes per asset | 100 / 64 MiB |
| Media processing | One bounded decode job; no unbounded queue |
| Interactive pages / managed captures | 16 pages / one capture |
| Inactive page context lifetime | 10 minutes after the last request, unless an action is active; expired pages are reaped and their assets released |
| Screenshot raster axis / total pixels | 32,768 / 32 megapixels |
| Managed capture deadline | 30 seconds |

Pillow also rejects malformed streams and decompression-bomb warnings. The media worker is lazy,
serial, and external to Env's event loop; cancelling it is awaited before its buffers are released.
These limits bound accepted work but are not an operating-system sandbox for malicious bot code.

## Readiness, generations, and action reconciliation

A browser exposes a read-only `window.simcordPreview` object for agents and capture tooling:

```javascript
{
  protocolVersion, contextId, contextGeneration, botGeneration,
  viewerId, targetId, publishedRevision, renderGeneration,
  lastAction, ready, complete, calibration, diagnostics, profile
}
```

`ready` belongs to the current local `renderGeneration`. It becomes true only after the displayed
settled projection, DOM, fonts, authorized media (or explicit diagnostics), and two animation frames
are ready. It does not mean the callback succeeded, the output is complete, or the visual result is
calibrated. `publishedRevision` is a settled publication; `contextGeneration` changes on viewer or
focus changes; `botGeneration` changes on restart; render generations also cover local changes such
as dropdowns, spoiler reveal, modal drafts, validation, and profile edits. Old media/font/render
continuations cannot update a newer generation.

Every callback action carries a context generation, bot generation, request ID, published revision,
and positive per-page sequence. Kind, control resolution, and values are validated before the
sequence is admitted. Admission is at-most-once: a duplicate latest request with the same payload
returns its
recorded status, a changed payload conflicts, old sequences expire, and gaps are rejected. Busy,
stale, unauthorized, disabled, deleted, or invalid controls are rejected before admission and are
never automatically retried. A disconnected client does not cancel an admitted callback; delivery
failure is separate from callback settlement.

A pre-admission rejection is an ordinary result, not a transport error: the response reports
`rejected: true`, `settlement: "rejected"`, `dispatch: "not_dispatched"`, the offending `sequence`,
the page's current `expectedSequence`, the live `revision` and `presentation`, and one structured
diagnostic (`bad-envelope`, `stale-sequence`, `sequence-gap`, `conflicting-request`, `busy`,
`stale-context`, `stale-generation`, `stale-revision`, `unknown-kind`, or `validation-failed`).
Rejections never consume the sequence: the next admissible action may reuse it. Mutating actions
(`click`, `select`, `modal_submit`) must echo the page's current `publishedRevision`; a mismatch is
rejected as `stale-revision` so a stale render cannot dispatch into newer state.

`lastAction` reports dispatch (`dispatched`/`not_dispatched`), acknowledgement
(`pending`/`acknowledged`/`deferred`/`unacknowledged`), settlement (`pending`/`settled`/`failed`/
`timeout`/`cancelled`), and presentation (`current`/`stale`/`access_denied`) independently. Bot callback
errors and mutations are both retained; errors are diagnostics, not rollback. An unacknowledged
interaction is failure, not simulated success. Inspect this result and then refresh; never retry a
consumed sequence to make a screenshot look successful.

## Structured snapshots

`await preview.snapshot()` is the structured, agent-facing read surface: it settles bot work,
republishes the Python presentation, and returns the detached JSON projection dict the bundled page
renders — the same state a browser would display, as plain data. It is intended for assertions and
text-only tooling where no browser or PNG is needed, and raises `SetupError` when the preview is not
active:

```python
snapshot = await preview.snapshot()
assert snapshot["selected"] is not None
print(snapshot["diagnostics"], snapshot["lastAction"])
```

The payload is protocol-versioned diagnostic data, not pixel output: `protocolVersion` labels the
shape, `publishedRevision`/`botGeneration` label the settled publication it reflects, and the
remaining keys — `context`, `viewers`, `viewerId`, `channelId`, `channel`, `targetId`, `messages`,
`selected`, `modal`, `candidates`, `assets`, `profile`, `status`, `diagnostics`, and `lastAction` —
describe the focused presentation. Field-level details may evolve under `protocolVersion`; assert on
the documented keys rather than the exact payload layout.

## Screenshot profiles, modes, and reports

`preview.screenshot(path, ...)` settles and pins the requested viewer/target before releasing the
Env operation guard. It returns an immutable `PreviewCapture`, not just a path:

```python
capture = await preview.screenshot(
    "panel.png", viewer=alice, target=panel, mode="surface",
    allow_incomplete=False,
)
assert capture.ready
print(capture.complete, capture.diagnostics)
```

`path` is `str | os.PathLike | None`. Pass `None` to render entirely in memory: `capture.path` is then `None` and
`capture.png` holds the PNG bytes (`bytes | None`, `None` when a filesystem path was given), which
suits agents and diff tooling that never touch disk.

The effective profile records theme, viewport width/height, locale, timezone, device scale, reduced
motion, Playwright/browser versions, system font identity, emoji fallback, and animation policy.
`mode="surface"` captures the focused message (or expanded modal) at its measured geometry;
`mode="viewport"` captures the requested viewport after hiding toolbar/diagnostics. Surface output
is useful for component diffs; viewport output preserves a reproducible page frame. Width and height
are positive bounded integers and are checked against the screenshot raster limits above.

The report includes path, viewer/channel/target/modal IDs, published and render generations, output
geometry, profile, readiness, calibration, action status, and structured diagnostics. Captures reject
unsettled actions, unavailable authorization, incomplete output (unless explicitly opted in),
concurrent capture, a bot restart during capture, and invalid destinations. Output is written
atomically, so cancellation or a failed capture does not leave a partial PNG. A pinned capture
cannot follow later focus, viewer, or backend changes; access and attachment membership are
rechecked before bytes are served.

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
checkbox groups, and checkboxes. Polls, stickers, voice, purchasing, arbitrary remote media,
server/channel navigation, login, a command composer, and native-mobile Discord are outside this
surface or remain explicitly unavailable. Components and Markdown are rendered with controlled DOM
nodes; their layout, typography, line wrapping, emoji fallback, responsive behavior, and browser
font metrics can differ from Discord. No proprietary Discord font or asset is bundled: this package
uses the platform system font stack and records that substitution in the capture profile.

No legitimate Discord reference fixture is bundled in this release, so captures are uncalibrated by
default. A stable repeated capture proves regression reproducibility, not Discord parity. When
maintainers obtain permitted references, record client/platform/date, theme, density/font scale,
viewport, locale, timezone, substitute font, browser build, and known differences separately from
functional tests.

See [Components & modals](components.md) for actor-level callback tests, the
[parity matrix](../parity-matrix.md) for backend support, and the [API reference](../api.md) for
public signatures.
