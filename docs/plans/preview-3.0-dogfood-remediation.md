# Preview 3.0: complete dogfood remediation commit plan

Status: **implementation in progress; reference and human accessibility release gates remain blocked**.

Implementation checkpoint: Commit 01 registers all 21 findings with independent implementation/reference states. The local protocol-3 cutover, responsive/exact presentation, scoped message/modal entity queries, Unicode/link pipeline, causal navigation, diagnostics/report privacy, and capture recipes are implemented. Real Chromium journeys, a sparse-font glyph corpus, executed Python capture recipes, fresh wheel/sdist checks, and an installed-wheel modal/edit/PNG journey were exercised. The repository suite passes 888 tests; the unchanged core coverage gate still fails at 94.27% against 95%. An untouched `988e4a2` snapshot also fails that gate at 94%; no threshold reduction or unrelated test padding was made. Independent review is blocked by the failed required gate. Authorized Discord transition/pixel evidence and human screen-reader gates remain unavailable; no 3.0 release or complete finding closure is claimed.

Source of requirements: [the dogfood audit](../reviews/preview-dogfood.md), findings **D01–D12 and U01–U09**. These identifiers always refer to that audit, not the unrelated decision IDs in older plans.

This is the authoritative **remaining remediation queue** for those findings. It supersedes conflicting implementation choices and commit ordering in [the earlier 3.0 parity plan](discord-preview-3.0-parity.md). Preserve that plan's wider supported-message scope, resource/security contracts, legal reference policy, and outstanding release-evidence requirements; they are not waived. Do not replay its already-landed protocol-2, identity, channel, media, or capture work. The two older component plans remain historical context.

## 1. Outcome and boundaries

Deliver a component preview that a developer can understand on first use and an agent can operate without guessing from pixels or scraping implementation details:

- Every supported component is reachable and usable, including modal entity selectors.
- Browser dimensions, render dimensions, capture dimensions, and crop scope are truthful and separate.
- Navigation identifies messages, finds them efficiently, and exposes actual callback outcomes without stealing the user's place.
- Draft, committed, pending, failed, and uncertain outcomes are distinct.
- Keyboard focus and errors are discoverable; accessibility is verified beyond DOM inspection.
- The same authorized projections and actor operations serve humans, Python consumers, and browser agents.
- Breaking changes are intentional, documented 3.0 cutovers—not accidental behavior loss or permanent compatibility paths.

Correctness takes priority over a small diff. Conversely, a rewrite is not inherently correct: retain the authenticated bridge, native ES modules, safe token renderer, identity/asset resolver, backend validation, and actor dispatch where their responsibilities are sound. Extract concrete responsibilities as their owning changes land; do not add a frontend framework, generic state framework, plugin system, or second simulator.

**Preserve throughout:** loopback-only binding; capability/Host/Origin checks; CSP and static allowlist; offline operation; per-viewer authorization and asset ownership; page-local drafts/focus/scroll; read-only pinned managed captures; generation/revision checks; at-most-once admission; truthful settlement; cancellation/shutdown; atomic PNG writes; current page/media/upload limits; lazy optional dependencies and base-package imports.

“Without breaking anything” means no accidental regressions in those guarantees or supported bot behavior. It cannot mean both retaining every old wire shape and making a clean 3.0 redesign. The intended incompatibilities below have explicit migrations; unrelated Python/backend APIs remain unchanged.

### Planning baseline facts that constrain this plan

- Protocol **2** already exists in `preview/protocol.schema.json`, `_snapshot.py`, `app.js`, capture tooling, and integration tests. Do not introduce protocol 2 again.
- `_snapshot.py` already uses scoped control keys and shared identity/candidate construction. The missing modal traversal is a source-of-data defect, not an absent select implementation.
- `_Page` already owns authorization, revision/generation, modal, target, history window, and replay state. Extend it; do not create a competing page registry.
- `_ActionOps` already records the admitted interaction; backend Interaction has `message_id`, `source_message_id`, and `followup_ids`. Use these for provenance, not a before/after channel diff.
- `_publish()` already increments `page.revision` on **every publication**, including unchanged content. Keep this semantics. D08's healthy-read failure does not justify a new content-hash revision model. The audit's attempted Refresh observation must be checked against an actually successful refresh receipt and installed revision during implementation; do not assume successful publication leaves the revision unchanged.
- `Preview.screenshot` already accepts snowflake targets, isolates a pinned page, freezes media time, and separates readiness/completeness/calibration. Do not build another capture service.
- `-#`, ordinary string selects, uploads, and real channel actions already worked in the audit. Preserve them and their semantic boundaries.
- Existing reference traces for select commit/cancel and modal validation remain blocked. Missing observations cannot become invented client behavior.

## 2. Expert decision record: every finding has a disposition

These are specialist judgments grounded in the source and audit, **not claims that a named external expert was consulted**. Each rejected choice has a substantive correctness objection. No rejected choice is permitted as an interim shipped solution.

