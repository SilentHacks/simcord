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
`bot.entitlements(after=discord.Object(id=0))`. Its finite `before` paginator forwards
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
