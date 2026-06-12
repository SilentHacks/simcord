"""pytest plugin: ready-made fixtures for simcord.

Define a ``simcord_bot`` fixture in your conftest that builds your bot, and the
``simcord_env`` fixture hands you a running environment::

    # conftest.py
    import pytest
    from mybot import create_bot

    @pytest.fixture
    def simcord_bot():
        return create_bot()

    # test_bot.py
    async def test_ping(simcord_env):
        channel = simcord_env.create_guild().create_text_channel("general")
        ...

Requires the ``pytest`` extra (``pip install simcord[pytest]``).
"""

from __future__ import annotations

import pytest

from .env import Env, run

try:
    import pytest_asyncio
except ImportError:  # pragma: no cover - plugin is inert without the extra
    pytest_asyncio = None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach the env transcript to failing tests — "what the bot did"."""
    report = yield
    if report.when == "call" and report.failed:
        envs = [v for v in getattr(item, "funcargs", {}).values() if isinstance(v, Env)]
        for env in envs:
            text = env.transcript()
            if text:
                report.sections.append(("simcord transcript", text))
    return report


@pytest.fixture
def simcord_bot():
    raise pytest.UsageError(
        "Define a `simcord_bot` fixture in your conftest.py that returns your bot to use the `simcord_env` fixture."
    )


if pytest_asyncio is not None:

    @pytest_asyncio.fixture
    async def simcord_env(simcord_bot):
        async with run(simcord_bot) as env:
            yield env
