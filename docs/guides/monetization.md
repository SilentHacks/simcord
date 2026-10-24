---
title: "SKUs & entitlements"
description: "Test Discord premium access, startup reconciliation, consumable fulfilment, entitlement expiry and event replay offline, with explicit uncalibrated parity boundaries."
---

# SKUs & entitlements

SimCord simulates Discord resources and delivery, not your application's billing policy.
Plans, allowances, credits, staff grants, trials, memory limits, member spending caps and
financial retention belong to the application under test. There is no checkout, payment,
refund engine, Subscription reporting API or `SUBSCRIPTION_*` lifecycle.

## Arrange catalog and history

```python
import discord
import simcord

env = simcord.Env(bot)
guild = env.create_guild("Premium guild")
premium = env.create_sku("Premium")
seeded = env.seed_entitlement(premium, guild=guild)
await env.start()  # setup_hook can already enumerate seeded entitlements through REST
try:
    assert seeded.id in {item.id async for item in bot.entitlements()}
finally:
    await env.shutdown()
```

`create_sku` accepts `discord.SKUType.subscription` (default), `.subscription_group`,
`.durable` or `.consumable`. Groups are explicit catalog entries: creating a subscription
does not create a group, associate tiers or change visibility. Groups cannot be granted.
Generated slugs and IDs are deterministic simulator choices. There is no Free-plan SKU.

Flags are `discord.SKUFlags`. Subscriptions and groups default to
`guild_subscription=True`; use `flags=discord.SKUFlags(user_subscription=True)` for
personal entries. One-time SKUs default to `available=True`. Availability does not erase
existing access. Fixtures arrange authoritative events/state; they do not enforce checkout
eligibility.

Owners may be raw positive 64-bit snowflakes or same-Env handles (`GuildHandle`,
`UserHandle`, or a `MemberActor` for `user=`). Owners need not be in the bot cache or
current guild membership. A guild entitlement can also carry a purchaser via `user=`;
that purchaser does **not** gain access in other guilds or DMs. Personal entitlements
follow the invoking user, never the context-menu target or message author.

`seed_entitlement` is synchronous and emits no CREATE. It accepts historical
`starts_at`, `ends_at`, `deleted`, `consumed`, and an explicit future `scheduled_end_at`.
Dates must be aware datetimes. Merely setting a wire `ends_at` does not schedule a gateway
UPDATE, and already-ended history emits no retroactive event. Only consumables can be
seeded consumed. `type=` accepts `discord.EntitlementType`; the existing fixture default
is application subscription (8) for recurring SKUs and purchase (1) for one-time SKUs.
This default is simulator policy, not an invariant that all real subscription entitlements
have type 8: the captured live subscription checkout used purchase (1), API test grants
used application subscription (8), and Application Test Mode purchases used type 4.

## Deliver live changes

```python
premium = env.create_sku("Premium")
grant = await env.grant_entitlement(premium, guild=env.guild)
await grant.schedule_end(env.now() + timedelta(days=7))
assert grant.snapshot().ends_at is None
await env.advance_time(7 * 24 * 60 * 60)
assert grant.snapshot().ends_at == env.now()
await grant.revoke()
```

Import `timedelta` from `datetime` for this example. Live grants emit CREATE and settle
bot reactions; they cannot start deleted/consumed or carry historical dates. `end()`
ends a started, non-test subscription now and emits UPDATE, retaining history.
`schedule_end(at_or_none)` replaces/cancels a future end for an unended, non-test
subscription. It does not expose a billing period through `ends_at`: that stays null
until the action executes. End/revoke clear pending actions. `revoke()` marks deleted
and emits DELETE, without implying a refund. Async transitions require a running Env
and reject before mutation otherwise.

An observed immediate subscription upgrade ended the old entitlement with a nondeleted
UPDATE before the replacement type-1 CREATE. Its UPDATE carried `fulfillment_status=3`;
a later REST snapshot carried 6. SimCord commits the ended state/status 6 before delivery
and emits the detached UPDATE with the prior status. This deterministic fixture handling
does not simulate propagation time or guarantee atomic replacement. `end()`/`grant_entitlement()`
can arrange these entitlement effects, not subscription billing, reporting or events.

A live grant is committed **before** settlement. If settlement fails, the exception's
notes contain its committed ID; use `env.get_entitlement(id)`, resolve the blocked bot
work, then settle. Do not retry the grant. The transcript also retains the CREATE.

`SKUHandle.snapshot()` and `EntitlementHandle.snapshot()` return fresh, detached real
`discord.SKU` / `discord.Entitlement` objects bound to the current bot generation.
Previously returned SDK objects do not update themselves after consume/end/revoke.

## Consumables, sandbox grants and replay

```python
topup = env.create_sku("Top-up", type=discord.SKUType.consumable)
bought = await env.grant_entitlement(topup, user=alice)
await bought.snapshot().consume()
await env.settle()
await bought.replay_create()
assert (await env.bot.fetch_entitlement(bought.id)).consumed
```

