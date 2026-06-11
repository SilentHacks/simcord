import pytest

from .bot import create_bot


@pytest.fixture
def dpt_bot():
    """The bot under test — picked up by discord-py-test's `dpt_env` fixture."""
    return create_bot()
