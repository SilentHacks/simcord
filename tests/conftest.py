import sys
from pathlib import Path

import pytest
import pytest_asyncio

import simcord

# Make `fixtures.sample_bot` importable both from pytest and as bot extensions.
sys.path.insert(0, str(Path(__file__).parent))

from fixtures.sample_bot import create_bot

# Enables the `pytester` fixture so tests/unit/test_pytest_plugin.py can run a
# sub-pytest that exercises simcord's own pytest plugin (simcord_env, the marker,
# and the failing-test transcript hook).
pytest_plugins = ["pytester"]


@pytest_asyncio.fixture
async def env():
    bot = create_bot()
    async with simcord.run(bot) as env:
        env.create_guild()
        yield env


@pytest.fixture
def channel(env):
    return env.guild.create_text_channel("general")


@pytest.fixture
def alice(env):
    return env.guild.add_member(env.create_user("alice"))
