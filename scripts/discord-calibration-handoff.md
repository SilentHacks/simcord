# Local-agent handoff prompt

Copy the prompt below to the coding agent **on the machine where you will manually operate Discord**. Do not append credentials. The [setup guide](discord-visual-calibration.md) contains the full workflow.

```text
You are helping me collect a small, trustworthy Discord reference batch for SimCord's bot-facing component preview. Work in my local checkout of feature/component-preview. If needed, clone the supplied simcord-local-calibration.bundle with:
  git clone --branch feature/component-preview /path/to/simcord-local-calibration.bundle simcord-local-calibration
Read docs/guides/discord-visual-calibration.md, scripts/discord_reference_bot.py, scripts/capture_discord_reference.py and tests/fixtures/preview/catalog.py before acting. The goal is usable private evidence, NOT full Discord-client parity, renderer rewrites or certification.

Boundaries:
- I personally log in, navigate, scroll, click, hover, type, tab, select, upload, submit and dismiss Discord UI. Ask me to prepare each state. Never perform these user-account actions yourself, even through browser tools or injected JavaScript.
- You may inspect rendered geometry/styles/fonts read-only and take screenshots of the prepared test-bot surfaces. Do not mutate CSS/DOM, inject hotkeys/listeners, force focus, hide controls/carets, disable animation, or add synthetic Discord chrome. Do not use screenshot normalization to conceal mismatches.
- No user tokens, private Discord API calls, network interception, cookie/storage extraction, session export or use of my ordinary browser profile. Only my test bot uses the official bot API. Never request tokens in chat: direct me to the hidden --prompt-token terminal prompt. Do not read token files or echo credentials.
- Keep screenshots, identifiers, metadata and assets under ignored .discord-reference-captures/. Never commit private evidence, browser profiles, credentials or capability URLs. Share an evidence ZIP only after I review it and approve the private destination.

Proceed:
1. Verify Git/Python 3.11+/uv and a graphical desktop. Install from the checkout:
   uv sync --extra dev --extra screenshot
   uv run playwright install chromium
   uv run python scripts/discord_reference_bot.py --check
   uv run python scripts/capture_discord_reference.py --check
   On Linux use playwright install --with-deps chromium only if required. Report actual command results. These checks are offline and are not live Discord evidence.
2. Help me create/install my own private test bot with bot + applications.commands scopes, View Channel/Send Messages/Embed Links/Attach Files permissions, and no privileged intents. I supply the guild ID; never invent it. Use a quiet test channel and harmless candidates (another consenting member, a role, a text channel). Start the bot in a persistent terminal:
   uv run python scripts/discord_reference_bot.py --guild-id MY_GUILD_ID --prompt-token --output-dir .discord-reference-captures/private/bot-batch-01
   MY_GUILD_ID is the ID I manually copied, not a literal argument. Let me enter only the BOT token locally. Ask me to manually invoke /visual_references in Discord. Keep the bot running for callbacks. Do not overwrite a batch; choose fresh numbered directories if they already exist.
3. Wait for bot-fixtures.json and assets/ from that invocation. The bot and SimCord share gallery_payload(); verify the manifest's asset hashes against the files using local tools. Those are new normalized input hashes, not historical payload hashes. Preserve the original payload and exact bytes.
4. Start the manual capture CLI in another persistent terminal:
   uv run python scripts/capture_discord_reference.py --fixtures .discord-reference-captures/private/bot-batch-01/bot-fixtures.json --output-dir .discord-reference-captures/private/capture-batch-01 --width 1280 --height 700 --delay 8
   The headed dedicated browser profile holds my login locally and must never be exported. Ask me to log in and prepare dark/Cozy/100%-zoom settings in the tool's original tab, then answer the tool's profile questions. Record actual font size, client language, build/version and animation settings; unavailable values stay unknown. Do not change settings midway. Larger viewport/new batch is allowed for tall surfaces, but record it honestly.
5. Guide me through a small first batch: REF-10 embed idle; REF-20 buttons idle/hover/keyboard-focus with disabled controls visible; REF-30 string menu open/selection/Escape outcome; REF-31 user/role/mentionable/channel menus plus one selected state; REF-50 text modal empty/focused/invalid/filled; REF-40 V2 media sections and a manually revealed spoiler. Record missing/blocked prerequisites explicitly. Premium requires my app's real active SKU and is omitted otherwise. No purchase actions.
6. Use auto crop for an idle message or the open REF-50 modal when it finds the correct rendered bounds. For menus/tooltips/sections use explicit x y width height CSS-pixel crops that include the relevant overlay. If auto fails because Discord markup changed, measure read-only or help me manually find coordinates; do not silently invent a rectangle or alter the page. Out-of-viewport means I scroll/reframe or use separately named sections, not clipping a complete surface.
7. For every capture, help fill the fixture/state/crop and manual action/observation notes. Once the countdown starts, I return to Discord and manually reopen the menu, focus or hover the control. Terminal focus can dismiss menus. Do not automate that restoration. Increase the delay if necessary. Observe the output, then inspect the actual PNG to confirm the expected visible state and that no private/login/payment UI was captured. Callback receipts, values, commit-vs-cancel and validation results come from my actual interaction, not assumptions. If my observation occurs after the capture, preserve it as a clearly timed supplemental private note; do not retroactively claim it was visible in the PNG.
8. Finish with q, review image/metadata privacy with me, then export only the evidence allowlist:
   uv run python scripts/capture_discord_reference.py --export .discord-reference-captures/private/capture-batch-01 --zip .discord-reference-captures/private/calibration-batch-01.zip
   Never zip the whole .discord-reference-captures directory. Do not include browser-profile, cookies, local storage, tokens or unrelated conversations. Tell me I can manually remove the dedicated profile afterward if I want to discard its login session.

Hand back:
- Exact branch/source revision and commands actually exercised.
- Private ZIP location, inventory of fixture/state/image dimensions, profile and any unknown provenance.
- Actual callback/selection/cancellation/validation observations and missing/blocked states.
- Any incorrect crop/state, unexpected animation or sensitive image requiring recapture; don't claim success for it.
- All evidence remains observed-unreviewed and calibrated=false until independently reviewed and compared.

Do not implement renderer changes unless I separately ask. If later replaying in SimCord, match exact payload/assets, viewer/author labels, grouping/predecessor/timestamps, viewport/profile and crop/state. The existing capture_visual_reference.py uses isolated worlds, not an equivalent replay of the posted channel. Do not replace historical ledger hashes, pad/resize images, add fixture CSS or equate “screenshot exists” with parity. Raw same-size compare_visual_reference.py diffs are diagnostic evidence, not automatic certification.
```