| Finding / expert lens | Chosen long-term decision | Rejected choice and why an expert would reject it | Explicit trade-off and migration | Owning commits |
| --- | --- | --- | --- | --- |
| D01 / security and domain modeling | Enumerate supported controls from both authorized message and modal trees through one scope-aware traversal; resolve selected/default entities and query candidates through the existing identity/eligibility owner. | Adding a special-case UserSelect list, borrowing a message's candidates, or dumping the guild fails other families, scope isolation, revocation, and least privilege. | Control-scoped candidate queries add loading/paging behavior; protocol consumers migrate. Defaults must be independently resolvable without appearing on the first options page. | 02, 05 |
| D02 / interaction and capture design | Own one focused-content scroll region inside the simulated viewport; every line/control remains reachable. Capture visible scope explicitly. | Increasing the default height, `overflow:auto` on every wrapper, or expanding content only for screenshots relocates the defect, creates scroll traps, or photographs a different product. | Long content requires intentional scrolling; viewport captures are crops, not full-message exports. Capture reports disclose that scope. | 03, 08 |
| D03 / visual metrology | Separate actual page render viewport, host availability, and configured capture profile; fixed mode never flex-shrinks. | Reporting requested width while silently shrinking or transforming the surface makes screenshots, wrapping, and AX geometry untrustworthy. | New browser/profile metadata and a 3.0 semantic migration; fixed profiles may need host panning. | 02, 03 |
| D04 / responsive accessibility | Default human pages to an explicitly labeled responsive viewport that fits the available workspace; offer exact fixed-profile inspection separately. | Automatically scaling an exact profile makes text/targets smaller and complicates popup hit-testing; leaving 720 px plus inspector chrome offscreen makes basic controls inaccessible. | Responsive pages can wrap differently from the session's capture defaults. The distinction is always displayed, and agents can select exact mode. No display-scale feature is added. | 03 |
| D05 / typography and internationalization | Use packaged Noto emoji in actual font selection, preserve native shaping/variation selectors, and verify rendered glyphs at every text/control surface. | Merely loading a font, putting emoji first for all text, hand-splitting emoji with regex, or replacing selectable text with glyph images breaks shaping, ordinary typography, or reproducibility. | Licensed substitute artwork remains an explicit Discord-fidelity deviation; some platform/browser differences remain outside the certified profile. No new emoji parser is added. | 04, 09 |
| D06 / parser engineering and security | Use `markdown-it-py`'s supported linkification path with a declared `linkify-it-py` dependency, bounded to safe absolute HTTP(S) under existing field policies. | A few URL regexes, browser-only autolinking, or a homemade punctuation parser fail balanced punctuation, code/link boundaries, Unicode, and server/browser consistency. | One direct preview-runtime dependency and its transitive Unicode data dependency; lock/license/package review required. No fetch/unfurl feature is introduced. | 04 |
| D07 / accessibility | A default visible focus treatment applies to every keyboard control; deliberate component-specific overrides may enhance, never remove it. | Adding selectors for only today's missing buttons repeats the same failure for the next control; pixel parity cannot justify invisible focus. | Intentional visual deviation where Discord focus equals idle; focus must remain legible in forced colors and zoom. | 03, 05–08 |
| D08 / reliability | Separate transport health, publication state, render readiness, presentation completeness, and recovered event history. A healthy read clears only the active transport failure. | Clearing all diagnostics on success hides missing assets/callback failures; keeping recovered errors active lies about current state; replaying actions risks duplicate mutations. | More explicit state fields and a bounded event history, with protocol migration. Uncertain actions require inspection, not automatic replay. | 02, 07 |
| D09 / browser layout engineering | One post-layout scroll-intent reconciler handles target reveal, history anchoring, bottom-follow, and resize/media changes. | A guessed extra 40 px or unconditional scroll-to-bottom fixes one screenshot while moving users reading history and breaking delayed-media cases. | Explicit scroll policy and layout observation are required; targets taller than the viewport reveal their start and remain scrollable rather than pretending to fit. | 03 |
| D10 / accessible form design | Shared select state distinguishes draft/committed values; invalid commit preserves draft and exposes associated, announced min/max guidance. | Silently returning false, sending invalid values for Python to reject, or closing/discarding the draft gives no correction path. | Extra validation state and guidance; it must not falsely make a valid capture incomplete or invoke a callback. | 05 |
| D11 / responsive visual design | Define a compact thumbnail spoiler treatment within the existing media primitive; keep the short label unbroken and the reveal target accessible. | Global smaller text, clipping the word, or per-fixture offsets damage other media and fail localization/zoom. | A size-specific visual variant needs its own reference measurement; exact typography is still subject to substitute-font limits. | 09 |
| D12 / assistive technology | A stable concise live status announces meaningful error/recovery transitions; diagnostics history is a separate navigable region. | Marking the whole rerendered diagnostics list live or focusing every error creates repeated interruptions and steals modal focus. | Human screen-reader work is a release gate; automated ARIA checks alone cannot close the finding. | 07, 11 |
| U01 / information architecture | Structured authorized summaries identify author, time, ID, content kind, and meaningful safe excerpt; content-free V2 has a component summary. | Longer raw excerpts still collide, expose syntax/IDs, split graphemes, and cannot identify attachments/component-only messages. | New summary schema and token-derived text; message IDs are visible locally but excluded from share-safe reports. | 02, 06 |
| U02 / navigation and AX | A keyboard-accessible searchable navigator with bounded server results, previous/next, exact-ID jump, and stable selection. | A prettier unbounded native dropdown or client-side filter over the entire channel remains costly and ambiguous; hidden-history lookup can disclose data. | Search/paging requires authorized queries and stale-query handling. Plain deterministic search is provided, not a fuzzy-ranking service or Discord application clone. | 02, 06 |
| U03 / causal provenance | Correlate real interaction outputs to the admitted request and surface an explicit View response/followup affordance without auto-retargeting. | Channel diffs, last-message guesses, or replaying callbacks misattribute concurrent output and violate at-most-once behavior. | Bounded per-page receipts, no retained message bodies; no-output/deferred/uncertain cases remain explicit. Later followups become visible only on explicit publication. | 02, 06 |
| U04 / interaction semantics | Acquire variant-specific client traces before changing commit/cancel rules; expose constraints/draft state and accessible help outside the calibrated Discord surface. | Inventing an Apply button inside Discord components or assuming all message/modal selectors share transitions confuses local ergonomics with fidelity. | Reference access is a prerequisite for final parity. Inspector-owned commit/cancel assistance is labeled SimCord behavior, excluded from message crops, and uses the same state machine. | 01, 05 |
| U05 / product and metrology | Named Responsive/Exact modes, actual-size readout, editable exact profile/presets/reset, and explicit Use current viewport for a capture recipe. | Ambiguous “Fit” or numbers that silently mix host, render, and capture dimensions destroy reproducibility. | Responsive is not a pixel-parity profile; exact mode may scroll the host. Capture remains a separately chosen deterministic profile. | 03, 08 |
| U06 / inspector UX | Inspector outside the viewport, collapsed when empty; concise current-state feedback and expandable structured detail. | Permanent empty panels and protocol jargon waste space; moving inspector widgets into the Discord crop changes the product being compared. | Inspector layout is SimCord-owned, not Discord-owned. On narrow screens tools use an accessible drawer with focus restoration. | 03, 07 |
| U07 / security-conscious DX and AX | Stable structured active diagnostics plus bounded history, actionable remediation/subject links, and a separate allowlisted share-safe report. | Dumping snapshots, raw HTTP/exception strings, or regex-scrubbing arbitrary data can leak capabilities, private text, file paths, and modal handles. | Share-safe reports intentionally omit content and identifiers. Full authorized local debugging remains available through Python/snapshot and `env.errors`, not public exports. | 02, 07 |
| U08 / API and workflow design | Page-local layout switching; a Capture panel explains exact managed versus live-page capture and generates a real Python recipe with explicit viewer/target/profile. | A fake browser PNG button, screen-recording permission prompt, or a second remote renderer cannot honestly capture local drafts while preserving pinned isolation. | PNG capture remains Python/Playwright-owned; current open-menu/draft screenshots use the existing agent/browser tool on that page. This boundary is visible, not a hidden omission. | 03, 08 |
| U09 / temporal semantics | Publication time/revision, simulated presentation time, and connection-read health have distinct names; Refresh explicitly settles/publishes. | Poll-success timestamps called “fresh”, an unexplained “current” badge, or automatic refresh on every poll misrepresent the world and alter scenario timing. | No claim that unseen backend mutations are detected. UI says “Published snapshot; backend changes require Refresh”. Real-clock age is informational, not deterministic screenshot state. | 02, 07 |

## 3. Settled target contracts

All names in this section describe **proposed** protocol-3/API behavior, not current implementations.

### 3.1 One clean protocol-3 cutover

Package 3.0 and protocol 3 are independently versioned. Protocol 3 is justified by the combined navigation/candidate query, result-receipt, diagnostics, freshness, and presentation contracts—not by CSS fixes.

Keep the existing full-message map, timeline, authorized 50-message channel window, explicit targets, scoped control keys, asset ownership, and action sequence/generation envelope. Change these contracts together:

