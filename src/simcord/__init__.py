"""simcord: offline testing framework for discord.py bots.

Run your real, unmodified bot against a virtual in-memory Discord — no
network, no tokens, no Terms of Service concerns — and test prefix commands,
slash commands, components, permissions and events the way a user exercises
them.

Typical usage::

    import simcord

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))

        await alice.send(channel, "!ping")
        assert channel.last_message.content == "Pong!"
"""

from importlib.metadata import PackageNotFoundError, version

from . import _dpy_internals

_dpy_internals.verify()

from .actors import MemberActor  # noqa: E402
from .asserts import (  # noqa: E402
    assert_error,
    assert_message,
    assert_no_errors,
    assert_responded,
    assert_sent,
)
from .backend import Backend  # noqa: E402, F401  — importable for advanced use, but NOT public API:

# Backend's methods and payload shapes are internal and may change in any release.
from .backend.errors import BackendError, SetupError  # noqa: E402
from .builders import ChannelHandle, GuildHandle, RoleHandle, UserHandle  # noqa: E402
from .env import Env, run  # noqa: E402
from .http import RouteNotImplemented  # noqa: E402
from .results import InteractionResult, ResponseMessage  # noqa: E402

try:
    __version__ = version("simcord")
except PackageNotFoundError:  # running from a source checkout without dist metadata
    __version__ = "0.0.0+unknown"

__all__ = (
    "BackendError",
    "ChannelHandle",
    "Env",
    "GuildHandle",
    "InteractionResult",
    "MemberActor",
    "ResponseMessage",
    "RoleHandle",
    "RouteNotImplemented",
    "SetupError",
    "UserHandle",
    "assert_error",
    "assert_message",
    "assert_no_errors",
    "assert_responded",
    "assert_sent",
    "run",
)
