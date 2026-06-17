"""Shared setup for the performance suite.

Benchmarks measure the synchronous in-memory hot path — ``router.dispatch`` over a
populated ``Backend`` — which is what makes simcord's "fast & offline" claim
concrete. No bot or event loop is needed except for the env-setup benchmark.
"""

from __future__ import annotations

from simcord.backend import Backend
from simcord.enums import ChannelType


def backend_with_channel(n_messages: int = 0) -> tuple[Backend, int, int]:
    """A backend holding a guild, a text channel and ``n_messages`` bot messages."""
    backend = Backend()
    guild = backend.create_guild("Bench Guild")
    channel = backend.create_channel(guild.id, "general", type=ChannelType.TEXT)
    for i in range(n_messages):
        backend.create_message(channel.id, backend.bot_user.id, f"message {i}")
    return backend, guild.id, channel.id