1. `messageIndex` is **one bounded navigation page**, never the entire channel. Each summary contains `id`, authorized author identity/name, creation/edited time, safe `excerpt`, content-kind summary, component-kind/label summary, attachment count/kinds, and ephemeral state only when visible to that viewer. Derive excerpts from existing parsed tokens, not Markdown regex stripping; do not reveal spoiler bodies in previews/search unless that field's presentation contract permits them. Use grapheme-safe presentation truncation; raw source is not a display summary.
2. `navigation` carries normalized query/filter, authorized previous/next availability, and opaque page-scoped cursors. Query length is at most 128 Unicode code points; results at most 50. Search is documented NFC/case-folded plain-text matching over authorized summary fields; exact snowflakes use the same non-disclosing authorization path. No regex query language, fuzzy ranking, remote lookup, or global entity search.
3. Replace eager `candidates` arrays with **control-scoped candidate descriptors** containing type/filter, independently resolved selected/default entries, current bounded result page, and explicit loading/available/empty/unavailable states. A selected value is not pruned just because it is outside the loaded option page. Authorized defaults count against the component's min/max, not the query's page size.
4. Add `publication` with real UTC `publishedAt`, existing `publishedRevision`, and publication reason. Preserve simulated `profile.presentationTime` separately. Reads never publish or settle bot work; Refresh and explicit navigation/presentation requests publish only their intended pages, except Python `preview.refresh()` retains its existing all-page publication contract.
5. `lastAction` becomes a typed receipt: request/sequence, admitted target/control reference, dispatch/settlement/acknowledgement, uncertainty flag, settled revision, safe diagnostics, and authorized response/followup/source-edit references with explicit kinds. Record real interaction provenance; ordinary composer/edit/delete results come from the actor operation, not interaction acknowledgement.
6. `activity` contains at most **20 recent action receipts per page**, without message bodies, upload data, or asset bytes. Keep existing latest-action replay semantics independently; do not turn activity into a durable transaction log. Page lease, viewer change, access revocation, and close clear/redact it. Eviction is visible as bounded recent history, not loss of current settlement.
7. Replace loose diagnostics with typed allowlisted records: stable ID/code/category/severity, current versus recovered state, completeness impact, safe message/remediation, optional authorized subject/control path, and correlation. Codes/messages are catalog-owned; arbitrary exception/HTTP/body text is not wire data. Python retains underlying exceptions through existing error facilities.
8. `window.simcordPreview` remains immutable and read-only. Publish structured navigation, select draft/commit status, transport health, publication, actual render/host geometry, overflow/scroll visibility, pending/last action, readiness, completeness, and diagnostics. Do not add an evaluate endpoint, mutable window API, or generic RPC for agents.

The JSON schema is authoritative for snapshots, action receipts, query responses, and browser status/report shapes. Use reusable `$defs` within the existing packaged schema and schema-versioned report fields; do not maintain independent undocumented JSON dialects. Validate fixtures in tests; runtime construction stays explicit/allowlisted rather than adding full-schema validation to every poll.

**Atomic migration:** server, bundled JS, Python snapshot/capture reports, schema validators, fixtures, scripts, examples, docs, and package/static allowlist migrate in commit 02. Reject protocol-2 pages with a safe reload/version error before dispatch; no aliases, dual renderers, or protocol adapters. Preserve working width/height Python keywords because their capture/profile meaning remains useful; breaking their spelling adds no correctness benefit.

### 3.2 Authorized bounded queries—not guild/history dumps

Implement concrete navigation and candidate queries through the existing authenticated page/action boundary, not a new unauthenticated search service:

- `browse_messages` selects a query/filter/keyset page; `browse_candidates` selects one active message/modal control's candidate page. These are explicit user-driven page-presentation operations, serialize with page changes under the existing operation guard, and publish a new projection. They never invoke a bot callback. Polling remains read-only.
- Use the current page viewer/channel/generation and observed revision; candidate requests additionally bind to an authorized active control and modal handle/source. Cursor binds query, ordering, scope, and generation; invalid/stale cursors fail safely, never silently fall back to a different viewer or unfiltered data.
- Use deterministic keyset ordering and an ID tie-breaker, not mutable array offsets. Navigation defaults to chronological message order; candidate inspector search uses normalized visible label plus typed ID. Client-parity candidate order, where different, comes from the reference transition contract in commit 05.
- Iterate existing backend data and reuse permission/identity/component-filter logic. Return only bounded results. Do not retain a second full-message/full-guild cache or send all eligible entities to every browser. Opening/searching is a deliberate publication; typing/paging does not secretly claim an old render snapshot represents newly queried backend state.
- Coalesce superseded **presentation query** requests in the frontend; never coalesce/retry mutating actor actions. Install a query response only if viewer, generation, control, and query intent still match. Loading is different from an empty authorized result.
- Recheck authorization on every query, cursor use, selection admission, response navigation, and asset read. Denied/deleted targets have the same unavailable shape. Totals/cursors/diagnostics must not leak inaccessible membership or history.
- Exact-ID jump may focus any authorized message in the configured channel, bringing its bounded history window into view. It is not arbitrary channel navigation. Other pages and Python presentation are untouched.
- Preserve the existing request-body caps, 16-page ceiling, 600-second lease, 128 MiB media cap, and upload policy. At most 50 rows/query, 20 receipts/page; no message bodies in activity. Publication/search scan cost is linear in the current in-memory world; do not introduce an index until measurements show it is necessary. **Trade-off:** bounded network/DOM/memory growth, but potentially O(N) query work; if this ceiling matters, add a measured authorized publication index, not an unreviewed global cache.

### 3.3 Responsive browsing versus exact rendering

Introduce `display="responsive"|"fixed"` on `env.preview`, default **responsive** for new human pages. Existing `layout`, `width`, `height`, locale/timezone/time/assets/SKU/port keywords retain their domain meaning. Width/height configure the session's exact profile and managed-capture default, not a promise to shrink it to fit an inspector.

Each `_Page` owns its current layout and presentation profile; `Preview.layout` becomes the initial/Python presentation default, not a global switch that retargets all browser pages. `Preview.show()` and `Preview.snapshot()` retain their Python-presentation ownership. New human pages inherit defaults; existing pages are not changed by Python show or another page's tools.

A concrete `configure_presentation` operation validates and publishes that page's layout/mode/viewport. It is not an actor operation. ResizeObserver observes **available workspace**, not content size, and coalesces responsive dimension changes behind pending admitted actions. Prevent resize/publication feedback loops; do not let a late resize overwrite a newer target/viewer/layout intent.

- **Responsive:** render at the host workspace's available CSS dimensions, excluding inspector chrome, and display the effective dimensions. Modal header/actions stay inside that viewport; only its body scrolls. Width/height fields are readouts in this mode. The UI explicitly says this is a responsive preview, not the saved exact profile.
- **Exact:** render at the selected width/height with no flex shrink or transform. A separate host stage owns panning when space is insufficient. The inspector never changes render dimensions. Exact-profile presets are 320×700, 640×700, 960×720, and 1280×900 plus Custom; these are SimCord conveniences, not Discord-certified profiles unless separately registered.
- **Use available viewport** explicitly copies available dimensions to a fixed profile; **Reset profile** restores session width/height. **Use current viewport for capture** selects those dimensions in the recipe without modifying Python/session defaults.
- Retain current bounds for explicit dimensions. When available host space falls below the minimum supported render viewport, expose the actual constraint and host scrolling; do not claim impossible fit. Required accessibility reflow scenarios use supported CSS widths, including 320 px and 400% zoom on a sufficient host.
- No automatic/manual transform-based display zoom in this series. Native browser zoom remains supported. A named scale feature can be reconsidered only with hit-testing/popover/text-size evidence; it is not necessary to close these issues.

Four scroll owners, each with a purpose: host stage (exact-mode host overflow), focused message content, channel timeline, and modal body. Do not make the entire document and every wrapper scrollable. Popups use measured host/viewport/modal geometry and current top-layer handling without breaking modal focus containment.

For channel scroll, retain keyed DOM/media, record the visible authorized anchor before change, apply layout-affecting UI changes first, and reconcile after DOM/fonts/intrinsic media geometry is ready. Policies:

