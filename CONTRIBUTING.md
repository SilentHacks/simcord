# Contributing

Thanks for helping make Discord bot testing better!

## Development setup

```bash
git clone https://github.com/SilentHacks/discord-py-test
cd discord-py-test
python -m venv .venv
.venv/bin/pip install -e .[pytest]
.venv/bin/pytest
```

## Guidelines

- **Fidelity first.** The backend must behave like real Discord: real error
  codes, real validation limits, real event payload shapes. When in doubt,
  check the [Discord API docs](https://discord.com/developers/docs) and
  discord.py's `discord.types` definitions.
- **Never fake success silently.** If a route or feature isn't implemented,
  it must raise `RouteNotImplemented` so users know.
- Every new route or feature needs an integration test that drives a real
  `commands.Bot` through it.
- Keep public API additions on the `Env` / handle objects — no module-global
  state.

## Reporting bugs

A failing test using `discord_py_test` is the perfect bug report. If your bot
hits an unimplemented route, the error message names it — include that in the
issue.
