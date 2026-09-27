---
title: "Preview evidence attestation"
description: "Reviewed local preview evidence, exact coverage counts, hashes, and outstanding Discord calibration prerequisites."
---

# Preview evidence attestation — 2026-09-27 UTC

**Result: local functional verification passed; Discord visual and human accessibility parity are not certified.**
This is a scrubbed record for renderer commit `820a041fd3edf651e379e2b05ac89663185d01fa`,
reviewed independently by `IndependentPreviewReviewer` on 2026-09-27. Five actionable
findings were corrected in that commit; two were rejected as pre-existing viewport
capture behavior and deliberately local required-select clearing. No human screen-reader
review or authorized private reference comparison has occurred.

| Artifact / environment | Version or SHA-256 |
| --- | --- |
| Snapshot and schema protocol | `2`; `protocol.schema.json` `965c7fbf2021e9e9b1f4420c151248a99f0a3c5db1231e36eb25ddf05e939469` |
| Renderer revision | `static/app.js` `3b0b3e96671e84cc21c622faedd53f957d6d3cf716ce4571dfdd8af0d5cb9401` |
| Package environment | Linux `6.8.0-90-generic`, Python `3.12.13`, `uv.lock` `4e7dd350ea8e20840a973d7e85f69fb56cfa5df6bc82f498af6c7ffee4ae583a` |
| Browser | Playwright `1.62.0`, Chromium `151.0.7922.34`, browser executable `0b20b130e7edd9dd51873be867761295fe0cfad490c2b9a64f95bd3cfc08fa71` |
| Installed-wheel smoke browser | Playwright `1.63.0`, Chromium `153.0.8010.12`, browser executable `8c599d43aec53f2460a31ae2f4af6bd863f8258b34ff519564bc5d4726bfaa1e` |
| Licensed font set | `static/fonts/manifest.json` `ef74a3641c6712480cc4c61007647af10b6b2d03874c2a1745669aab1f3f88bc`; individual face versions/hashes are recorded in that manifest and each capture profile |
| Authorized reference pack revision / hash | **Unavailable / unavailable**; no provenance-checked private pack was used |

The [coverage ledger](https://github.com/SilentHacks/simcord/blob/master/tests/fixtures/preview/coverage.json)
registers 94 fixtures in 14 families and 76 historical image hashes. Its three
independent status axes are: reference **76 available / 18 blocked**;
implementation **81 partial / 13 missing**; comparison **81 unrun / 13 not comparable**.
Visual matches **0**, reviewed matches with deviations **0**, failed comparisons **0**,
and certified profiles **0**. An `available` historical image is not certification-grade
without a provenance-checked pack, measured crop and compatible client profile.

Blocked reference IDs: `planned.profile`, `planned.identity`, `planned.message_context`,
`planned.reactions`, `planned.polls`, `planned.stickers_system`, `planned.markdown`,
`planned.embeds`, `planned.attachments_media`, `planned.v2`, `planned.buttons`,
`planned.selects`, `planned.modals`, `planned.access_lifecycle`,
`boundary.v2-content-rejected`, `boundary.v2-embeds-rejected`,
`boundary.v2-poll-rejected`, `boundary.v2-stickers-rejected`.
Not-comparable comparisons: nine historical `historical.ref.50.modals.*.dialog`
whole-window captures (choice, choice-bottom, entity, entity-bottom, text-empty,
text-filled, text-validation, upload, upload-empty) and the four `boundary.v2-*`
rows above. Exact per-row reasons and owners are in the coverage ledger. Unsupported
capture recipes are blocked at runtime rather than saved under a false state label.

**Explicit differences:** packaged licensed Noto fonts are not Discord's gg sans;
visible keyboard focus intentionally differs; media, avatars and SKU icons require
explicit authorized offline bytes; external provider/commerce services, account
navigation, login and native-mobile behavior are out of scope. Media decoding,
page count and screenshot raster have bounded limits; platform codec support varies,
and the worker memory ceiling is Linux-only. Unmeasured transitions, unknown client
provenance and human modal/select/reaction/poll screen-reader smoke are release blockers.

**Commands exercised:** `uv run pytest -q --tb=short` (867 passed, six
third-party deprecation warnings, after review fixes);
`uv run pytest tests/integration/test_preview*.py tests/unit/test_components_v2_validation.py -q --tb=short`
(302 passed); `uv run pyright src` (zero errors);
`uv run ruff check src tests examples benchmarks scripts` and
`uv run ruff format --check src tests examples benchmarks scripts` (passed);
`uv run --extra docs mkdocs build --strict` and
`uv run python scripts/check_discoverability.py` (38 HTML pages passed);
`uv build --out-dir /tmp/simcord-preview-reviewed-final` and
`uv run python scripts/check_package.py /tmp/simcord-preview-reviewed-final`
(fresh wheel and sdist verified after fixes);
installed that wheel into a fresh `/tmp` virtual environment and ran
`examples/preview_example.py` outside the checkout (`complete=true`, no diagnostics);
`uv run python scripts/capture_visual_reference.py --check`,
`--fixture historical.ref.20.buttons.idle`, `--fixture historical.ref.30.string.select.open.string.select.message`
(local captures succeeded); `uv run python scripts/compare_visual_reference.py --check-manifest tests/fixtures/preview/coverage.json`
(94 rows validated). The private reference comparison and human assistive-technology
checks were not run. See the [capture workflow](guides/preview.md#one-fixture-a-family-or-the-private-batch)
for the missing authorized prerequisites and exit-status rules.