1. Explicit target navigation reveals the full target if it fits; otherwise reveals its start.
2. Older/newer history paging preserves the current visible anchor and offset.
3. New messages follow the bottom only when the user was already at the bottom; otherwise show a New messages affordance.
4. Responsive/composer/media changes preserve the appropriate anchor and recheck the focused target against the actual timeline bounds.
5. Deleted targets restore a deterministic surviving visible position, not an unauthorized neighbor.

Use a measured post-layout correction and targeted observation; no unconditional scroll polling, magic pixel offsets, or arbitrary sleeps.

### 3.4 Select behavior and accessibility

Use one concrete select state owner: closed/open, highlighted value, committed values, draft values, validity, pending request, and uncertainty. Extract select rendering/keyboard handling into `static/selects.js` while evolving it; keep state/action admission owned by app/page logic. Extract modal composition into `static/modals.js` if required for the select ownership cutover; component-tree dispatch stays in `components.js`. Update packaged static allowlists in the same commit. No circular imports or framework rewrite.

The API supplies selected/default entity identities independently of the current option page. Candidate publication prunes only **actually unavailable** selections, never off-page selections, and never dispatches because of pruning. A revoked field gets safe associated feedback. Python remains authoritative at submission.

Create a variant-specific transition table for string/user/role/mentionable/channel in message/modal placements, single/multiple, min=0/required, including open/navigate/choose/clear/Enter/Space/Escape/outside/blur/trigger-close/submit. Populate empirical commit/cancel/search/validation rows from authorized client traces before the parity implementation lands; do not assume the old fallback is Discord.

Independently of client parity:

- Show min/max and selected count; preserve invalid drafts and associate errors via stable label/description IDs.
- Announce attempted invalid commit once; no backend request is admitted for an invalid count.
- Message selection dispatches exactly once after a valid commit. Modal selection stays local until modal submit.
- Pending disables duplicate commit; transport failure distinguishes not-admitted from admitted/uncertain. Never automatically replay.
- Inspector-owned “Apply selection” / “Cancel draft” assistance, when needed for the documented fallback, is labeled SimCord and operates through the same transitions. It is outside the emulated crop, not a fabricated Discord button. Authorized traces determine whether the emulated control itself has a commit affordance.
- Escape closes a popup before its modal; Tab/Shift+Tab containment and focus return survive rerenders, candidate loading, validation, layout switches, and opener deletion.
- A global focus-visible baseline covers every new and existing inspector/component/composer/history/lightbox control, including forced-colors mode. Focus visibility is not removed to match a screenshot.

**Evidence trade-off:** implementation of non-empirical validation/authorization can proceed without private references; closing select/modal parity requires the missing traces. Do not release the guessed fallback as certified, or call this plan complete while that gate is outstanding.

### 3.5 Text, emoji, and safe URL parsing

Reuse `_markdown.py` field policies and token types. Declare `linkify-it-py` directly in preview/screenshot extras, update the lockfile and dependency notices. Configure fuzzy domains/emails off and unwanted protocols off; emit only validated absolute HTTP(S) bare links under link-enabled policies. Parser ownership ensures code fences/spans, explicit destinations, literal fields, escaping, and nested Markdown do not become a second browser regex pipeline. Preserve existing masked links and `-#` behavior. No remote fetching or automatic embed preview.

Use the packaged color-emoji face for explicit emoji descriptors in buttons/options/chips/reactions. For mixed ordinary text, use an audited fallback stack that reaches the emoji face before broad script fallbacks can intercept emoji, while keeping the primary Latin/mono face first. Preserve native shaping, grapheme clusters, VS15/VS16 text/emoji intent, ZWJ, modifiers, regional indicators, and keycaps. Do not blanket-force color emoji for all punctuation or render all text in an emoji face. Actual browser glyph proof—not font-family strings or `document.fonts.check` alone—must validate this choice. If the chosen fonts intercept a required sequence, revise the licensed font/range configuration before accepting the commit; do not compensate with letter spacing or a per-fixture span hack.

Browser summary truncation uses `Intl.Segmenter` on the supported browser. Server-generated summary excerpts use `regex`'s maintained Unicode `\X` grapheme implementation, declared as a direct lazy dependency in preview/screenshot extras and pinned through the lockfile. The current runtime dependency inventory has no equivalent implementation; Python string slices plus a hand-written ZWJ rule are rejected. **Trade-off:** a second narrowly justified text dependency with a Unicode-version update obligation; consistent text boundaries are preferable to corrupting emoji/script clusters. Do not use this dependency to replace the Markdown parser.

Required corpus: ordinary Latin, Arabic/Hebrew mixed-direction text, CJK, Devanagari; sparkle, face, profession ZWJ, skin tone, flag, keycap, VS15/VS16 symbol; message, button label/emoji, select option/chip, modal label, and copyable/searchable summaries. Document licensed substitute artwork and supported font versions.

### 3.6 Results, freshness, diagnostics, and share-safe export

Build receipts from admitted `_Action` / real Interaction / actor outcomes. Distinguish original response, followup, source-message edit, modal opened, no output, deferred, rejected, failed, timed out, and cancelled. A deferred response is not a failure; a timeout is not proof that nothing changed. For delayed followups, retain only the bounded interaction references needed to resolve outcomes on explicit publication, expire them with the page/activity limit, and never invent background/live synchronization.

Reauthorize every output reference on read and on View activation. Deleted or denied outputs are unavailable without disclosing IDs/names/counts to unauthorized viewers. User-generated concurrent bot output must never be attributed by channel diff. Activity never auto-focuses a page, another viewer, Python target, or a capture. View response uses the existing explicit focus action; Back returns to the invoking control when it still exists.

Transport health updates on every authenticated read, even when the snapshot revision is unchanged. A healthy read resolves the active poll error but does not clear a genuine presentation diagnostic, bot action failure, stale publication status, denied access, or failed font/media readiness. Keep readiness, authorized content completeness, and connectivity as separate facts. A capture requires healthy access/read/render conditions even if an otherwise complete cached snapshot exists.

Publication UTC time is server wall time; simulated presentation time remains deterministic. UI may display elapsed publication age outside the crop, treating clock skew as unknown rather than asserting false freshness. `lastSuccessfulRead` is local connectivity metadata, not “last backend update”. Do not add automatic refresh, a global mutation observer, or a websocket broker. Refresh settles/publishes and returns an observable receipt/revision; successful refresh and successful poll recovery both reconcile transport health.

Diagnostics have stable active/resolved lifecycle and bounded history (20 local transport events plus the 20 backend action receipts, with explicit scope). Avoid unbounded per-error-string accumulation. Human summary is concise; expanded details show severity, code, affected feature/control, safe remediation, and relevant request/publication state. Clicking an authorized subject may focus it only on explicit user activation; no auto-focus for poll errors.

A stable `role="status"` region politely announces meaningful recoverable failure/recovery transitions once; access loss that prevents continued interaction receives an appropriately urgent announcement. Do not make the entire list live or repeat unchanged diagnostics each poll. Keep modal focus and drafts intact.

**Two different data products:**

- Local snapshot/browser status: authorized structured state, including content/identifiers necessary to operate the page. Private; never advertised as share-safe.
- Copy/Download support report: an allowlisted, schema-versioned report containing runtime/protocol/renderer versions, mode/dimensions, publication revision, safe diagnostic codes/severity/state/remediation, safe structural paths, and server-generated non-secret correlation IDs. Exclude URLs/capabilities, request headers, context/modal handles, viewer/channel/message IDs, names, message/label/filename/query/draft content, blobs, raw exception/HTTP text, absolute paths, and arbitrary `detail` dictionaries. User-supplied request IDs are not trusted as safe report text.

