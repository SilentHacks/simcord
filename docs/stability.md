---
title: "Stability & versioning"
description: "What SimCord's public API covers, what stays internal, and how versioning works for the 2.0 release."
---

# Stability & versioning

!!! warning "What changed in 2.0"
    Settlement now joins all runnable bot-owned work, including executor-backed work and
    finite callback chains. Recognized external waits may park only through
    `env.external_wait(awaitable, reason=...)`; unknown waits time out. Ownership survives
    timeout, cancellation, later operations, restart, and teardown boundaries.

## Migrating from 1.x

In 2.0, every dispatched handler joins its runnable bot-owned work before the
actor or public operation returns. Replace implicit parking with an explicit
external-input declaration:

- Use `await env.external_wait(awaitable, reason="...")` for an intentional
  external wait.
- Unknown waits time out with diagnostics; release the dependency, then call a
  later operation or `env.settle()` to recover. Do not replay the original action.
- Actor, builder, lifecycle, and time-control operations reject overlap before
  mutating the virtual world.

To keep production code independent of SimCord, inject the test environment's
`external_wait` adapter only in tests and use the normal awaitable in production,
or keep an indefinite wait outside a dispatched handler in the application's
own supervisor.

SimCord follows [semantic versioning](https://semver.org/). The
**public API** below is covered by that promise: no breaking change to it without
a major version bump. The 2.0 release keeps this contract while changing
settlement ownership as described above. CI continues to enforce honest parity,
coverage, and offline-performance checks.

The fuzzer proves that across every route whose body is a field set — message
send and edits, webhook execute, bulk delete, and the rest — an unrecognised
request key raises `UnsupportedField` rather than being silently dropped. The
few routes that *cannot* be expressed as one flat field set are not swept under
the rug: those with no JSON body, the command-sync routes that store their
payload verbatim (so nothing is dropped), and the polymorphic
interaction-callback envelope (whose message `data` is itself vetted) are
enumerated with a reason and drift-guarded, so the boundary stays explicit.

## Supported discord.py

SimCord targets **discord.py 2.7.1+** (`discord.py>=2.7.1,<3`). The locked CI
matrix tests 2.7.1, and a separate weekly workflow runs against upstream
`master`; other released 2.x versions in the declared range are not each
continuously tested. Because a faithful fake must shadow a few discord.py
internals (view timeout tasks, parser entry points), simcord verifies them at
import via `simcord._dpy_internals.verify()` and fails **loudly** with an
`ImportError` naming what moved, rather than miscompiling silently. The `<3`
ceiling is deliberate: a new discord.py major may move those internals, so the
range widens only once a release has been tested.
SimCord supports **Python >=3.11** and CI tests **3.11–3.14**. The settlement
engine uses the standard `asyncio` task factory, callback scheduling, and
timer-heap capabilities provided there.

## Public API

The public surface is exactly what `simcord` exports from its top-level package
(everything in `simcord.__all__`):

- the entry point `run` and the `Env` it yields;
- the world builders — `GuildHandle`, `ChannelHandle`, `UserHandle`,
  `RoleHandle` — and the `MemberActor` that drives simulated users;
- the result objects `ResponseMessage` and `InteractionResult`;
- the request-observability type `HttpLogEntry` and `Env.http_requests`;
- the assertion helpers (`assert_responded`, `assert_sent`, `assert_message`,
  `assert_error`, `assert_no_errors`);
- the error and parity-signal types `BackendError`, `SetupError`,
  `RouteNotImplemented` and `UnsupportedField`.

`Env.http_requests` is the semver-covered request-observability API. Its
`HttpLogEntry` fields preserve discord.py transport arguments (`params`, `json`, and
`reason`); they are not wire-normalized query strings or encoded headers, and files are
not captured. `Env.http_log` remains the live 2.x tuple list and emits
`DeprecationWarning` on access.

The `pytest` plugin (the `simcord_env` fixture) is part of the public surface
too.

## What is intentionally internal

Everything else may change in any release, including a patch:

- **`Backend`** and its methods, state dictionaries and payload shapes. It is
  importable for advanced assertions, but is not semver-covered — treat reads as
  best-effort and expect churn.
- The **route table**, route handlers and the serializers in
  `simcord.backend.serializers`.
- The **gateway event payloads** simcord injects, and the `simcord.parity`
  drift-guard machinery.
- Any module beginning with an underscore (e.g. `simcord._dpy_internals`).

## Parity is a moving target, honestly tracked

SimCord deliberately fails **loudly** on anything it does not implement — an
unimplemented route raises [`RouteNotImplemented`](parity-matrix.md) and an
unrecognised request field raises `UnsupportedField`, rather than letting a test
pass against behaviour that diverges from real Discord. The
[parity matrix](parity-matrix.md) lists what is implemented, what is not yet
implemented, and what is deliberately out of scope; all three are generated and
verified in CI, so they cannot quietly drift as discord.py evolves.

These exception types are part of the public API precisely so your tests can
assert on them.

## Deprecation policy

From 1.0 onward, a public-API symbol is never removed or changed incompatibly
without first being deprecated for at least one minor release. A deprecated
symbol keeps working, emits a `DeprecationWarning` pointing at its replacement,
and is listed in the changelog; removal then waits for the next major version.
Anything documented above as intentionally internal carries no such guarantee
and may change in any release.
