import pytest

from .bot import create_bot


@pytest.fixture
def simcord_bot():
    """The bot under test — picked up by simcord's `simcord_env` fixture."""
    return create_bot()
