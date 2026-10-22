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

Per-test options for the environment go through the ``simcord`` marker, whose
keyword arguments are forwarded to :func:`simcord.run`::

    @pytest.mark.simcord(strict_sync=False)
    async def test_unsynced_command(simcord_env):
        ...

Requires the ``pytest`` extra (``pip install simcord[pytest]``).
"""

from __future__ import annotations

import pytest

from .env import Env

try:
    import pytest_asyncio
except ImportError:  # pragma: no cover - plugin is inert without the extra
    pytest_asyncio = None


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "simcord(**options): keyword options forwarded to simcord.run() for the simcord_env fixture",
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    """Select clocks in the actual test context, never an async fixture task."""
    envs = {
        value for value in getattr(item, "funcargs", {}).values() if isinstance(value, Env) and value._started
    }
    if len(envs) == 1:
        with next(iter(envs)).time_context():
            return (yield)
    return (yield)


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
    async def simcord_env(simcord_bot, request):
        marker = request.node.get_closest_marker("simcord")
        options = marker.kwargs if marker else {}
        env = Env(simcord_bot, **options)
        await env.start()
        try:
            yield env
        finally:
            await env.shutdown()
            if env.check_errors:
                env._raise_errors_since(env._errors_acknowledged)
