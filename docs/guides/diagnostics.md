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
    If the bot raised errors during a test and the test never inspected them, `simcord.run`
    re-raises them as an `ExceptionGroup` when the environment closes — so a swallowed bug
    can't slip past a green test. **Reading `env.errors` counts as inspecting**: your
    assertions take over from there.

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

`env.raise_errors()` is the one-call way to assert "the bot ran cleanly": it raises an
`ExceptionGroup` of everything captured (even a single error) and does nothing if there were
none.

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

`assert_error` reads `env.errors`, which counts as inspecting them (see below), so it also
satisfies the teardown guard.

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

## The HTTP log (`env.http_log`)

For fine-grained assertions, `env.http_log` is the list of every REST call the bot made, as
`(method, path, json_body)` tuples:

```python
posts = [c for c in simcord_env.http_log if c[0] == "POST" and "/messages" in c[1]]
assert len(posts) == 1                       # the bot sent exactly one message
assert posts[0][2]["content"] == "Pong!"
```

This is how you assert the bot **didn't** do something (e.g. didn't double-post), or made
exactly the calls you expect.

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
