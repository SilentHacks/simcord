import sys
from pathlib import Path

import pytest
import pytest_asyncio

import simcord as dpt

# Make `fixtures.sample_bot` importable both from pytest and as bot extensions.
sys.path.insert(0, str(Path(__file__).parent))

from fixtures.sample_bot import create_bot


@pytest_asyncio.fixture
async def env():
    bot = create_bot()
    async with dpt.run(bot) as env:
        env.create_guild()
        yield env


@pytest.fixture
def channel(env):
    return env.guild.create_text_channel("general")


@pytest.fixture
def alice(env):
    return env.guild.add_member(env.create_user("alice"))