The report carries no hidden payload or PNG. Sharing visual captures is a separate explicit action: screenshots may contain private content even without a capability. This restriction costs convenient raw debugging; direct developers/agents to `env.errors` and private authorized snapshots for deeper local inspection. Test exports using deliberately sensitive/malicious exception strings and user data, not just a happy report.

### 3.7 Capture and layout discoverability

Add page-local Message/Channel layout selection to `configure_presentation`; migrate all `self.layout` global assumptions in history/projection/admission to the relevant page layout. Switching layout preserves selected target and compatible drafts, closes invalid popups, re-establishes scroll/focus ownership, and does not retarget another page or invalidate an unrelated capture. Changing viewer still clears private state.

The Capture panel offers:

1. **Managed deterministic capture recipe** using the existing Python API, explicit target/viewer, layout, mode/media time, and selected dimensions. Add `viewport: tuple[int, int] | None = None` and `layout: Literal["message", "channel"] | None = None` to `Preview.screenshot` for per-capture overrides; `None` uses session width/height and the Python presentation's layout respectively. Validate both dimensions and the layout before allocating, propagate only to the pin/profile, and never mutate a human page or session defaults. Viewer/target snowflakes use the existing authorized resolvers. Generated code is labeled executable only inside the active scenario with `preview` in scope; session IDs are not portable scenario definitions. When a human modal is open, a source-message recipe is explicitly labeled as such: it cannot recreate that modal or its drafts from a message ID. Capturing the open human modal uses the live-page route below; a managed modal capture still requires its actual opener-owned `InteractionResult`.
2. **Capture this live page guidance** for the existing browser/agent screenshot tool, with immutable `window.simcordPreview` readiness/action/renderGeneration checkpoints. This is the route for open popups, drafts, validation, and scroll states. Managed capture intentionally does not inherit them. No placeholder Download PNG button or server screenshot endpoint.
3. The share-safe support report from section 3.6, clearly distinct from a private capture recipe/snapshot.

`mode="viewport"` captures the exact logical viewport without inspector chrome. `mode="surface"` captures the visible target/dialog **intersection with its owning viewport**, not a full element bounding box outside the scroll region. Neither expands a modal or scrolls a human page. Capture geometry records logical viewport, content extent, visible crop, scroll offset, overflow directions, output size, and `scope="visible"`. Cropping by an intentional viewport is not missing implementation, but may not be represented as “the entire message is present”. `complete` describes information required for the declared capture scope; it does not mean all scroll content is pictured. Update tools that relied on the old bounding-box semantics in the same owning commit.

No full-scroll composite mode is added. **Trade-off:** one image does not certify every scroll position; top/middle/bottom live-page recipes prove reachability. Full-scroll stitching would be a different artifact, not a Discord viewport comparison.

## 4. Ordered commits to land

Each commit is a complete runnable vertical slice with its focused behavioral tests, actual browser smoke, documentation/changelog fragment, and consumer migrations where relevant. No scaffolds, fake capabilities, permanently xfailed bugs, or unfinished UI buttons. Tests named below are **existing files to extend**; new test functions/scenarios are proposed. Exact prose/helper wiring is not a regression contract.

C01 registers evidence and runnable scenarios; it must not introduce failing permanent tests before their owning fix or assert the broken behavior as desired. New failing-before/passing-after regressions land with their fix.

### Commit 01 — `docs(preview): register dogfood acceptance and reference prerequisites`

**Dependencies:** none. **Owns:** inventory and U04 evidence acquisition.

**Files:** `tests/fixtures/preview/{catalog.py,coverage.json,states.json,profiles.json,measurements.json}`, `scripts/preview_dogfood.py`, this plan, existing reference tooling as needed.

- Register each D/U identifier against a concrete scenario/expected user outcome and owning commit; retain separate implementation/reference/comparison statuses.
- Fold the dogfood factories into the existing shared catalog where appropriate; the script remains a thin checkout runner, not a second fixture system. Keep original content demos, duplicate summaries, long content, 25-option boundaries, two viewers, and history stress.
- Register real modal entity default/required/optional/all-family cases, unsupported/denied candidates, result followups, one-poll failure, profile modes, exact-ID jump, keyboard/zoom/forced-colors, and sensitive export inputs.
- Acquire authorized Discord traces for per-variant select commit/cancel/search/clear, modal validation, and candidate ordering. Acquire missing measured thumbnail/gallery/button/modal profiles. Store private images/recordings outside the repository; commit only sanitized measurements/provenance/recipes.
- Missing trace rows remain precise external prerequisites with owner and required input; do not invent transitions or mark comparisons passed. Independent functional commits can land while reference acquisition runs; commits 05/09 and release gates cannot claim final calibration before their evidence is available.

**Acceptance:** catalog/coverage tooling checks run; gallery launches in message/channel modes; each of 21 IDs maps to a real scenario; sensitive URLs are not in saved output. Existing completed reference/coverage rows are not reset to planned.

**Trade-off:** evidence acquisition requires maintainer/client/asset access; it is not replaced with screenshots of the preview itself.

### Commit 02 — `feat(preview)!: publish scoped protocol 3 navigation and outcome contracts`

**Dependencies:** 01; no private pixel measurements needed for authorization/provenance.

**Owns:** D01 backend and complete usable modal path, U01 data, U02 bounded queries, U03 receipts, U07 safe wire data, U09 publication fields; foundations for D08/D10.

**Files:** `_snapshot.py`, `_pages.py`, `_actions.py`, `_server.py`, `preview/__init__.py`, `protocol.schema.json`, `static/{app.js,components.js}`, capture/tooling consumers and all snapshot validators; relevant guide/API/migration fragments.

- Implement section 3.1 atomically, including every newly exposed backend/query operation's functioning JS consumer. The existing page can remain visually basic, but all controls/results must still function.
- Use one annotated authorized tree traversal for message/modal candidates. Resolve defaults separately from options pages. Exercise actual guild and DM modal selections, with no message selector present.
- Replace unbounded index/candidate payloads with bounded presentation operations and typed loading/unavailable states. Implement stale scope/cursor rejection and query intent isolation.
- Correlate admitted interaction/actor outcomes, bound activity, and reauthorize result references. Preserve replay receipts and exact dispatch/settlement/acknowledgement distinctions.
- Publish typed safe diagnostics and publication UTC metadata. Remove raw error/body serialization at the wire boundary; underlying exceptions remain available locally. Do not change existing per-publication revision increments.
- Migrate every protocol-2 consumer, schema/JS guard, root-field assumption, fixture helper, and capture script in one clean cutover. Include the breaking-change fragment now, not only at release.

**Focused verification:** extend `test_preview_dx.py`, `test_preview_fixes.py`, `test_preview_channel.py`, `test_preview_security.py`, and applicable `test_preview_additional.py` cases. Validate schema; all four modal entities submit actual handles; valid defaults off the first page survive; identical custom IDs never collide; denied/default/response/cursor access does not disclose private data; each query is bounded; replay invokes one callback and yields the same receipt. Live browser smoke opens every entity modal field and submits a real callback; compare Alice/Bob pages.

**Trade-off:** this is a cohesive larger contract commit. Splitting server/schema/client into incompatible intermediate commits would be worse. Protocol-2 readers must migrate; no compatibility adapter remains.

### Commit 03 — `feat(preview)!: separate responsive presentation from exact capture profiles`