Consumption is fulfilment, not credit spending. The application owns fulfilment
idempotency and its own credit ledger. `replay_create()` injects the **original** backend-owned
CREATE payload again, even after consumption or revocation, without changing the row.
Each delivery is independent. Seeded history has no original CREATE and cannot replay.
This is simulator fault injection, not a promise that Discord redelivers stale CREATEs.

The SDK's `Client.create_entitlement(sku, owner, owner_type)` exercises the sandbox REST
API and returns `None`; enumerate or fetch to recover the ID. API-created test grants
have type 8, `starts_at=null`, `ends_at=null` and `consumed=false`. A guild grant has the
requested `guild_id` and the application's bot user as purchaser `user_id`; a personal
grant has the requested `user_id` and no `guild_id`.

Test-create accepts only subscription SKUs with the matching scope flag. A nondeleted,
unended subscription for that scoped owner blocks another test grant across tiers
(`400/40074`), including when the existing entitlement came from live checkout.
One-time purchases do not block subscriptions; guild purchaser identity is not personal
ownership, and guild and personal ownership can coexist. Temporal boundary handling is
simulator policy, not calibrated natural-expiry behavior.

API test grants and explicit `discord.EntitlementType.test_mode_purchase` (4) fixtures
can be deleted via SDK `Entitlement.delete()`. Type 4 is not API provenance: endpoint
permission does not authorize the reference recorder to clean up someone else's purchases.
Fixture `revoke()` remains idempotent; REST DELETE does not. Deleted rows remain readable
history, but repeated DELETE and consume of a deleted row reject with `404/10029`
(`Unknown Entitlement`). First consume emits one UPDATE; repeat consume rejects with
`400/40022` (`Entitlement already consumed`). Wrong-kind consume rejects with `400/40018`
(`Only consumable SKUs can be consumed`). Rejected mutations do not write or emit events.

Test-create rejects unknown SKUs with `400/10027` (`Unknown SKU`) and one-time or
mismatched-scope SKUs with `400/50057` (`Invalid SKU`). Zero or both scope flags also
reject with 50057 as an explicit simulator fail-fast policy, outside the captured
mismatched-scope contract. Owner validation precedes SKU lookup: invalid `owner_type`
or `owner_id` returns `400/50035` (`Invalid Form Body`) with nested
`errors.<field>._errors`. Captured examples were `owner_type=0` with
`ENUM_TYPE_COERCE` / `Value "0" is not a valid enum value.` and
`owner_id="not-a-snowflake"` with `NUMBER_TYPE_COERCE` /
`Value "not-a-snowflake" is not snowflake.`, even with an unknown SKU.

REST, snapshots and Gateway payloads include `fulfilled_at` and `fulfillment_status`;
interaction payloads omit those two fields. All projections include nullable
`starts_at`/`ends_at`, `gift_code_flags=0` and `promotion_id=null`. `consumed` is present
for consumables and API test grants, omitted for other ordinary/test-mode non-consumables
(discord.py normalizes absence to `False`). Optional `source_type` is retained across
projections; fixture policy uses 10 for explicit type 4 and 9 for type-1 subscriptions.

## REST, interactions and restart

All six routes are supported: SKU list; entitlement list/get/create-test/delete-test;
and consume. Listing is history: ended rows are included by default, deleted rows
excluded by default, and consumed rows retained. User/guild/SKU filters combine before
pagination; SKU IDs are ORed, filter categories ANDed. No-cursor and `before` pages
descend by numeric ID, selecting the nearest/newest qualifying rows; `after` pages
ascend, selecting the nearest newer rows. Cursors are strict. Limit is 1–100; explicit
limit-100 captures include more than 100 rows. The endpoint's omitted-limit default is
not calibrated. There is no `exclude_consumed` query. Unknown write fields raise
`UnsupportedField`.

**discord.py 2.7.1 limitations:** the default/after entitlement paginator discards
`exclude_deleted=False`. Default iteration first requests a descending no-cursor page,
then switches to its `after` strategy, so multi-page iteration can overlap and miss
older rows. For complete nondeleted SDK enumeration, explicitly use
`bot.entitlements(limit=None, after=discord.Object(id=0))`. Its finite `before` paginator forwards
the deleted flag but advances from the newest rather than oldest row on descending
pages, also overlapping pages and potentially missing history. SimCord honors received
requests, not discarded intent.

For complete raw history on this SDK version, use its existing low-level HTTP API:

```python
cursor = None  # initial no-cursor page descends, just like subsequent before pages
while True:
    page = await bot.http.get_entitlements(
        bot.application_id, limit=100, before=cursor, exclude_deleted=False
    )
    for entitlement in page:
        process(entitlement)  # raw dictionaries, not discord.Entitlement objects
    if len(page) < 100:
        break
    cursor = int(page[-1]["id"])  # oldest returned row
```

