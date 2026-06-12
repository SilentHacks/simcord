import pytest

from .bot import create_bot


@pytest.fixture
def simcord_bot():
    """The bot under test — picked up by discord-py-test's `simcord_env` fixture."""
    return create_bot()
