---
title: "Architecture"
description: "How SimCord works: it swaps discord.py's two transport seams — the REST client and the gateway parser — for an in-memory backend, while every model, converter, check and view runs as real, unmodified discord.py code."
---

# Architecture

discord.py has two narrow seams:

1. **Every REST call** funnels through `HTTPClient.request(route)` (interaction
   responses go through the async webhook adapter — also a single `request` method).
2. **Every gateway event** enters through `ConnectionState.parsers[event](payload)`.

`simcord` replaces the transports behind seam 1 with fakes routed into an
in-memory backend, and injects Discord-shaped payloads through seam 2. Everything
between the seams — models, converters, the command tree, checks, views, the cache —
is real discord.py code running unmodified.

```
test ──► builders/actors ──► virtual backend (single source of truth)
                                   │                     │
                  gateway payloads ▼                     ▼ REST responses
                  ConnectionState.parsers        FakeHTTPClient route table
                                   │                     ▲
                                   ▼                     │
                                  your real, unmodified bot
```

## Key design decisions

- **One store, two projections.** REST responses and gateway events are generated from
  the same state, so when the bot sends a message it also sees its own `MESSAGE_CREATE` —
  caches and listeners behave exactly as in production. To keep this honest, every state
  write lives on `Backend` and emits its own gateway event there; route handlers only
  parse the request, permission-check it (via `ctx.require_*_permissions`), call one
  `Backend` method, and serialize the result — so a mutation can never be announced
  inconsistently or forgotten as more routes are added.
- **One shard router.** `AutoShardedClient` gets one fake gateway/websocket adapter per
  active shard, while one router applies Discord's snowflake formula and delivers each
  backend event exactly once. All adapters feed the client's shared real
  `AutoShardedConnectionState`, preserving discord.py's cache and readiness semantics
  without modelling sockets or IPC.
- **Loud gaps.** An unimplemented route raises `RouteNotImplemented` naming the route,
  and an edit handler sent a field it does not honour raises `UnsupportedField` rather
  than silently dropping it. A testing tool must never silently fake success — neither at
  the route level nor the field level.
- **Authentic errors.** Backend failures surface as genuine `discord.Forbidden` /
  `NotFound` / `HTTPException` with real Discord JSON error codes, because user code
  branches on them.
- **Payloads typed against `discord.types`.** Serializers are annotated with discord.py's
  own TypedDicts — the exact contract its parsers consume — so shape drift against a new
  discord.py release is caught statically.
- **Deterministic settling.** The environment tracks every task the bot spawns and waits
  for quiescence after each injected event. No `asyncio.sleep` guesswork; a hung handler
  fails fast with the pending tasks listed.
- **Quarantined internals.** Every private discord.py touchpoint lives in
  `_dpy_internals.py` behind an import-time self-check, and CI runs weekly against
  discord.py's master branch to catch drift early.
- **Deterministic snowflakes** with valid embedded timestamps, from a fixed virtual epoch.

## What it will never do

Connect to Discord. There is no "integration mode"; automating a real client violates
Discord's Terms of Service and this project exists precisely to make that unnecessary.