`process` is your history consumer. This version-specific recipe uses a low-level SDK
interface, not a new public SimCord helper or an SDK patch; recheck the limitation when
upgrading.
In a 110-deleted-row simulator exercise, default SDK enumeration returned zero;
finite SDK `before(limit=110)` returned 110 rows but only 100 distinct IDs; the raw
recipe returned all 110 distinct IDs in two pages.

All interaction families reuse the same owner/access projection, including DMs,
context menus, buttons, selects, modals and autocomplete. Guild-owned entitlements come
first, preserving order within each scope, not globally sorting IDs. Guild ownership
takes precedence over purchaser identity. Gateway `ENTITLEMENT_*` events go to shard
zero, even for a guild on another shard. A worker without shard zero does not receive
them; there is no fallback shard or invented intent gate.

Restart retains catalog, entitlement history, replay payloads and scheduled actions.
The new bot's `setup_hook` can reconcile REST state before guild cache hydration.
Restart does not automatically replay entitlement history. Leaving a guild does not
remove application entitlement state or cancel its schedule; SDK `entitlement.guild`
can become `None`. Restart hydrates only guilds still in authoritative backend state.

Preview remains presentation-only. Premium names/prices/icons are explicitly supplied
presentation assets, not inferred from SKU resources. Premium button activation only
explains the external Discord purchase boundary; it never grants an entitlement.

## Evidence and open parity checks

Calibration separates authenticated observations from fixture choices and remaining
boundaries. The local, ignored reference bundles are not published with these docs:

| Bundle | Evidence status |
| --- | --- |
| `monetization-20261021T192933380599Z` (S) | Completed legacy bundle; inherited validation recorded in R's `validation.prior_bundles` |
| `monetization-20261021T200523439882Z` (P) | Completed legacy bundle; inherited validation recorded in R's `validation.prior_bundles` |
| `remaining-20261021T220043382260Z` (R) | Valid partial bundle with historical blockers |
| `remaining-20261021T224639252974Z` (G) | Valid partial bundle with historical blockers |
| `upgrade-now` (U) | Completed, valid, zero pending; linked to G, not proof of universal parity |

| Classification | Calibrated scope or boundary |
| --- | --- |
| Observed behavior | Null API dates (S REST 7–9, Gateway 1, interaction 2); guild bot purchaser (G 17); mismatched-scope Invalid SKU (G 73–74); cross-tier duplicate rejection (R 50–64, G 32–46, U 53). |
| Observed behavior | Consume UPDATE and repeat 40022 (P 12/16); durable 40018 (P 25); type-4 DELETE 204 (R 29/40); fresh unconsumed DELETE then consume 404 (G 375/381/387); retained history and mutation rejection behavior. |
| Observed behavior | Direction-dependent filtered pagination at explicit limit 100: R 258–259 and G 269–270 each cover 101 scoped rows; unfiltered datasets contain 105 (R) and 210 (G) rows. Guild-first interaction order is captured; this is not numeric total ordering. |
| Observed behavior | Immediate upgrade old-A ended UPDATE, type-1 B CREATE, then subscription event (U Gateway 2–4); interactions 3–5 retain only B through scheduled downgrade and cancellation. UPDATE status 3 then later REST status 6 is a snapshot observation, not a propagation-time contract. |
| Explicit simulator policy | Explicit non-grantable groups; fixture default recurring type 8; deterministic IDs/slugs, fulfillment metadata and end snapshots; temporal access/duplicate boundaries; zero/both scope flags fail fast with 50057. Other-origin DELETE retains 40019 policy. |
| Unsupported | Checkout/payments/refunds, subscription reporting and `SUBSCRIPTION_*` events, billing transitions, application plans/credits/trials. Fixture end/grant arranges entitlement effects only. |
| Unverified | Atomic replacement/exact boundary, natural expiry, activated downgrade, future starts, ended consumables, isolated duplicates after natural end, live DELETE, omitted-limit default, ambiguous zero/both scope flags, every interaction family and multishard real-Discord delivery. |

No-event observations apply only to bounded connected capture windows, with legacy
sequence limitations retained. Cancellation is not expiry; an upgrade end is not natural
expiry; a scheduled downgrade is not an activated downgrade. G subscription LIST 444
proved cancellation despite detail request 445 returning `400/10013 Unknown User`;
offline recovery made no new requests. The operator runbook below is retained for
reference, not a request for fresh captures, purchases or waiting. Passing local tests
does not establish additional Discord parity.

### Capture a subscription sandbox batch

Use an authorized, verified, team-owned application with monetization onboarding
complete. Discord permits either user or guild subscription offerings per app,
not both simultaneously. This recorder covers **user subscriptions only**.

Create two user-subscription SKUs named `Parity Subscription A` and
`Parity Subscription B`. Leave them unpublished for the initial accessibility
check; the recorder never publishes SKUs or makes purchases. Install the bot in a
private test server with `bot` and `applications.commands` scopes, View Channels
and Send Messages permissions, and one text channel named `parity`. Leave
privileged intents disabled and the Interactions Endpoint URL blank. Do not run
alongside another process using the same bot.

