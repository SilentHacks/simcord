---
title: "Stability & versioning"
description: "What SimCord's public API covers, what stays internal, and how versioning works on the road to 1.0."
---

# Stability & versioning

SimCord follows [semantic versioning](https://semver.org/). Once 1.0 lands, the
**public API** below is covered by that promise: no breaking change to it without
a major version bump. The surface is already settled, and the two gates 1.0 waited
on are now in place: property-based fuzzing of the honesty layer (every request
body key is applied or raises `UnsupportedField`, never silently dropped) and a
[performance baseline](performance.md) guarding the offline-speed value
proposition.

## Supported discord.py

SimCord targets **discord.py 2.7.x** (`discord.py>=2.7,<3`) and is exercised
against it in CI. Because a faithful fake must shadow a few discord.py internals
(view timeout tasks, parser entry points), simcord verifies them at import via
`simcord._dpy_internals.verify()` and fails **loudly** with an `ImportError`
naming what moved, rather than miscompiling silently against an untested release.
The `<3` ceiling is deliberate: a new discord.py major may move those internals,
so the range widens only once a release has been tested.

## Public API

The public surface is exactly what `simcord` exports from its top-level package
(everything in `simcord.__all__`):

- the entry point `run` and the `Env` it yields;
- the world builders — `GuildHandle`, `ChannelHandle`, `UserHandle`,
  `RoleHandle` — and the `MemberActor` that drives simulated users;
- the result objects `ResponseMessage` and `InteractionResult`;
- the assertion helpers (`assert_responded`, `assert_sent`, `assert_message`,
  `assert_error`, `assert_no_errors`);
- the error and parity-signal types `BackendError`, `SetupError`,
  `RouteNotImplemented` and `UnsupportedField`.

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
