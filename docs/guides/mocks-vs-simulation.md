---
title: "Mocking discord.py vs simulating Discord"
description: "Choose between AsyncMock unit tests, SimCord integration tests, and a real Discord test server. Understand which bugs each approach can and cannot catch."
---

# Mocking discord.py vs simulating Discord

Mocks and simulation solve different testing problems. Use mocks to isolate your own services. Use SimCord when behavior depends on discord.py or Discord state.

## Direct answer

A callback test with `AsyncMock` proves that your function called a method. A SimCord test proves that a user action traveled through discord.py and produced valid Discord behavior.

```python
# Mock test: verifies one call
ctx.send = AsyncMock()
await ping.callback(ctx)
ctx.send.assert_awaited_once_with("Pong!")
```

```python
# SimCord test: verifies observable behavior
await alice.send(channel, "!ping")
assert channel.last_message.content == "Pong!"
```

The second test also covers prefix resolution, checks, converters, command dispatch, HTTP serialization, gateway parsing, listeners, and cache updates.

## Comparison

| Capability | Direct mocks | SimCord | Real Discord server |
| --- | --- | --- | --- |
| Runs offline | Yes | Yes | No |
| Needs a token | No | No | Yes |
| Uses real discord.py dispatch | Usually no | Yes | Yes |
| Computes permissions and hierarchy | You program the mock | Yes | Yes |
| Tests interactions and component state | You program the mock | Yes | Yes |
| Deterministic and fast | Yes | Yes | No |
| Proves deployed credentials work | No | No | Yes |
| Subject to rate limits | No | No | Yes |

## Use direct mocks for isolated code

Mocks are appropriate when a command delegates to an application-owned seam:

```python
weather.current.return_value = Forecast(temperature=18)
await service.render_forecast("London")
weather.current.assert_awaited_once_with("London")
```

You control the service interface, so an isolated test is clear and stable.

## Use SimCord for Discord behavior

Choose SimCord when the failure could live in:

- Command registration or dispatch
- Argument conversion
- Checks and cooldowns
- Permission resolution
- Interaction acknowledgement
- Gateway event handling
- Discord.py cache state
- Views, buttons, selects, or modals
- Error handlers

Hand-building mocks for these paths duplicates discord.py's behavior inside the test. The test may pass because the mock agrees with the implementation rather than because the bot works.

## Keep a small deployment check when needed

A real Discord application is still useful for narrowly checking credentials, command deployment, or behavior outside the [parity matrix](../parity-matrix.md). Do not make a live Discord guild the default integration-test environment.

## Recommended test shape

1. Pure unit tests for business rules.
2. Direct mocks for application-owned integrations.
3. SimCord tests for Discord-facing behavior.
4. A minimal live smoke check only when deployment itself must be proven.

## Continue

- [How to test a discord.py bot with pytest](testing-discord-py-bots.md)
- [Test without a token](test-without-token.md)
- [Core concepts](../concepts.md)
- [Parity matrix](../parity-matrix.md)