**Dependencies:** 02. **Owns:** D02–D04, D07 baseline, D09, U05, U06 shell, U08 layout switching.

**Files:** `env.py`, `preview/{__init__.py,_pages.py,_snapshot.py,_actions.py}`, `static/{index.html,app.js,preview.css}`, schema/status definitions, profile/channel/browser tests.

- Add the display keyword and page-local `configure_presentation` behavior from section 3.3; include working layout switching, mode controls, presets/reset, effective/profile dimensions, and minimal capture-dimension handoff.
- Replace shrinkable preview/inspector flex coupling with host stage plus correctly owned scroll regions; compact/collapsible inspector chrome fits before computing responsive viewport. Native zoom/reflow stays readable.
- Keep fixed logical dimensions exact. Responsive mode explicitly changes the page's logical viewport and reports it; neither mode changes session capture defaults.
- Remove the blanket focus-outline suppression and establish a reusable global focus-visible baseline for all current/new controls, with high-contrast support.
- Implement and smoke the post-layout scroll intent policies; first reproduce/trace D09 timing because its hypothesized cause was an inference. Test delayed media/fonts, history buttons, growing composer, resize, target deletion, and bottom-follow versus reading history.
- Retain modal focus/inert boundaries and correct popup positioning across the new scroll owners.

**Focused verification:** existing channel/capture/browser edge suites. At 360×640, 800×600, 1280×800, and zoom/reflow, responsive Submit/Send/tools are reachable; exact mode reports/measures the selected dimensions even beside inspector; long messages scroll to the final control; no new nested-scroll trap; latest focus is above composer; earlier-history readers are not moved by new output; page A's layout/resize never changes page B/Python/capture.

**Trade-off:** default browser presentation changes for 3.0; exact mode reproduces explicit old-profile intent without a deprecated compatibility path. Responsive screenshots and exact profiles are different artifacts, labeled accordingly.

### Commit 04 — `fix(preview): render Unicode and safe bare links through the shared text pipeline`

**Dependencies:** 02–03 for final summary/profile consumers. **Owns:** D05, D06, U01 readable/grapheme-safe summaries.

**Files:** `_markdown.py`, `_snapshot.py`, `static/{text.js,components.js,messages.js,preview.css}`, font metadata as needed, `pyproject.toml`, `uv.lock`, optional-dependency/license/package docs.

- Implement section 3.5 with declared supported linkification and grapheme dependencies; retain lazy base-package imports.
- Apply actual emoji face selection to explicit descriptors and mixed text fallback; do not add an emoji scanner or per-fixture replacements.
- Derive safe human excerpts/component summaries from tokens; preserve mentions as authorized readable labels, spoiler secrecy, code literal boundaries, and complete graphemes.
- Preserve existing `-#`, masked links, safe HTML/URL restrictions, bidi/script shaping, and code highlighting.

**Focused verification:** existing preview security/fonts/identity/browser suites. Safe bare links plus balanced punctuation, adjacent masked links, Unicode paths, and escaped/code/literal/unsafe-scheme negatives; real anchors inspected without following external navigation. Installed-wheel glyph/font inspection and visual corpus across text/control/summary surfaces; copied strings retain complete emoji clusters.

**Trade-off:** additional runtime text dependencies and Unicode-version maintenance, deliberately chosen instead of incomplete custom parsers. No remote unfurling and no exact Discord emoji-art claim.

### Commit 05 — `fix(preview): implement explicit accessible select state and validated commits`

**Dependencies:** 01–04; required select/modal transition traces from 01.

**Owns:** D01 candidate UX closure, D10, U04, D07 select/modal coverage.

**Files:** `static/{selects.js,components.js,modals.js,app.js,preview.css}`, static allowlist, `_snapshot.py`/`_actions.py` only for genuine contract adjustments, states/coverage ledger, relevant browser suites.

- Extract concrete select/modal responsibilities during the functioning cutover; keep one draft/action state owner and safe one-way imports.
- Implement the variant-specific observed transitions, explicit count/constraint guidance, selected identities independent of loaded pages, loading/no-results/unavailable states, and stale-query handling.
- Invalid attempted commit stays local with field-associated announced feedback and recoverable draft. Modal defaults/false/allowed-empty values remain effective state at submit.
- Add labeled inspector draft/commit/cancel assistance where the project fallback requires it; never inject invented client controls into the calibrated crop.
- Handle pending, stale candidate/control, revocation, rejected admission, and uncertain settlement without another automatic dispatch.

**Focused verification:** all five select families in message/modal placements, min=0/required/single/multi/min/max, actual defaults on later option pages, Space/Enter/Home/End/arrows/Tab/Escape/outside/blur, viewport flip/clamp, and modal focus return. Assert actual callbacks/values and exactly-once dispatch, not only chip text. Human keyboard smoke with short modal viewport; reference trace comparison records any intentional accessibility differences.

**Trade-off:** no guessed commit parity. If authorized traces are unavailable, independent validation work may be exercised, but this commit's full acceptance and release closure remain blocked—not silently reduced to a fallback.

### Commit 06 — `feat(preview): add authorized message navigation and causal result discovery`

**Dependencies:** 02, 04–05. **Owns:** U01–U03.

**Files:** `static/{index.html,app.js,preview.css}`, a concrete `navigation.js` module if extracting the new navigator, `_snapshot.py`/query helpers as needed, channel/result/browser tests.

- Replace the native Message dropdown as the sole navigator with a labeled searchable result list/dialog, distinct summaries, keyboard current/selected state, previous/next within the chosen ordering, bounded paging, and exact-ID jump.
- Query changes are explicit publications; user selection alone changes target. Retain query/selection during healthy polling; discard stale viewer/generation results.
- Present recent action outcomes with response/followup/source-edit/no-output/deferred/uncertain distinctions. View and Back navigate explicitly; preserve the invoking control when it still exists.
- Use normal authorized focus/history windows for output outside the current timeline; no extra unbounded body cache and no automatic focus changes.

**Focused verification:** duplicate DOG-06 messages, content-free V2, attachment-only messages, identical author labels, long grapheme excerpts, >=1,000-message world, query/cursor boundaries, deleted/denied exact IDs, fresh ephemeral followups, concurrent unrelated bot output, timeout-with-mutation, and two independent pages. Agent workflow can identify the correct message/output from structured summary/receipt plus stable control keys, not raw DOM indices.

**Trade-off:** deterministic plain search and bounded recent activity, not fuzzy search/global channel browsing. Moving to a result is intentional and never implicit.

### Commit 07 — `fix(preview): reconcile health diagnostics freshness and accessible support reports`

**Dependencies:** 02–06. **Owns:** D08, D12, U06–U07, U09.

**Files:** `static/{app.js,index.html,preview.css}`, a concrete diagnostics/report module if needed, safe error catalog/schema, server result mapping where required, browser/security/DX tests.

- Implement the independent health/readiness/completeness/publication/history state and recovery lifecycle in section 3.6. One healthy unchanged read resolves transport failure without re-rendering unrelated controls or clearing other issues.
- Expose publication time/revision and explicit Refresh meaning; report unknown unseen-backend freshness honestly. Check Refresh request admission, success receipt, returned revision, and transport reconciliation, not merely button clicks.
- Provide concise human status, collapsed-empty inspector, active/resolved filters, remediation/subject navigation, restrained live announcements, and copy/download allowlisted support report.
- Remove raw `String(error)`/HTTP body/exception serialization paths from browser-visible reporting; do not hide failures. Keep underlying diagnostic cause locally available through existing Python facilities.

