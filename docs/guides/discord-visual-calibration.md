# Local Discord visual calibration

This checkout provides a bot, a human-operated capture tool, and a [local-agent handoff](discord-calibration-handoff.md). It does **not** automate a Discord user account. The first goal is a small trustworthy reference batch, not a claim of full-client parity.

## 1. Get this branch onto your machine

Use the supplied Git bundle if the branch is not available in your normal checkout:

```bash
git clone --branch feature/component-preview /path/to/simcord-local-calibration.bundle simcord-local-calibration
```

Open a terminal in that checkout. The bundle contains source and instructions, not credentials or private reference images. Git, Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) are required; the capture tool needs a graphical desktop.

```bash
uv sync --extra dev --extra screenshot
uv run playwright install chromium
uv run python scripts/discord_reference_bot.py --check
uv run python scripts/capture_discord_reference.py --check
```

These commands also work in PowerShell. On Linux, missing browser system libraries can be installed with `uv run playwright install --with-deps chromium` (may require administrator privileges). The capture check uses a local generated page; it never logs in to Discord or labels its images as Discord evidence.

## 2. Prepare a private test guild and bot

Create your own test application/bot in the [Discord developer portal](https://discord.com/developers/applications). Install it in a private test guild with **bot** and **applications.commands** scopes. Allow **View Channel**, **Send Messages**, **Embed Links**, and **Attach Files** in the test channel. Your user needs **Use Application Commands**. No privileged intents, Administrator permission, user token, or self-bot are needed.

Enable Developer Mode in Discord and manually copy the guild ID. Create a quiet text channel dedicated to these fixtures. Use harmless names and no real conversations. For entity menus, add a second consenting test member, a visible role, and a visible text channel so candidates are meaningful; record the visible names and restrictions in capture notes.

In terminal A, replace `123456789012345678` with your guild ID:

```bash
uv run python scripts/discord_reference_bot.py --guild-id 123456789012345678 --prompt-token --output-dir .discord-reference-captures/private/bot-batch-01
```

Enter **only the test bot token** at the hidden local prompt. Never put any token into chat, a handoff prompt, source, screenshots, or shell command history. Existing `DISCORD_TOKEN`/`DISCORD_GUILD_ID` environment configuration remains supported. Premium controls require an active SKU owned by this application via `DISCORD_SKU_ID`; otherwise premium is explicitly outside this batch.

Wait for the “Synced /visual_references” message. **Manually** invoke `/visual_references` in the test channel. This posts eight labelled messages, then writes:

- `bot-batch-01/bot-fixtures.json`: normalized input payloads and hashes, asset hashes, message/channel IDs, author, timestamps and predecessor context;
- `bot-batch-01/assets/`: the exact attachment bytes generated for those posts.

The bot now uses the same `gallery_payload()` factories as the SimCord capture runner. Mention normalization substitutes `@simcord-viewer` for the invoking user; these hashes are **new local input hashes**, not historical ledger hashes or a claim about Discord's complete wire representation.

**Leave terminal A and the bot running** throughout interaction capture, or callbacks and modal launchers will stop responding. One output directory is one gallery invocation. To post again, stop the bot and restart with a fresh directory such as `bot-batch-02`; never overwrite an earlier batch.

## 3. Open the dedicated manual-capture browser

In terminal B:

```bash
uv run python scripts/capture_discord_reference.py --fixtures .discord-reference-captures/private/bot-batch-01/bot-fixtures.json --output-dir .discord-reference-captures/private/capture-batch-01 --width 1280 --height 700 --delay 8
```

The tool opens headed Chromium with a **dedicated persistent profile** at `.discord-reference-captures/browser-profile`. Manually log in, navigate to the test channel, and configure:

- browser zoom **100%**, dark theme, Cozy display, and an explicitly recorded chat font size;
- a known UI language, animation/reduced-motion preference and Discord build/version if obtainable;
- no unrelated conversations, settings, payment UI, login/2FA dialogs or notifications in the capture area.

Keep using the tab opened by the tool. Finish these settings before answering its profile questions; unknown fields stay `unknown`, never guessed. Do not change profile settings during a batch: start a new capture directory if they change. For tall surfaces you may choose a taller viewport at startup, or record separately named visible sections; never stitch, pad, resize, or clip a supposedly complete surface.

The browser profile contains your local login session. It is ignored by Git, **not part of the evidence pack**, and must never be shared. Afterward you may manually delete only that dedicated profile to remove the saved session. The tool never reads/export cookies, storage or tokens, intercepts network traffic, or changes Discord CSS/DOM to manufacture a match.

## 4. Capture a small calibration batch

For each capture, enter the fixture ID, a unique state name, a crop, and the manual action/observation notes. Use `auto` for an idle message or open REF-50 modal: it reads current rendered bounds. Discord markup can change; if bounds cannot be found, enter explicit **CSS-pixel** `x y width height`. For menus/tooltips, use an explicit crop containing both the control and its overlay; the message's bounds alone do not include the menu.

If you need coordinates, your local agent may inspect the rendered DOM/bounding boxes **read-only**, or you may manually use browser DevTools. Close DevTools before capture and recheck viewport geometry. No injected handlers, forced focus, CSS overrides or synthetic Discord chrome. Include enough context to identify the component without including unrelated client chrome. Out-of-viewport crops are rejected, not silently clipped.

After the terminal prints the countdown, **return to Discord and manually prepare/reopen the requested state**. Switching to the terminal can close menus or lose focus; the delay exists to restore the real state. Hover the requested control, tab to keyboard focus, or reopen the modal/menu yourself. Do not switch away until the screenshot has been taken. Increase `--delay` if eight seconds is insufficient. The tool refuses non-channel/login URLs, the wrong channel, a non-1 pixel ratio, unfocused Discord, and existing capture names.

Start with this batch rather than all historical rows:

| Fixture | Suggested uniquely named captures | Manual observations |
| --- | --- | --- |
| REF-10-LEGACY-EMBED | `idle` | Field layout, wrapping, thumbnail/hero dimensions; preserve actual author/grouping context. |
| REF-20-BUTTONS | `idle`, `primary-hover`, `primary-focus` | Disabled appearance; hover and keyboard focus must be genuinely prepared. Do not purchase a SKU. |
| REF-30-STRING-SELECT | `closed`, `open`, `two-selected`, `escape-cancel` | Values before/after selection; callback receipt; whether Escape cancels or commits. |
| REF-31-ENTITY-SELECTS | `user-open`, `role-open`, `mentionable-open`, `channel-open`, `user-selected` | Exact visible candidate labels/decorations, availability, selected value and callback receipt. |
| REF-50-MODALS | `text-empty`, `text-focus`, `text-validation`, `text-filled` | Open the text modal manually; attempt invalid submission; note browser/client validation and callback outcome. |
| REF-40-V2-LAYOUT-MEDIA | `idle-top`, `idle-media`, `spoiler-revealed` | Name sections honestly; manually reveal spoilers. Record precisely which image/container was revealed. |

REF-00 and REF-11 remain available for context/attachment follow-up. More modal families, upload states, pressed-state timing and premium states can be captured when their prerequisites are available. Never infer blocked states from another family. Enter `q` to finish; a fresh output directory is required for another session, while the dedicated login profile can be reused.

Each PNG gets a JSON sidecar. `reference-pack.json` records PNG SHA-256, the actual viewport/device ratio/browser/locale/timezone/loaded font declarations, user-attested settings, crop geometry, manual trace, original fixture input and asset hashes. Font declarations do not prove which font rendered every glyph. Screenshots preserve live animation and caret state; moving pixels may require repeat samples with unique names.

All captures start as **observed-unreviewed, calibrated=false**. Unknown client build, fonts or behavior observations remain provenance gaps. Check every image for the intended state, sensitive content and cropped-off edges before accepting it as a useful reference.

## 5. Return only reviewed private evidence

```bash
uv run python scripts/capture_discord_reference.py --export .discord-reference-captures/private/capture-batch-01 --zip .discord-reference-captures/private/calibration-batch-01.zip
```

The ZIP allowlist includes the pack manifest, declared PNGs/sidecars and their fixture assets, not the browser profile, token configuration or unrelated files. The pack still contains private server/user identifiers and possibly avatar URLs; share it only through an approved private artifact channel after reviewing the images and metadata. Do not commit it or upload it to a public issue. Return the ZIP plus a short inventory of captures, missing states, profile unknowns and noteworthy callback/validation outcomes.

The receiving agent must reproduce **the same payload, asset bytes, author/mention text, grouping, viewport/profile, crop and manually observed state** in SimCord. Existing `capture_visual_reference.py` creates isolated local worlds, so it is not automatically a scene-equivalent replay of this grouped Discord channel. New rows/profile measurements need deliberate review; do not overwrite historical hashes or force these files into historical ledger rows.

A raw pixel comparison can be run only after matching crops and dimensions:

```bash
uv run python scripts/compare_visual_reference.py /path/to/discord-crop.png /path/to/simcord-crop.png --output-dir .discord-reference-captures/private/comparison-batch-01
```

Dimension mismatch is evidence, not a reason to resize. Use diff/overlay output to identify typography, spacing, color and behavior mismatches. A captured PNG, a passed offline check, or a comparison invocation alone never certifies parity.
