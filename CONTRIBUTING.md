# Contributing

Thanks for helping make Discord bot testing better!

## Development setup

The project uses [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/SilentHacks/simcord
cd simcord
uv sync --extra dev
uv run pre-commit install --install-hooks   # optional: run lint/format/pyright on commit
uv run pytest tests examples
uv run ruff check src tests examples benchmarks scripts && uv run ruff format --check src tests examples benchmarks scripts
uv run pyright src
```

The pre-commit hooks mirror the CI `lint` job (ruff on commit, pyright on push)
using the project's own pinned tools, so a clean commit means a green CI lint.

## Project layout

- `src/simcord/backend/`: the virtual Discord with dataclass models, wire-format
  serializers (annotated against `discord.types`), the permissions engine, error catalog,
  in-memory CDN, and the central `Backend` store.
- `src/simcord/http/`: the route table and per-resource REST handlers, plus the
  fake `HTTPClient`/webhook adapter.
- `src/simcord/gateway.py`: feeds payloads into discord.py's real parsers.
- `src/simcord/{env,builders,actors,results,interactions}.py`: the public API.
- `src/simcord/_dpy_internals.py`: **every** touch of a private discord.py API,
  behind an import-time self-check. New private-API usage goes here, nowhere else.

## Guidelines

- **Fidelity first.** The backend must behave like real Discord: real error codes, real
  validation limits, real payload shapes. When in doubt, check the
  [Discord API docs](https://discord.com/developers/docs) and discord.py's
  `discord.types` definitions.
- **Never fake success silently.** Unimplemented routes must raise `RouteNotImplemented`.
- Every new route or feature needs an integration test in `tests/integration/` driving
  the sample bot (`tests/fixtures/sample_bot/`) through it, plus a parity-matrix update:
  run `uv run python -m simcord.parity docs/parity-matrix.md` to regenerate the
  route inventory (a unit test enforces it), and update the curated feature table by hand.
  Pure backend logic (permissions, routing) gets unit tests in `tests/unit/`.
- New state mutations live on `Backend`, paired with their gateway emit; route handlers
  parse, permission-check (`ctx.require_*_permissions`), call one backend method, and
  serialize.
- Public API lives on `Env` and the handle objects. There is no module-global state.
- Add a towncrier news fragment in `changes/` for user-visible changes
  (e.g. `changes/42.feature.md`).

## Reporting bugs

A failing test using `simcord` is the perfect bug report. If your bot hits an
unimplemented route, the error message names it. Include that in the issue.
