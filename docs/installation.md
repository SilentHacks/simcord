---
title: "Installation"
description: "Install SimCord, the discord.py testing framework, with optional pytest, local preview, and screenshot extras."
---

# Installation

## Requirements

| Requirement | Version |
| --- | --- |
| Python | >=3.11 (tested on 3.11–3.14) |
| discord.py | >=2.7.1,<3 |

SimCord has no runtime dependencies beyond discord.py itself for the base simulation. It deliberately
never opens a Discord socket or needs a token.

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

You can still write tests; you just provide your own async test harness instead of the pytest fixtures.


## Install the local preview

The optional [component preview](guides/preview.md) serves an authenticated browser page on
loopback and renders real callbacks without connecting to Discord:

```bash
python -m pip install "simcord[preview]"
```

For screenshots, install Playwright and its browser explicitly:

```bash
python -m pip install "simcord[screenshot]"
playwright install --with-deps chromium
```

(`--with-deps` installs the system libraries Chromium needs; plain `playwright install chromium` is
enough when those dependencies are already present.)

Missing `aiohttp`, Pillow, Playwright, or browser binaries produce direct installation guidance
when the feature is used. They are never imported or downloaded by a base `import simcord`.

## Verify the install

```bash
python -c "import simcord; print(simcord.__version__)"
```

SimCord runs an **import-time self-check** against your installed discord.py: every private
discord.py touchpoint it relies on is verified to still exist. If a discord.py upgrade ever
moves something out from under it, the import fails immediately with a clear message rather
than misbehaving silently mid-test.

## discord.py compatibility

SimCord targets discord.py `>=2.7.1,<3`. The locked CI matrix tests 2.7.1,
while a separate weekly workflow runs against upstream `master`; other released
2.x versions in the declared range are not each continuously tested. Because
the framework reuses discord.py's own `discord.types` TypedDicts for every payload,
wire-shape mismatches against a new release surface as static type errors before
they reach you.

## Next steps

- [Quickstart](quickstart.md) — wire SimCord into your project and write your first test.
- [Core concepts](concepts.md) — the builders/actors/queries model.
