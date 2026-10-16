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

`create_sku` accepts `discord.SKUType.subscription` (default), `.durable` or `.consumable`.
A subscription atomically creates one additional `.subscription_group` entry, visible in
`Client.fetch_skus()`. Groups cannot be created directly or granted. Generated slugs and
IDs are deterministic simulator choices. There is no Free-plan SKU.

Flags are `discord.SKUFlags`. The default subscription is guild-scoped; use
`flags=discord.SKUFlags(user_subscription=True)` for a personal subscription. One-time
SKUs default to `available=True`. Availability does not erase existing access. Fixtures
arrange authoritative events/state; they do not enforce checkout eligibility.

Owners may be raw positive 64-bit snowflakes or same-Env handles (`GuildHandle`,
`UserHandle`, or a `MemberActor` for `user=`). Owners need not be in the bot cache or
current guild membership. A guild entitlement can also carry a purchaser via `user=`;
that purchaser does **not** gain access in other guilds or DMs. Personal entitlements
follow the invoking user, never the context-menu target or message author.

`seed_entitlement` is synchronous and emits no CREATE. It accepts historical
`starts_at`, `ends_at`, `deleted`, `consumed`, and an explicit future `scheduled_end_at`.
Dates must be aware datetimes. Merely setting a wire `ends_at` does not schedule a gateway
UPDATE, and already-ended history emits no retroactive event. Only consumables can be
seeded consumed. `type=` accepts `discord.EntitlementType`; the default is application
subscription (8) for recurring SKUs and purchase (1) for one-time SKUs.

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
are perpetual, omit date fields and can be deleted via SDK `Entitlement.delete()`.
They have private sandbox provenance. A fixture with wire type
`discord.EntitlementType.test_mode_purchase` (4) is **not** an API test grant and does
not gain sandbox deletion permission. One-time purchase fixtures use normal live grants
(or explicit type 4); the test-entitlement API is for subscriptions, not one-time SKUs.

## REST, interactions and restart

All six routes are supported: SKU list; entitlement list/get/create-test/delete-test;
and consume. Listing is history: ended rows are included by default, deleted rows
excluded by default, and consumed rows retained. User/guild/SKU filters combine before
pagination; SKU IDs are ORed, filter categories ANDed. Limit is 1–100. There is no
`exclude_consumed` query. Unknown write fields raise `UnsupportedField`.

**discord.py 2.7.1 limitation:** the default/after entitlement paginator omits
`exclude_deleted`, even if the caller supplies `False`. SimCord honors the received
request, not the caller's discarded intent. The SDK's `before=` strategy forwards it.

All interaction families receive the same owner/access projection, including DMs,
context menus, buttons, selects, modals and autocomplete. Gateway `ENTITLEMENT_*`
events go to shard zero, even for a guild on another shard. A worker without shard zero
does not receive them; there is no fallback shard or invented intent gate.

Restart retains catalog, entitlement history, replay payloads and scheduled actions.
The new bot's `setup_hook` can reconcile REST state before guild cache hydration.
Restart does not automatically replay entitlement history. Leaving a guild does not
remove application entitlement state or cancel its schedule; SDK `entitlement.guild`
can become `None`. Restart hydrates only guilds still in authoritative backend state.

Preview remains presentation-only. Premium names/prices/icons are explicitly supplied
presentation assets, not inferred from SKU resources. Premium button activation only
explains the external Discord purchase boundary; it never grants an entitlement.

## Evidence and open parity checks

The following rules are **explicit simulator policies**, approved for this release;
they are not authenticated observations of Discord. Keep them distinct from supported
behavior when designing production assumptions. No real paid purchases or client
automation are needed or authorized by this guide.

| Unverified contract | Current simulator policy | Capture needed to establish parity |
| --- | --- | --- |
| API test wire type, purchaser and projection omissions | Type 8; only requested owner field; omit dates in create/get/list/events | User and guild create response + CREATE + GET + list |
| Interaction temporal/consumption boundaries | Exclude future-start, ended (`ends_at <= now`), deleted and consumed rows | Interactions before/after consume, revoke and end; future entitlement if available |
| Consume event and repeats | First consume emits UPDATE; repeated consume succeeds without another event; historical/deleted consumables remain consumable | Consume + gateway, repeat, and ended/deleted cases |
| Sandbox deletion history and repeats | Retain gettable tombstone; default list excludes it; `exclude_deleted=false` includes it; repeated delete succeeds without another event | DELETE, GET, both list modes, repeat DELETE |
| HTTP rejection status and precedence | 404/10027 unknown SKU; 404/10029 unknown entitlement; 400 paired with 40018 wrong-kind consume, 40019 non-sandbox delete and 40074 duplicate grant; lookup/validation order follows route implementation | Status/body for each rejection and combined invalid inputs |
| Duplicate sandbox trigger | Reject same SKU+owner while a nondeleted, unended grant exists; deleted/ended history permits another | Same and differing owners/SKUs; duplicate after delete/end |
| One-time SKU test-create rejection | 400/50035; use purchase fixtures instead | Invalid API test-create request for durable/consumable SKU |
| Cursor ordering | Ascending pages, strict cursors; before selects nearest preceding page | More than 100 entitlement rows, before/after with filters |

Calibration requires an authorized monetized reference application with subscription and
consumable SKUs, permitted owner IDs, and sandbox/test-mode observations. Record status,
body and gateway/interaction traces per row above; a passing local test is not parity evidence.

Primary sources: Discord's [Entitlement resource](https://docs.discord.com/developers/resources/entitlement),
[SKU resource](https://docs.discord.com/developers/resources/sku),
[app subscriptions](https://docs.discord.com/developers/monetization/implementing-app-subscriptions),
[one-time purchases](https://docs.discord.com/developers/monetization/implementing-one-time-purchases),
[interaction payload](https://docs.discord.com/developers/interactions/receiving-and-responding),
[gateway events](https://docs.discord.com/developers/events/gateway-events),
[sharding](https://docs.discord.com/developers/events/gateway#sharding), and
[error catalog](https://docs.discord.com/developers/topics/opcodes-and-status-codes).
The ascending paginator and omitted flag are observed in installed discord.py 2.7.1 source;
they do not prove server ordering. Consume UPDATE has only unofficial explicit corroboration,
so remains in the calibration table.