Networked reference capture requires **aiohttp 3.12+** for its public per-request
middleware API. This prevents aiohttp from silently retrying uncertain writes,
including DELETE/PUT; only explicitly recorded HTTP 429 retries remain enabled.
The recorder checks this prerequisite before requesting credentials or recording
a mutation intent. Preview's library dependency remains `aiohttp>=3.9`; offline
evidence validation does not need the capture transport.

From the repository root, run in your own interactive terminal:

```bash
uv run python scripts/capture_monetization_reference.py --guild SERVER_ID --user YOUR_USER_ID
```

Enter the bot token at the hidden prompt; never put it in chat, shell arguments,
or a committed file. The recorder discovers app/SKU IDs, refuses existing active
grants for the selected user/SKUs, and adds only its own temporary guild command
without bulk-overwriting commands. When prompted, reload Discord and invoke
`/simcord_parity_probe` in `#parity` at the baseline, granted and deleted stages.
It captures raw REST requests/responses, entitlement dispatches, authorized
interaction payloads, connection health and labelled actions, with 60-second
observation windows after mutations. A disconnect invalidates conclusions about
absent events during that window.

The batch probes duplicate grants, a different SKU for the same owner, deleted
GET/list visibility (including explicit `exclude_deleted=false`), repeated
deletion and recreation after deletion. Only IDs from verified,
app/SKU/user-matched type-8 test-create receipts in this session are eligible for
cleanup. The temporary command is also removed. If Discord rejects an unpublished
SKU, inspect the captured error before deciding whether publication is necessary.
The initial A grant must succeed to establish the fixture. Duplicate,
different-SKU and post-deletion recreation requests are exploratory: their HTTP
rejections are evidence, not fatal assumptions. A completed session means the
capture sequence finished, not that every request succeeded.

Evidence goes into a new, ignored `.discord-reference-captures/monetization-*`
directory with restrictive permissions. `manifest.json` journals created IDs
and cleanup status. On interruption or failure, inspect it and the request/REST
logs before retrying; a transport failure can leave a write's outcome unknown.
Keep that directory and ask for help resolving any uncertain write or cleanup.
Review the bundle before sharing: secrets are redacted, but IDs and user/member
metadata are intentionally retained.

No credentials or Discord connection are needed for the runnable boundary check:

```bash
uv run python scripts/capture_monetization_reference.py --self-test
```

This local check is not Discord parity evidence. Guild ownership, one-time
purchases/consumption, temporal boundaries and large pagination remain separate
capture work.

### Capture test-mode purchases and consumption

Use the same authorized app/server/user, with a `Parity Consumable` consumable
SKU and a `Parity Durable` durable SKU. This mode never creates test entitlements
for one-time SKUs, publishes SKUs, or performs checkout.

Discord documents that Store items must be published **to the Store and API**.
Publishing can expose paid offers to real buyers, not just testers: get the app
owner's approval before doing so. API-only publication does not make an item
visible in the Store. Keep subscription SKUs unchanged.

In the Discord client, open **User Settings → Advanced**, enable Developer Mode
and Application Test Mode, and enter the reference application ID. Verify the
orange banner names the correct app. Discord requires a valid payment method on
file even though Application Test Mode purchases are not charged. Complete any
billing setup yourself; never share payment screens, credentials or card details.
If test mode or Store availability cannot be confirmed, stop without purchasing.

After confirming the banner, start the recorder in your own terminal:

```bash
uv run python scripts/capture_monetization_reference.py \
  --mode purchases --test-mode-confirmed --guild SERVER_ID --user YOUR_USER_ID
```

The flag records your confirmation; the recorder cannot inspect your client or
guarantee checkout safety. The first prompt is a baseline interaction, not a
purchase. Then follow the terminal: buy exactly one requested item from the app's
Store **only when prompted**, check the banner again after reloads, and invoke
`/simcord_parity_probe` in `#parity`. Do not use the broken "Go To SKU" shortcut.

There are six interaction checkpoints: baseline, consumable before consume,
after its first and repeated consume requests, durable before consume, and after
its wrong-kind consume request. Each mutation is followed by a labelled
60-second observation window and GET/list capture. Rejections are retained as
evidence without assuming a particular status, error code, event or projection.

Before consuming, the recorder requires exactly one new, unconsumed type-4
entitlement matching the app/SKU/user and revalidates it via GET. Preexisting
entitlements are excluded, and a full 100-row page or ambiguous/new live purchase
stops the session rather than guessing. `manifest.json` records purchase IDs.
These purchase rows are **not deleted by cleanup**; only the temporary command
is removed. On interruption, preserve the evidence and inspect the outcome before
restarting or buying another item. No subscription changes are made in this mode.

The built-in `--self-test` also checks purchase selection boundaries without a
Discord connection. Its results are not reference parity evidence.

### Capture the remaining evidence

Use `scripts/capture_remaining_monetization.py` after the initial subscription
and purchase batches. It reuses the raw recorder, discovers SKU IDs and existing
completed local bundles. The default pass needs no new purchases or published SKUs;
the explicitly opted-in checkout stages below are exceptions.

