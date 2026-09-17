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

The example prints the live capability-gated preview URL and writes a PNG plus a JSON capture
report to stdout. The session exits once the capture finishes; add
`await preview.wait_closed()` before the context exits to keep it open for clicking around.

For more patterns, including selects, autocomplete, view timeouts, fault injection, and DMs,
see the [recipe cookbook](../docs/cookbook.md) and the
[documentation](https://simcord.readthedocs.io/).