**Focused verification:** injected one-read failure followed by unchanged healthy responses; persistent missing asset/font diagnostic; callback failure and uncertain admission; denied access; manual Refresh after a backend mutation; sensitive/malicious inputs in every export field; no capability/identifier/content/absolute path in share-safe output; inspector/modal focus preserved. Human screen-reader failure/recovery walkthrough is recorded separately from automated live-region assertions.

**Trade-off:** reports are share-safe summaries, not full snapshots. No callback replay, no live-sync broker, and no claim that a poll proves backend freshness.

### Commit 08 — `feat(preview)!: expose truthful capture recipes and visible crop geometry`

**Dependencies:** 02–07. **Owns:** D02/D03 capture closure, U05/U08 capture workflow.

**Files:** `_capture.py`, preview public API/schema, `static/{app.js,index.html,preview.css}`, capture runner/comparator consumers, examples/guide/API, capture/package tests.

- Implement the per-capture `viewport` and `layout` overrides and Capture panel from section 3.7; generate working in-scenario Python snippets using existing authorized snowflake/viewer resolution. Explicitly exercise a browser layout different from the Python presentation so the recipe cannot silently capture the wrong surface.
- Capture visible owning-viewport intersections; preserve modal constraints, popup/live-page distinction, pin authorization, media time, cancellation, and atomic output.
- Report actual logical viewport, content/visible crop, scroll offsets/overflow, output dimensions, and scope; keep ready/complete/calibrated independent.
- Migrate old surface-bounding-box expectations/tools rather than introducing `legacy_surface`. Add working instructions for live-page states and safe-report versus private capture handling.

**Focused verification:** exact viewport output sizes independent of human host/inspector; long scrolled message surface cropped to visible intersection; constrained modal never expands; wrong profile is rejected before allocation; screenshots do not retarget/resize human pages; live open popup/draft capture uses the actual page; pin revocation/restart, cancellation, in-memory capture, and atomic destination behavior preserved. Execute the generated recipe in the active demo scenario and inspect its reported geometry/PNG.

**Trade-off:** Python/Playwright installation remains explicit; the page does not pretend it can download its own pixels. Visible crop semantics are a documented breaking correction, and full-scroll stitching is deliberately absent.

### Commit 09 — `fix(preview): calibrate compact spoilers and remaining component presentation`

**Dependencies:** 01, 03–08; required authorized geometry/reference data.

**Owns:** D11, D05 visual confirmation, outstanding family fidelity evidence retained from the earlier plan.

**Files:** media/text/component CSS and renderers as actually implicated, profiles/measurements/states/coverage, existing capture/comparison tools.

- Implement measured thumbnail versus ordinary-media spoiler treatment; the label never breaks its short word at supported widths/zoom, reveal remains keyboard-operable, and localized guidance does not rely on a fixed English width.
- Compare actual default-dark message/button/select/modal/embed/attachment/V2 states at named profiles, including 1–10 mixed-aspect galleries, narrow/wide wrapping, supplied premium metadata, open/selected/error/scroll states, and intentional focus/font deviations.
- Fix family-owned geometry/color/wrapping defects exposed by those comparisons, not per-fixture offsets, blanket image resizes, masks covering defects, or line-height compensation for wrong fonts.
- Keep private reference images/derived comparisons local. Commit sanitized measurements, scenario changes, ledger status and reviewed non-image attestations only.

**Focused verification:** compact spoiler screenshot/reveal at 84 px accessory and ordinary cards, zoom/contrast; registered region/wrap comparisons reject wrong spacing/color/icon/missing-control mutations; all required states have explicit pass/fail/blocked status. A complete local PNG is not certification.

**Trade-off:** substitute glyph outlines and intentional accessible focus remain explicit deviations; missing evidence stays blocked. No widening to native mobile, other themes, or external-service simulation.

### Commit 10 — `test(preview): verify integrated human agent security and installed-wheel journeys`

**Dependencies:** 02–09. **Owns:** end-to-end regression and DX/AX acceptance for all findings.

**Files:** existing integration suites and helpers, fixture ledger/recipes, package/discoverability/CI checks where required, scrubbed evidence attestations.

- Run the full walkthrough matrix below against actual browser and real bot operations; assert causal outcomes and visible state, not source text/default prose/mock echoes.
- Include two viewers/two pages, slow asset/query completion, access revocation, deletion, restart/stale envelopes, timeout after mutation, cancellation/close, explicit-refresh behavior, immutable status, and pinned capture isolation.
- Run the installed wheel outside the checkout with sparse OS fonts and offline network, all declared extras/packaged static dependencies, and optional-dependency negative cases. Base `import simcord` stays lazy.
- Obtain human accessibility review on supported browser/AT for keyboard, navigator, draft/error/recovery, modal, composer, reaction/poll controls retained from the prior scope, zoom and forced colors. Record browser/AT versions, steps and observed results.
- No private Discord images, credentials, sensitive exports, or platform-dependent “looks fine” baselines in CI.

**Acceptance:** all 21 findings' user-visible criteria and preservation gates pass; no reference-blocked row is mislabeled complete/calibrated; comparator sensitivity and installed-wheel parity are observed. Coverage is meaningful behavior, not a claim that tests prove all possible regressions absent.

**Trade-off:** automation proves functional/security/render consistency; human AT and private Discord comparison remain separate evidence. Do not substitute one for another.

### Commit 11 — `docs(preview)!: publish the 3.0 workflows migrations and release attestation`

**Dependencies:** 10 and all external release evidence gates.

**Files:** `docs/{guides/preview.md,api.md,concepts.md,parity-matrix.md,stability.md,llms.txt,preview-attestation.md}`, `README.md`, examples/readmes, applicable `changes/` fragments, this plan and audit disposition links.

- Consolidate migrations already documented in their owning commits: protocol 2→3, bounded index/candidates/queries, typed receipts/diagnostics, responsive default versus exact profile, page-local layout, capture override and visible crop semantics.
- Publish executable human and agent journeys, including explicit publication, causal output navigation, invalid draft correction, private snapshot versus share-safe report, and deterministic versus live-page screenshot distinction.
- Keep D01–D12/U01–U09 disposition/evidence traceability; do not alter historical audit observations to make the implementation look successful.
- Remove superseded wire keys/paths/documentation and tests pinned to incidental old wording; retain no compatibility renderer/export path. Keep reference catalog and useful dogfood runner.
- Publish truthful ready/complete/calibrated claims and known legal/font/resource/offline deviations. No unqualified “full Discord parity”.

**Acceptance:** examples and generated capture snippet run from installed wheel; docs build strictly; discovery/package checks pass; every breaking behavior has a before/after migration; no orphaned schema/JS/static consumer; every issue has final evidence and ownership. Version tagging/pushing/releasing is not authorized by this plan alone.

**Trade-off:** the release claim is precise and bounded, not a promise to track every changing Discord client or preserve protocol-2 clients indefinitely.

## 5. Dependencies and issue closure

Land commits in the numbered order for coherent review. Independent evidence collection and text/layout investigations can happen concurrently, but no consumer lands before its contract, and no cosmetic comparison certifies pre-font/pre-layout output.