1. Keep the bot installed in the authorized server and the `parity` channel.
   Do not run another bot/recorder process with the same token.
2. Leave the two one-time SKUs unpublished if their purchase batch is finished.
   Keep the SKUs and `.discord-reference-captures` directories; do not delete them.
3. For the fullest user-scope run, arrange a second **consenting** user ID.
   `--other-user` authorizes test grants to that user, not checkout or account
   automation. They need not purchase anything or remain online for duplicate
   comparisons. Omit it only if that comparison must remain explicitly blocked.
4. Run from the repository root:

   ```bash
   uv run python scripts/capture_remaining_monetization.py \
     --guild SERVER_ID --user YOUR_USER_ID --other-user CONSENTING_USER_ID
   ```

   Omit the final flag if no second user is available. Enter the bot token only
   at the hidden local prompt.
5. Leave the terminal running. HTTP rejection probes are expected measurements.
   Observation windows print their duration and need no action. Bulk pagination
   population prints progress. Only respond when explicitly asked to invoke the
   probe command; the normal user-scope path is otherwise automated.
6. Keep the final directory and report **both** the session status and
   `Evidence valid` result. No ZIP/upload is needed when the reviewer has access
   to this machine.

The automated batch covers:

| Contract | Capture |
| --- | --- |
| Rejection status/body and precedence | Unknown SKU; GET/consume/DELETE of a positively confirmed unknown entitlement; paired known/unknown SKU with invalid owner type or malformed owner ID; subscription consume rejection |
| One-time test-create rejection | Deliberately invalid consumable/durable test-create requests, including a clean-owner comparison when a second consenting user is supplied; successful surprises are journaled and cleaned, never used as purchase fixtures |
| Non-sandbox deletion | Revalidate type-4 purchase IDs from completed local purchase bundles, then capture DELETE rejection or success, GET/list and gateway windows; these measurement targets are not adopted as sandbox cleanup ownership |
| Duplicate scope | Same/different SKU and authorized owner, deletion history, repeated DELETE, B after A deletion, A while B is active, and recreation |
| Pagination | First prove deleted history is listable, then create/delete enough sandbox grants for at least 101 distinct scoped rows; retain limit-100 traversals and limit-2 pages, middle-ID before/after probes, owner/SKU combinations and multi-SKU filters |
| Guild projection | Selected guild-subscription SKUs; create/GET/list/events and before/during/after interactions by two consenting members across two authorized guilds |

Pagination creates retained **test entitlement history**, not paid subscriptions.
It does not erase old records. No preexisting entitlement is deleted by cleanup.
The runner explores both cursor directions rather than assuming server ordering,
retains ordered raw arrays, respects HTTP 429 delays and paces requests.
If history cannot safely reach more than 100 rows, the report leaves large
pagination blocked; small pages are not substituted.

Each response is correlated with its exact request and actual query string.
The ledger records create receipts and pending writes before further mutations;
files are flushed durably, fields retain missing-versus-null distinctions, and
Gateway sequence/connection health is recorded. Source-bundle and recorder-file
hashes identify inputs and code revisions. Tokens/session secrets are redacted;
IDs and owner metadata remain private evidence.

`validation.json` checks protocol integrity, ownership/cleanup, request
correlation, real case references, observation windows, cursor observations and
large-page traversal. It includes separate reports for completed prior bundles.
It does **not** turn captured facts into simulator calibration approval.
`completed_with_blockers` with `Evidence valid: True` is a valid partial run:
inspect the named blocked cases and reasons in `manifest.json`. A completed run
does not prove all lifecycle cases or fix simulator behavior.

Validation is independent of recorder ownership flags. API test-create proof needs
an exact integer type 8, the requested application/SKU/owner and a fresh,
non-manual ID. Explicitly authorized manual type-4 purchases use a separate proof
path. Malformed evidence produces located error codes without echoing raw values.

The validator can also run without credentials:

```bash
uv run python scripts/validate_monetization_reference.py CAPTURE_DIRECTORY
```

#### Recovery without discarding evidence

On an interruption, use Ctrl+C **once** so owned cleanup can finish. Preserve the
directory; never start from scratch blindly.

```bash
uv run python scripts/capture_remaining_monetization.py --resume CAPTURE_DIRECTORY
```

Resume skips finished sections and preserves prior raw logs. An interrupted
section may need fresh sandbox fixtures or repeated manual checkpoints. A
recorded response can recover ownership without replaying the request. An
unacknowledged write is **not retried or inferred from a list**; retain the
journal and ask the reviewer to resolve it. Only positive-provenance resources
may be cleaned separately:

```bash
uv run python scripts/capture_remaining_monetization.py \
  --resume CAPTURE_DIRECTORY --cleanup-only
```

Cleanup-only loads the saved identities and verifies correlated positive receipts;
it does not require a new capture's checkout configuration or source fixtures.
Recovery preserves schema-1 ordered correlation and schema-2 request IDs. A
success-looking manifest flag or an earlier retry response cannot establish ownership.

