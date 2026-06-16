"""Cover simcord's own pytest plugin by running a sub-pytest with ``pytester``.

The rest of the suite uses a hand-rolled ``env`` fixture, so the shipped
``simcord_env`` fixture, the ``simcord`` marker, the missing-``simcord_bot``
guard, and the failing-test transcript hook are only exercised here.
"""

from __future__ import annotations

_BOT_CONFTEST = """
import pytest
import discord
from discord.ext import commands


@pytest.fixture
def simcord_bot():
    intents = discord.Intents.all()
    return commands.Bot(command_prefix="!", intents=intents)
"""


def test_simcord_env_fixture_and_transcript_hook(pytester):
    pytester.makeconftest(_BOT_CONFTEST)
    pytester.makepyfile(
        """
        import pytest


        @pytest.mark.simcord(strict_sync=False)
        async def test_marker_options_forwarded(simcord_env):
            assert simcord_env.create_guild() is not None


        async def test_without_marker(simcord_env):
            assert simcord_env is not None


        async def test_failure_attaches_transcript(simcord_env):
            simcord_env.create_guild()
            raise AssertionError("boom")
        """
    )
    result = pytester.runpytest("-o", "asyncio_mode=auto", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, failed=1)
    # The failing test's report carries the env transcript section.
    result.stdout.fnmatch_lines(["*simcord transcript*"])


def test_missing_simcord_bot_fixture_raises_usage_error(pytester):
    # No conftest defining simcord_bot: requesting simcord_env must fail loudly
    # with a helpful UsageError rather than a cryptic fixture error.
    pytester.makepyfile(
        """
        async def test_needs_bot(simcord_env):
            pass
        """
    )
    result = pytester.runpytest("-o", "asyncio_mode=auto", "-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*Define a `simcord_bot` fixture*"])
