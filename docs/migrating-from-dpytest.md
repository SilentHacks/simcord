# Migrating from dpytest

[dpytest](https://github.com/CraftSpider/dpytest) pioneered this approach — faking the
HTTP layer and injecting gateway events. `simcord` covers the modern surface
dpytest never did (slash commands, components, modals, autocomplete, permissions with
real error codes) and replaces its module-global API with explicit objects.

## Concept mapping

| dpytest | simcord |
| --- | --- |
| `dpytest.configure(bot)` | `async with dpt.run(bot) as env:` (or the `simcord_env` fixture) |
| `dpytest.message("!ping")` | `await alice.send(channel, "!ping")` |
| `dpytest.verify().message().content("Pong!")` | `assert channel.last_message.content == "Pong!"` |
| `dpytest.get_config().guilds[0]` | `env.guild` / `env.create_guild()` |
| `dpytest.member_join()` | `guild.add_member(env.create_user("alice"))` |
| `dpytest.empty_queue()` | not needed — actors settle automatically |
| — (unsupported) | `alice.slash(...)`, `alice.click(...)`, `alice.submit_modal(...)`, … |

## Key differences

- **No global state.** Everything hangs off the `Env` returned by `dpt.run`; multiple
  environments can coexist.
- **Explicit actors.** Messages are sent *by someone, from somewhere* — which is what
  makes permission-sensitive bugs testable.
- **Assertions are plain Python** against real `discord.Message` objects and backend
  state, with pytest's normal introspection — no verification builder DSL.
- **Strictness.** Unsynced slash commands, disabled buttons, hidden channels and
  oversized payloads fail your tests, because they fail on Discord.