Deleted sandbox grants remain session-owned history, backed by their original
positive receipts. Later baseline scans do not adopt those IDs as pre-existing
resources, so repeated resume and cleanup-only remain safe. A journal that
already lists the same ID as both baseline and owned is still rejected; resolve
that conflict by auditing its evidence, not by discarding history or weakening
the ownership check.

Recovery cannot change the original app/server/owner identities. A second owner
may be added to a run that previously lacked one; the relevant comparison
sections are reopened, not the purchase batches. Connection gaps and interrupted
windows stay recorded; later healthy windows do not retroactively fill them.

Checkout recovery also inspects per-stage completion: a blocked live-upgrade
stage is reopened even if an older journal marked the enclosing section done.
Completed guild sections and identified consumable purchases are not repeated.
Purchase baselines traverse all pages, including more than 100 deleted grants
left by pagination testing. A `*_baseline` line is automatic inspection, **not
a request to subscribe**; wait for the numbered `MANUAL CHECKOUT` instructions.
Blocked stages print their reason directly in the terminal.

Interaction checkpoints are validated against the `probe_received` action's
exact interaction ID. Extra invocations by another authorized participant stay
in the raw log but are not mistaken for the invocation that advanced the prompt.
Existing bundles can be revalidated without repeating those interactions.

#### External prerequisites and real lifecycle watching

A personal entitlement cannot prove guild ownership. If the same eligible app
allows saving both unpublished guild SKUs, use those; a separate app is not a
prerequisite. Discord's restriction on offering both subscription scopes does not
justify rejecting an unpublished sandbox measurement in advance. Never publish
both subscription scopes to attempt this capture.

For one comprehensive follow-up, leave `Parity Guild Subscription A` and
`Parity Guild Subscription B` unpublished. Create a second private server with
a `parity` text channel, install the same bot with `bot` and
`applications.commands` scopes, and invite both consenting users to both servers.
Both users need View Channel, Send Messages and Use Application Commands.
Keep both users available for the prompts. The runner verifies membership,
channels and independent temporary commands before creating grants.

```bash
uv run python scripts/capture_remaining_monetization.py \
  --mode guild --guild FIRST_SERVER_ID --other-guild SECOND_SERVER_ID \
  --user OPERATOR_ID --other-user CONSENTING_MEMBER_ID \
  --deleted-purchase --test-mode-confirmed
```

The guild pass selects these exact guild SKU names, records all four
user/server projections before/during/after a guild grant, duplicates with both
owners/SKUs, both personal/guild grant creation orders, mismatched owner types,
and a guild-scoped >100-row pagination dataset. Completed earlier global
rejection and user-scope batches are retained, not repeated.

`--deleted-purchase --test-mode-confirmed` adds one **fresh** consumable
Application Test Mode purchase, deleted while still unconsumed, then consume
and repeat-consume with GET/list/events/interactions after each mutation.
Earlier consumed-and-deleted purchases cannot prove this case. Publish only the
consumable if needed, buy exactly one only when prompted, verify the orange
banner names the app, and never use a live checkout for this stage. The manual
purchase ID is explicitly authorized but never adopted as API-created cleanup
ownership. Durable purchases need not be repeated.

##### Optional zero-total live upgrade in the same run

To also observe a legitimate subscription end caused by an upgrade, append
`--live-upgrade --team-discount-confirmed` to the command **before starting**.
This is optional and involves real, renewing subscriptions:

1. Use an account on the app's developer team. Prepare **user** subscription
   A at the lowest available tier and user B at a higher tier. Publish those
   user offerings only; leave both guild SKUs unpublished.
2. Do not subscribe yet. Keep Application Test Mode enabled for the consumable
   stage. The recorder explicitly prompts when to disable it for the live stage.
3. At the A prompt, subscribe only if checkout shows the team's **100% discount
   and final 0.00 total**. Stop otherwise; never supply payment to bypass a
   missing discount. Enter `0.00` in the recorder only after verifying checkout.
4. At the B prompt, upgrade the existing A subscription to the higher-priced B.
   Independently verify this checkout also shows 100% discount and final 0.00.
   Do not make a downgrade or unrelated second subscription.
5. Follow the interaction prompts while A and B are inspected. Then in Discord
   User Settings → Subscriptions cancel every subscription started here,
   including A if the upgrade failed. Follow the cancellation probe prompt.
   The recorder checks subscription reporting as well as entitlement state.

An upgrade is classified `ended_by_upgrade`, **not natural billing expiry**.
Cancellation of the replacement usually retains access until period end.
If interrupted or cancellation is unverified, cancel in Discord immediately
and resume the same evidence directory; **do not purchase again**. A persisted
cancellation obligation prevents the run from claiming completion.