| Finding | Required closure commits | Required proof |
| --- | --- | --- |
| D01 | 02, 05, 10 | All modal entities/defaults submit real values with scoped/revoked access |
| D02 | 03, 08, 10 | Final long-message control reachable; capture crop is truthful |
| D03 | 02, 03, 08, 10 | Requested/effective/host/capture dimensions agree with their declared mode |
| D04 | 03, 10 | Narrow/short responsive modal and inspector controls visible/reachable |
| D05 | 04, 09, 10 | Actual glyph rendering/font proof and intact copied clusters |
| D06 | 04, 10 | Safe clickable bare URLs, code/literal/unsafe negatives |
| D07 | 03, 05–08, 10 | Keyboard-visible focus everywhere, including new controls/forced colors |
| D08 | 02, 07, 10 | Healthy unchanged read recovers transport without suppressing real incompleteness |
| D09 | 03, 10 | Post-layout target visibility plus preserved reading/history anchors |
| D10 | 05, 10 | Invalid commit announced, draft retained, zero dispatch; valid commit once |
| D11 | 09, 10 | Compact spoiler word fits and reveal works under zoom |
| D12 | 07, 10 | Human AT discovers meaningful error/recovery without focus theft |
| U01 | 02, 04, 06, 10 | Duplicate/component-only summaries are distinguishable and readable |
| U02 | 02, 06, 10 | Authorized bounded search/page/jump with keyboard navigation |
| U03 | 02, 06, 10 | Correlated results discoverable without misattribution or auto-retargeting |
| U04 | 01, 05, 10 | Observed transition contract, discoverable draft/correction/cancel |
| U05 | 03, 08, 10 | Explicit responsive/exact controls and reproducible chosen capture profile |
| U06 | 03, 07, 10 | Compact accessible inspector and useful human status |
| U07 | 02, 07, 10 | Actionable safe diagnostics; exported sensitive inputs cannot leak |
| U08 | 03, 08, 10 | Working page layout change and executable capture workflow, no fake button |
| U09 | 02, 07, 10 | Publication/read/simulated time distinct; actual Refresh publishes changes |

## 6. Verification and release gates

### Per owning commit

1. Re-read current source and relevant tests; preserve newer correct behavior. For exported APIs, use language-server references and migrate every consumer in that commit.
2. For the audited bug, use the existing reported failure as ground truth; establish a focused failing-before/passing-after behavioral regression where practical, without retesting merely to dismiss the report. D09's causal inference needs actual layout tracing, not a magic correction.
3. Run the real page, exercise the changed pointer/keyboard/actor path, observe backend outcome plus visible state/status, and inspect a screenshot. Tests alone are not visual evidence. Wait on revision/request/renderGeneration/readiness, not arbitrary sleeps.
4. Extend existing tests for consumer-visible boundaries, not incidental wording/defaults/forwarding. Each new trust boundary needs denial/revocation/stale-input coverage. Add no second framework.
5. Record scenario/profile/runtime versions, outcome, safe diagnostics, and comparison status. Private Discord references and sensitive page images remain local.
6. Update the owning API/docs/changelog fragment and delete the superseded path. Every commit must remain runnable; do not postpone migrations/cleanup to commit 11.

### Integrated walkthroughs

- **First-use DX:** install extras, launch gallery, responsive screen fits, discover modes/layout, find duplicate/V2 message, open/fill/submit entity modal, correct invalid multi draft, see actual response, inspect remediation, export share-safe report, run capture recipe, Close exits cleanly.
- **AX:** open authorized page; read immutable structured status; find message by summary/ID; perform semantic control actions; wait for matching request/settled revision and render generation; distinguish draft/pending/uncertain; locate causal response; capture exact logical profile or live-page popup intentionally. No mutable eval/control API or DOM-index guessing.
- **Metrology:** fixed 320/640/960/1280 widths at short/normal heights, responsive host 360×640/800×600/1280×800, inspector opened/closed, normal/zoom/forced colors, content taller/wider than viewport; exact capture dimension/crop metadata verified. No scale/resize/mask hides geometry failures.
- **Reliability/privacy:** one failed poll then healthy identical revision; ongoing missing asset; callback timeout with backend mutation; Alice ephemeral/Bob denied; deleted result/control; cursor/query races; restart/revocation; delayed font/media; capture/resize/isolation; one admitted callback on replay.
- **Text:** safe bare/masked links and punctuation/Unicode negatives; graphemes and bidi; code literal behavior; `-#`; actual emoji glyphs across all identified fields.
- **Accessibility:** all controls focus-visible; navigator/list names/current state; min/max/error associations; popup before modal Escape; modal containment/return; error and recovery announced once; keyboard scroll to last content; human screen-reader transcript/attestation.

### Existing commands to use when implementation lands

The commands are future implementation/release checks, **not checks claimed to have run for this planning-only change**. Install the repository's existing extras/browser explicitly; choose targeted existing test files during each commit, then run integrated checks once after integration.

```sh
uv sync --extra dev --extra screenshot --extra docs
uv run playwright install --with-deps chromium
uv run pytest tests/integration/test_preview*.py -q
uv run python scripts/discord_reference_bot.py --check
uv run python scripts/capture_visual_reference.py --check
uv run ruff check src tests examples benchmarks scripts
uv run ruff format --check src tests examples benchmarks scripts
uv run pyright src
uv run coverage run -m pytest tests examples -q
uv run coverage report
uv run mkdocs build --strict
uv run python scripts/check_discoverability.py site
uv build
uv run python scripts/check_package.py dist
```

Run the existing family/fixture capture and private comparison workflow against exact registered profiles. A missing required private pack/reference trace yields a blocked gate, not a passing calibration. Record comparator sensitivity and repeat-capture noise before setting region-specific budgets; whole-page similarity is not acceptance.

### External prerequisites: explicit, not absorbed

- Authorized current Discord web-client access and private bot/server/application for missing select/modal traces and geometry.
- Legitimately usable fixture avatars/emoji/media and active SKU presentation metadata where the broader parity matrix requires them.
- Human reviewers with supported browser/screen reader for the required accessibility journeys.

Reference/human gates can block **release**, not erase an issue or authorize guessed behavior. Finish independent reachable commits; record the exact blocked input/owner/state. No final implementation completion claim until all issue closures, preservation checks, and required evidence pass.

## 7. Migration and trade-off checklist

Before implementation is considered complete, explicitly publish all of these choices to users:

- Protocol 3 replaces protocol 2; bounded summaries/candidates are queried, not complete dumps.
- Responsive human-page default differs from fixed capture defaults; exact inspection is explicit and does not shrink.
- Page-local layout/profile configuration cannot mutate other pages or Python presentation.
- Surface capture is a visible viewport intersection, not expanded/full-message output; managed capture cannot inherit human drafts.
- Runtime link/grapheme dependencies are declared and Unicode/font versions are pinned/documented.
- Noto artwork and accessible focus can intentionally differ from Discord, with evidence-linked deviations.
- Activity/history and queries are bounded; query scanning remains O(N) until a measured index is justified.
- Polling is not live backend synchronization; no automatic callback retries or refresh broker.
- Share-safe reports sacrifice raw private debugging data; private snapshots/screenshots/recipes remain private.
- No browser-owned PNG service, full-scroll stitching, fuzzy/global search, transform zoom, native-mobile theme expansion, remote asset proxy, commerce simulation, or framework rewrite is part of these fixes.
- Private reference acquisition and human AT review remain real external work, not CI pass claims.

Any additional trade-off discovered while implementing must be recorded with the affected issue, expert objection, user-visible effect, migration, and verification. If it weakens an acceptance requirement, obtain an explicit scope decision before landing it. Do not silently turn a blocker into a smaller feature or add a compatibility path to avoid the clean cutover.
