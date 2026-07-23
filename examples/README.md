# SimCord example

A small but realistic community bot and its test suite, showing how to drive
each interaction surface with SimCord.

- **`bot.py`**: the bot under test. `create_bot()` builds a `commands.Bot` with
  a prefix command (`!ping`), a manual daily-reward cooldown (`!daily`), a
  permission-gated slash command (`/ban`), a modal (`/feedback`), a button
  confirm flow (`/purge`), and a persistent self-assign role menu (`!panel`).
- **`conftest.py`**: defines the `simcord_bot` fixture the pytest plugin picks up.
- **`test_bot.py`**: one test per feature, each in the builders, actors, and
  queries style: arrange a world, act as a user, assert on what the bot did.

## Run it

```bash
python -m pip install "simcord[pytest]"
pytest examples
```

Or, from a checkout of this repo using [uv](https://docs.astral.sh/uv/):

```bash
uv run pytest examples
```

For more patterns, including selects, autocomplete, view timeouts, fault injection, and DMs,
see the [recipe cookbook](../docs/cookbook.md) and the
[documentation](https://simcord.readthedocs.io/).