The captured live team checkout returned entitlement type `1` (`PURCHASE`), not
the type `8` (`APPLICATION_SUBSCRIPTION`) used by sandbox grants. Live recording
accepts those two wire types only for the exact selected subscription SKU,
application and user; consumable mutation remains restricted to type `4`.
After cancellation has started, resume is cancellation-only: it re-identifies
the purchased entitlement from the original baseline, checks subscription
reporting, and never prompts another purchase or upgrade. A missed upgrade
remains explicitly blocked even after cancellation is verified. Prior temporary
commands are verified from their create/delete receipts across resumes, not
required to occupy the latest per-server command registry.

The subscription **list** response contains complete subscription objects and
is authoritative for cancellation: match user, SKU and entitlement IDs, require
status `2` (`ENDING`, will not renew) and a non-null `canceled_at`. Entitlement
access can remain active with `ends_at: null` after cancellation. Do not require
a second detail request to confirm the list row; a recorded detail error is
retained as an HTTP observation, not interpreted as renewal.

If those positive responses already exist in an interrupted capture, re-evaluate
the saved evidence before asking the operator for another run. An offline
recovery must preserve protocol streams, identify its exact response references,
journal the previous state and its offline nature, and pass bundle validation.
It may resolve cancellation, but must not claim an unobserved upgrade or natural
expiry. If the Discord client already shows the subscription canceled, do not
ask the operator to purchase again or repeat cancellation merely because a
recorder checkpoint is unresolved.

These opt-ins preserve raw request/response and interaction references, app/
SKU/owner identities, manual-selection baselines, connected event windows,
mutation receipts and resumable stage IDs. The bot does not automate checkout,
cancel live subscriptions, or independently verify the operator's checkout total.

##### Immediate upgrade and scheduled-downgrade follow-up

Use a **new** capture directory, linking the earlier verified-canceled A
subscription bundle. This does not wait for its billing period to end:

```bash
uv run python scripts/capture_remaining_monetization.py \
  --upgrade-from .discord-reference-captures/COMPLETED_SOURCE \
  --team-discount-confirmed \
  --output .discord-reference-captures/upgrade-now
```

