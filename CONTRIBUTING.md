# Contributing

Thanks for helping make Discord bot testing better!

## Development setup

```bash
git clone https://github.com/SilentHacks/discord-py-test
cd discord-py-test
python -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/pytest tests examples
.venv/bin/ruff check src tests examples && .venv/bin/ruff format --check src tests examples
```

## Project layout

- `src/discord_py_test/backend/` — the virtual Discord: dataclass models, wire-format
  serializers (annotated against `discord.types`), the permissions engine, error catalog,
  in-memory CDN, and the central `Backend` store.
- `src/discord_py_test/http/` — the route table and per-resource REST handlers, plus the
  fake `HTTPClient`/webhook adapter.
- `src/discord_py_test/gateway.py` — feeds payloads into discord.py's real parsers.
- `src/discord_py_test/{env,builders,actors,results,interactions}.py` — the public API.
- `src/discord_py_test/_dpy_internals.py` — **every** touch of a private discord.py API,
  behind an import-time self-check. New private-API usage goes here, nowhere else.

## Guidelines

- **Fidelity first.** The backend must behave like real Discord: real error codes, real
  validation limits, real payload shapes. When in doubt, check the
  [Discord API docs](https://discord.com/developers/docs) and discord.py's
  `discord.types` definitions.
- **Never fake success silently.** Unimplemented routes must raise `RouteNotImplemented`.
- Every new route or feature needs an integration test driving a real `commands.Bot`
  through it, and a row in `docs/parity-matrix.md`.
- Public API lives on `Env` and the handle objects — no module-global state.
- Add a towncrier news fragment in `changes/` for user-visible changes
  (e.g. `changes/42.feature.md`).

## Reporting bugs

A failing test using `discord_py_test` is the perfect bug report. If your bot hits an
unimplemented route, the error message names it — include that in the issue.
