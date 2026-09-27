# SimCord example

A small but realistic community bot and its test suite, showing how to drive
each interaction surface with SimCord.

- **`bot.py`**: the bot under test. `create_bot()` builds a `commands.Bot` with
  a prefix command (`!ping`), a manual daily-reward cooldown (`!daily`), a
  permission-gated slash command (`/ban`), a modal (`/feedback`), a
  button-to-modal panel edit flow (`!panel`), a button confirm flow (`/purge`),
  and a persistent self-assign role menu.
- **`preview_example.py`**: an executable panel → modal → source-message edit scenario that
  captures a PNG and consumes `PreviewCapture` diagnostics.
- **`conftest.py`**: defines the `simcord_bot` fixture the pytest plugin picks up.
- **`test_bot.py`**: one test per feature, each in the builders, actors, and queries style:
  arrange a world, act as a user, assert on what the bot did.

## Run it

```bash
python -m pip install "simcord[pytest]"
pytest examples
```

Or, from a checkout of this repo using [uv](https://docs.astral.sh/uv/):

```bash
uv run pytest examples
```

To run the browser preview example:

```bash
python -m pip install "simcord[screenshot]"
playwright install --with-deps chromium
python examples/preview_example.py
```

Or, from a checkout of this repo:

```bash
uv sync --extra screenshot
uv run playwright install --with-deps chromium
uv run python examples/preview_example.py
```

The example writes a PNG plus a structured JSON capture report to stdout — so
`python examples/preview_example.py | jq` works without printing a capability URL.
Inspect `complete` and `diagnostics` before sharing a report; a local PNG is not a
certified Discord comparison. Pass `--keep-open` only for interactive use: it prints
the live capability URL to stderr and calls `preview.wait_closed()`. See the
[protocol-2 migration and private comparison workflow](../docs/guides/preview.md#migrating-preview-consumers-to-protocol-2).

For more patterns, including selects, autocomplete, view timeouts, fault injection, and DMs,
see the [recipe cookbook](../docs/cookbook.md) and the
[documentation](https://simcord.readthedocs.io/).