The source supplies and authenticates the app, operator, server and A/B names.
Its raw files remain unchanged. Enter the bot token only at the hidden terminal
prompt. Use the app-team account, keep Application Test Mode **disabled**, and
keep user A/B published to Store with B priced higher; guild SKUs stay unpublished.
The [app Store](https://docs.discord.com/developers/monetization/managing-skus#linking-to-your-store)
supports subscription changes.

Follow the numbered recorder prompts, not the steps ahead of the recorder:

1. Reload Discord and invoke `/simcord_parity_probe` in the original `parity`
   channel for the before observation. Do not purchase yet.
2. If A needs preparation, resume it in User Settings → Subscriptions. If the
   client instead requires a new/restarted A subscription, a fresh checkout is
   allowed with **100% team discount and final 0.00**. Enter `RESUMED` only for
   a no-checkout resume, or `0.00` after the free checkout, then reload/probe.
   Already-renewing A is reused without this action.
3. Upgrade that A subscription to B through the Store. Independently verify
   100% discount/final 0.00, enter `0.00`, then reload/probe.
4. When prompted, select A in the Store to schedule B → A at period end.
   Do not force renewal or make an unrelated purchase. Reload/probe even if
   the UI cannot schedule it; the recorder retains that blocker.
5. Cancel every subscription reused, resumed or started in the session in
   User Settings → Subscriptions, including any replacement A. Reload/probe
   when asked. No entitlement deletion is needed.

The recorder captures entitlement GET/list projections, full subscription-list
objects, raw entitlement/subscription Gateway events and each interaction.
Connected observation windows default to 60 seconds. Read-only linkage and
cancellation settling allows up to 120 seconds (`--settle-seconds`), without
repeating checkout or API writes.

After the upgrade it automatically attempts one same-owner sandbox A grant
while B is active, records acceptance/rejection, and removes only a positively
identified new API-created grant. This is **cross-tier overlap**, not isolated
duplicate-after-natural-end evidence. Manual purchase IDs, unexpected wire
types, mismatched SKUs/owners and preexisting rows cannot become cleanup ownership.

Upgrade ending is labeled `ended_by_upgrade` only when the raw A/B transition
proves it. Other A outcomes remain explicit while a verified active B still
permits further collection. Scheduled downgrade proves only pending renewal
(`renewal_sku_ids`), **not activation, future-start access or natural expiry**.
Changing the subscription invalidates its previous billing-expiry watch plan.

For interruption, resume **this new directory** with `--resume`; never restart
the batch or repeat a purchase merely to repair a checkpoint. A missing probe
can be collected without repeating its UI action or checkout attestation.
Once cancellation has started, recovery is cancellation-only. Every managed
real ID, including replaced A IDs, must be verified ended or canceled using
subscription-list evidence; redundant detail lookups are not required.

##### Expiry-only follow-up from existing evidence

Use the completed, canceled-subscription bundle as input to a **new** follow-up:

```bash
uv run python scripts/capture_remaining_monetization.py \
  --expiry-from .discord-reference-captures/COMPLETED_SOURCE \
  --output .discord-reference-captures/expiry-watch
```

This derives the application, original user/server, subscription and entitlement
IDs, billing period end, and before-expiry interaction from validated raw source
responses. It hashes and links that source; it does not copy old interactions
into a new Gateway session or claim continuous coverage across the offline gap.
Do not use the old purchase capture as the `--resume` directory for this phase.

Run within 30 minutes before the saved period end, allowing setup time. If run
earlier, only read-only preflight is performed and the terminal prints when to
return. Nothing is scheduled or running afterward. Enter the bot token at the
hidden local prompt; keep the machine awake, online and the terminal open.
No second member or second server is needed.

The runner rechecks the canceled subscription identity and unchanged billing
schedule. It captures entitlement REST polls, subscription reports, Gateway
entitlement events and authorized subscription events. A resumed subscription
or changed schedule stops the watch without entering checkout. Unavailable
subscription reporting does not discard other observations, but prevents a
natural-billing-expiry claim.

After detecting an end, it asks for **one** reload and `/simcord_parity_probe`
in the original server's `parity` channel. The saved active interaction supplies
the earlier observation. It then revalidates the original ended row, checks for
conflicting live grants, attempts one same-SKU/owner **sandbox** grant, records
the outcome and removes only any new API-created grant. It never consumes,
deletes or cancels the real subscription entitlement and never buys/upgrades.

Default watch duration is four hours (`--watch-seconds`); the post-end command
allows 24 hours (`--probe-timeout`). A timeout preserves evidence without asking
for another purchase. A late start still captures REST/post-end observations,
but cannot recover Gateway events emitted before connection. Unexpected earlier
end dates are recorded without mislabeling them as scheduled billing expiry.
The exact atomic eligibility boundary remains explicitly unverified.

On interruption or a bounded-window timeout, preserve and resume the **new**
follow-up directory:

```bash
uv run python scripts/capture_remaining_monetization.py \
  --resume .discord-reference-captures/expiry-watch
```

Resume reuses a successfully captured post-end interaction and does not replay
a grant with a recorded outcome. Unknown write outcomes still require review.
Owned-resource cleanup alone remains available with `--cleanup-only`.
Completion applies to this expiry-only scope, not every parity prerequisite.

##### Other legitimate lifecycle fixtures

Natural expiry and future activation require legitimate entitlement fixtures.
Do not fake timestamps or substitute test deletion or the opt-in upgrade above
for natural expiry. When a legitimate temporal fixture already exists,
the same runner supports read-only real-time watching:

```bash
uv run python scripts/capture_remaining_monetization.py \
  --mode watch --guild SERVER_ID --user YOUR_USER_ID \
  --watch-entitlement LEGITIMATE_ENTITLEMENT_ID --live-lifecycle-confirmed \
  --watch-seconds 3600
```

Start while the entitlement is still active or future-dated and follow the
before/after interaction prompts. The flag attests this is a legitimate
lifecycle fixture, not an API test grant. Discord can publish `ends_at` only
when a subscription actually ends; a prepopulated end date is not required.
Cancellation alone is not expiry. Already-ended fixtures can capture post-end
access and grant attempts but cannot restore the missing before/UPDATE evidence.
Watches preserve their original baseline across resume and report the offline
gap. Supply one watched fixture per run when timing matters; multiple supplied
IDs are processed sequentially. Exact `ends_at <= now` interaction eligibility
remains unproven by ordinary later interactions.

For a genuinely ended/deleted **type-4 consumable** fixture, explicit consumption
authorization is separate:

```bash
uv run python scripts/capture_remaining_monetization.py \
  --mode watch --guild SERVER_ID --user YOUR_USER_ID \
  --consume-history TEST_MODE_ENTITLEMENT_ID
```

The runner revalidates app, owner, SKU kind, type and actual historical state
before each consume/repeat, and captures interactions, GET/list and gateway
windows. It refuses live purchases, unverifiable/404-only history, and arbitrary
active purchases. An already-consumed historical row is reported separately
from the unconsumed historical case. Without legitimate fixtures, those rows
remain blocked; there is no public sandbox date setter to replace them.




Primary sources: Discord's [Entitlement resource](https://docs.discord.com/developers/resources/entitlement),
[SKU resource](https://docs.discord.com/developers/resources/sku),
[app subscriptions](https://docs.discord.com/developers/monetization/implementing-app-subscriptions),
[one-time purchases](https://docs.discord.com/developers/monetization/implementing-one-time-purchases),
[interaction payload](https://docs.discord.com/developers/interactions/receiving-and-responding),
[gateway events](https://docs.discord.com/developers/events/gateway-events),
[sharding](https://docs.discord.com/developers/events/gateway#sharding), and
[error catalog](https://docs.discord.com/developers/topics/opcodes-and-status-codes).
Authenticated consume UPDATE evidence is recorded in the local bundles described above.
The discord.py 2.7.1 paginator caveats concern SDK request/cursor handling; raw captured
pages establish the server ordering used here. Neither local captures nor fixture checks
establish the unverified lifecycle boundaries.
