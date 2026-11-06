---
title: "Errors & diagnostics"
description: "Assert on errors your discord.py bot swallowed with env.errors and raise_errors(), inspect the REST call log, read the transcript of what the bot did, and inject API failures to test error handling."
---

# Errors & diagnostics

SimCord is built around a simple principle: **a bot bug should never pass silently.** This
guide covers the tools that make what your bot did — and what went wrong — visible and
assertable.

## Captured errors (`env.errors`)

Unhandled exceptions from command handlers, app-command callbacks and event listeners don't
crash the bot (just as they don't in production) — but SimCord captures them so the classic
"the bot silently failed" bug becomes assertable:

```python
await alice.send(channel, "!broken")
assert isinstance(simcord_env.errors[-1].original, discord.Forbidden)
assert simcord_env.errors[-1].original.code == 50013
```

`env.errors` is a list of the exceptions the bot raised, in order. (`CommandNotFound` is
excluded — a non-command message isn't a bug.)

### Errors fail tests by default

!!! warning "Uninspected errors are re-raised at teardown"
    Reading `env.errors` returns a shallow list snapshot and acknowledges only the
    currently captured prefix. A later error still fails teardown, including one
    captured during shutdown; previously acknowledged errors are not re-raised
    alongside it. Exception objects and their tracebacks are preserved.

Saving an empty snapshot does not acknowledge errors that arrive later, and mutating
a snapshot cannot change the history. Explicit `raise_errors()` still checks the
entire captured history, including errors already inspected.

You have three ways to handle this:

```python
# 1. Inspect — your assertions own correctness.
assert simcord_env.errors == []

# 2. Assert cleanliness explicitly, anywhere mid-test.
simcord_env.raise_errors()   # raises an ExceptionGroup of everything captured, or nothing

# 3. Opt out for a test that genuinely doesn't care.
async with simcord.run(bot, check_errors=False) as env:
    ...
```

`env.raise_errors()` always raises an `ExceptionGroup` of the **entire captured history**
(even errors acknowledged earlier), or does nothing if there were none. It acknowledges
only the current prefix in opt-in mode, including an empty prefix; later errors still fail
teardown. `check_errors=False` disables automatic checking in either mode. Teardown never
masks an exception already propagating from the test body.

`env.error_cursor` and `env.errors_since(cursor)` are non-consuming observations used by
Preview diagnostics. They do not acknowledge errors or disable teardown checking.

## Assertions

You can assert with plain `assert` on the [result and query objects](../api.md), but SimCord
also ships a few helpers whose **failure messages show what the bot actually did** — so a red
test explains itself. They're plain functions (no pytest required) importable from the top
level:

```python
from simcord import assert_sent, assert_responded, assert_error

await alice.send(channel, "!ping")
assert_sent(channel, content="Pong!")           # the channel's last visible message

result = await alice.slash(channel, "hello")
assert_responded(result, contains="hi", ephemeral=True)   # the interaction's response
```

Each field (`content`, `contains`, `embed_title`, `ephemeral`) is checked only when you pass
it. When an assertion fails, the message includes the real output — e.g. `assert_sent` prints
the channel's recent history, and `assert_responded` prints whether the bot deferred or opened
a modal instead:

```
AssertionError: last message did not match:
  content: expected 'Pong!', got 'Pnog!'
recent messages:
  - 'Pnog!'
```

For errors, `assert_error` replaces the clunky `any(isinstance(e, ...) for e in env.errors)`
idiom and unwraps discord.py's `CommandInvokeError.original` for you:

```python
env.inject_error("POST", "/channels/*/messages", status=403, code=50013)
await alice.send(channel, "!post")
assert_error(env, discord.Forbidden, code=50013)   # matches the wrapped original
assert_no_errors(env)                               # the symmetric "ran cleanly" check
```

`assert_error` reads `env.errors`, so it acknowledges the same errors as a direct read:
only the currently captured prefix, not errors that arrive later.

## The transcript

`env.transcript()` returns a human-readable, ordered record of everything that crossed
SimCord's two seams — each gateway event injected and each REST call the bot made:

```python
print(simcord_env.transcript())
# GATEWAY MESSAGE_CREATE            author=alice content='!ping'
# REST    POST /channels/123/...    content='Pong!'
```

The [pytest plugin attaches this to failing tests automatically](fixtures.md#the-pytest-plugin),
so a failure shows you exactly what the bot did, in order — usually enough to diagnose
without a debugger.

## HTTP request records (`env.http_requests`)

For fine-grained assertions, `env.http_requests` is the list of every REST call the bot made,
as [`simcord.HttpLogEntry`](../api.md#simcord.HttpLogEntry) records:

```python
posts = [
    entry
    for entry in simcord_env.http_requests
    if entry.method == "POST" and "/messages" in entry.path
]
assert len(posts) == 1
assert posts[0].json["content"] == "Pong!"
```

Each record exposes `method`, `path`, `params`, `json`, `reason`, and `denial`.
Transport arguments retain discord.py's values: query parameters are not wire-normalized
strings, and `reason` is not an encoded HTTP header value. Uploaded files are not captured.
The `json` field preserves every supplied JSON-compatible shape, including top-level arrays.
`params` and `json` are detached deep snapshots, so later mutations to source containers do
not alter a record. The request record is appended before fault injection and route handling,
so failed and unimplemented requests remain observable.

## Permission diagnostics

Explain effective permission bits independently of a failed operation:

```python
trace = simcord_env.explain_permissions(simcord_env.bot.user, channel)
effective = discord.Permissions(trace["effective"])
for step in trace["steps"]:
    print(step["source"], step["before"], step["after"])
```

The ordered trace covers base roles, owner/Administrator bypasses, `@everyone`,
aggregated role and member overwrites, and active timeout masking. Threads use
their parent's overwrites. This explains permission bits, not every REST policy:
thread state, channel access and role hierarchy can independently refuse an action.

Bot refusals add `DENIAL` entries to the transcript and structured `denial` detail
to the failing HTTP request record. Discord exception text and error JSON remain
unchanged. `env.permission_checks` returns detached check records with actor,
guild and channel IDs, required/missing flags, effective bits, outcome and reason.
Non-permission authorization refusals, such as role hierarchy, are attributed too.

```python
observed = simcord_env.observed_permissions()
print([name for name, used in observed if used])
```

Observed flags include refused checks. They describe only paths this environment
exercised, not a least-privilege guarantee for your entire bot.

### Silent degradations

Some successful requests lose an effect without a Discord error:

```python
for degradation in simcord_env.degradations:
    print(degradation["kind"])
simcord_env.assert_no_degradations()  # opt-in assertion
```

Records are detached snapshots. Modeled ordinary-send cases are missing TTS
permission (`tts_permission_dropped`), unmentionable role pings
(`unmentionable_role_ping_dropped`), ignored ephemeral flags
(`normal_message_ephemeral_ignored`) and known external custom emojis without
the external-emoji permission (`external_emoji_permission_stripped`).
An explicit allowed-mentions policy suppressing a ping is intentional, not a
degradation. Unknown emoji IDs are not guessed or rewritten. Successful TTS
creates retain `tts=True` in the create result/event but ordinary bot messages
read later have `tts=False`, matching the captured behavior.

### Suite reports and failure sweeps

```bash
pytest --simcord-observed-permissions
pytest --simcord-sweep
```

The observed-permissions option aggregates exercised flags across environments
and xdist workers. The sweep first runs each test normally, then reruns passing
tests with fresh function-scoped fixtures once per observed permission.
It discovers both `simcord_env` and environments created directly or through
`simcord.run`.

Revocation changes real bot-exclusive roles and bot member overwrites, not HTTP
fault responses. REST enforcement, interaction `app_permissions` and cached
`channel.permissions_for(guild.me)` therefore agree. A channel overwrite can
revoke a channel permission while `guild.me.guild_permissions` correctly retains
the guild-level grant.

Assertions that no longer hold under revocation are ignored. Unhandled bot
exceptions and timeouts are reported with the permission and source location;
findings make pytest exit unsuccessfully. Handled refusals are not findings.
Baseline failures remain ordinary test failures and are not swept.

Env-owned callback/task errors and settlement timeouts carry bot-ownership
evidence. Direct product calls can also produce findings when their traceback
reaches application code under pytest's root, outside the configured `testpaths`.
Keep test helpers under those test paths: a direct SDK helper refusal, or a
failure without application ownership evidence, is unsupported rather than an
invented bot failure. Broader external application code is still covered when
executed as Env-owned bot work. A baseline using only permissionless interaction
callbacks may observe no flags; that is not permission-revocation coverage.

The report separates exercised and unsupported cases. Broader-scoped user
fixtures, reused environments, bot-owned guilds, Administrator bypasses,
and guild-scoped grants that cannot be removed without changing other members
are unsupported. Removing Administrator could revoke unrelated permissions, so
it is never presented as a faithful one-flag sweep. Harness/setup failures are
reported as unsupported rather than
invented bot failures. An unsupported case is not evidence that your bot handles
revocation.

## Injecting API failures

Real bots have to survive Discord hiccups. `env.inject_error` makes matching REST calls fail
so you can test the bot's error handling:

```python
async def test_handles_api_outage(simcord_env):
    simcord_env.inject_error("POST", "/channels/*/messages", status=500)
    ...   # assert your bot degrades gracefully / retries / reports
```

Parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `method` | — | HTTP method to match, or `"*"` for any. |
| `path` | — | `fnmatch` pattern against the API path, e.g. `"/channels/*/messages"`. |
| `status` | `500` | HTTP status to return. |
| `code` | `0` | Discord JSON error code (e.g. `50013`). |
| `message` | injected default | The error message. |
| `times` | `1` | How many matching calls fail; `None` keeps the fault active for the rest of the test. |

```python
# Fail the first two attempts with a rate-limit-style 429, then let it through:
simcord_env.inject_error("POST", "/channels/*/messages", status=429, times=2)

# Make every ban call fail for the whole test with a realistic 50013:
simcord_env.inject_error("PUT", "/guilds/*/bans/*", status=403, code=50013, times=None)
```

Because failures surface as genuine `discord.Forbidden` / `discord.HTTPException` with real
codes, your `except discord.HTTPException:` branches are exercised exactly as in production.

## Settlement timeouts and recovery

`settle_timeout` defaults to five seconds for actor operations, `env.settle()`, and the
pre-restart drain; `env.settle(timeout=...)` overrides that individual settle. Startup
READY settlement has its own fixed five-second budget, independent of `settle_timeout`.
Restart uses a fresh fixed five-second READY budget and then a separate fixed five-second
guild-replay budget. These are settlement budgets, not a timeout on `setup_hook` or login.

`TimeoutError` reports the operation, effective timeout, bot-owned task or callback, wait
reason, and any recognized waits left parked. A timeout or cancellation does not release
ownership or pretend work completed; state may already have changed. Release the real
dependency, then invoke a later actor action or `env.settle()` to join the resumed work.
Do not rerun the actor action to recover.

## Unimplemented routes

If your bot hits a route SimCord doesn't implement yet, it raises `RouteNotImplemented`
naming the route — never a silent fake success. That signal is *not* a `discord.HTTPException`,
so a broad `except discord.HTTPException` in your bot can't swallow it. If you hit one,
[open a parity-gap issue](https://github.com/SilentHacks/simcord/issues/new?template=parity-gap.md).
See the [parity matrix](../parity-matrix.md).

## Next

- [Fixtures & configuration](fixtures.md) — `check_errors` and the rest.
- [Permissions](permissions.md) — the source of most `50013` errors.
- [Recipes](../cookbook.md) — error-path patterns.
