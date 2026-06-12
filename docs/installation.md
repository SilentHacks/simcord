---
title: "Installation"
description: "Install SimCord, the discord.py testing framework. Requirements, the pytest extra, and discord.py version compatibility."
---

# Installation

## Requirements

| Requirement | Version |
| --- | --- |
| Python | 3.11 or newer |
| discord.py | 2.7 or newer (`>=2.7,<3`) |

SimCord has **zero runtime dependencies beyond discord.py itself**. It deliberately never
opens a socket, so there is no networking stack to install or configure.

## Install with the pytest plugin

Most users want the bundled pytest fixtures. Install the `pytest` extra:

```bash
pip install simcord[pytest]
```

This pulls in `pytest` and `pytest-asyncio` and registers the `simcord_env` and
`simcord_bot` [fixtures](guides/fixtures.md) automatically — no plugin activation needed.

=== "uv"

    ```bash
    uv add --dev "simcord[pytest]"
    ```

=== "Poetry"

    ```bash
    poetry add --group dev "simcord[pytest]"
    ```

=== "pip"

    ```bash
    pip install "simcord[pytest]"
    ```

## Install the core only

If you drive the environment yourself with [`simcord.run`](guides/fixtures.md#without-pytest)
— for example under a different test runner — install the base package:

```bash
pip install simcord
```

You can still write tests; you just provide your own async test harness instead of the
pytest fixtures.

## Verify the install

```bash
python -c "import simcord; print(simcord.__version__)"
```

SimCord runs an **import-time self-check** against your installed discord.py: every private
discord.py touchpoint it relies on is verified to still exist. If a discord.py upgrade ever
moves something out from under it, the import fails immediately with a clear message rather
than misbehaving silently mid-test.

## discord.py compatibility

SimCord targets the discord.py `2.7+` line and is tested in CI against discord.py's
released versions **and** its `master` branch weekly, so drift is caught early. Because the
framework reuses discord.py's own `discord.types` TypedDicts for every payload, wire-shape
mismatches against a new release surface as static type errors before they reach you.

## Next steps

- [Quickstart](quickstart.md) — wire SimCord into your project and write your first test.
- [Core concepts](concepts.md) — the builders/actors/queries model.
