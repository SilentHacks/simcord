"""Every backend getter raises a typed BackendError for a missing entity — the
not-found branches the happy-path tests never take."""

from __future__ import annotations

import pytest

import simcord


async def test_unknown_entities_raise(env):
    backend = env.backend
    guild_id = env.guild.id

    lookups = [
        lambda: backend.get_user(999_999),
        lambda: backend.get_guild(999_999),
        lambda: backend.get_channel(999_999),
        lambda: backend.get_message(999_999, 999_999),
        lambda: backend.get_role(guild_id, 999_999),
        lambda: backend.get_member(guild_id, 999_999),
    ]
    for lookup in lookups:
        with pytest.raises(simcord.BackendError):
            lookup()
