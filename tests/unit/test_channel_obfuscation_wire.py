"""Wire projection boundaries, independent of the Discord parser cache."""

import copy
from types import SimpleNamespace

import discord

from simcord.backend import serializers
from simcord.gateway import FakeGateway


async def test_hidden_wire_projection_and_interaction_exemption(env):
    hidden = env.guild.create_text_channel(
        "secret",
        topic="sensitive",
        overwrites={env.guild.default_role: discord.PermissionOverwrite(view_channel=False)},
    )
    backend = env.backend
    original = dict(serializers.channel_payload(backend, backend.get_channel(hidden.id)))
    before = copy.deepcopy(original)
    delivered = []
    gateway = FakeGateway(
        backend,
        SimpleNamespace(
            intents=discord.Intents.all(),
            parsers={"CHANNEL_UPDATE": delivered.append, "INTERACTION_CREATE": delivered.append},
        ),
    )
    gateway.feed("CHANNEL_UPDATE", original)
    projected = delivered[0]
    assert projected["name"] == "___hidden___"
    assert projected["flags"] == 1 << 17
    assert projected["topic"] is None
    assert projected["last_message_id"] is None
    assert projected["permission_overwrites"] == [
        {
            "id": str(env.guild.id),
            "type": 0,
            "allow": "0",
            "deny": str(discord.Permissions.view_channel.flag),
        }
    ]
    for key in ("id", "type", "position", "parent_id", "guild_id"):
        assert projected[key] == original[key]
    assert original == before
    interaction = {
        "type": 2,
        "guild_id": str(env.guild.id),
        "channel": original,
        "data": {"resolved": {"channels": {str(hidden.id): original}}},
    }
    gateway.feed("INTERACTION_CREATE", interaction)
    assert delivered[-1] == interaction
    assert delivered[-1]["channel"]["name"] == "secret"
    assert backend.get_channel(hidden.id).topic == "sensitive"


async def test_projection_keeps_intent_gating_and_does_not_scan_on_messages(env):
    channel = env.guild.create_text_channel("visible")
    delivered = []
    intents = discord.Intents.default()
    intents.guild_messages = False
    gateway = FakeGateway(
        env.backend,
        SimpleNamespace(
            intents=intents,
            parsers={"MESSAGE_CREATE": delivered.append},
        ),
    )

    # A permission scan on every message would be both unnecessary and expensive.
    def forbidden_scan(*args):
        raise AssertionError("ordinary message triggered a whole-guild visibility scan")

    gateway._refresh_visibility = forbidden_scan
    gateway.feed(
        "MESSAGE_CREATE",
        {
            "guild_id": str(env.guild.id),
            "channel_id": str(channel.id),
        },
    )
    assert delivered == []
    intents.guild_messages = True
    intents.message_content = True
    gateway.feed(
        "MESSAGE_CREATE",
        {
            "guild_id": str(env.guild.id),
            "channel_id": str(channel.id),
        },
    )
    assert len(delivered) == 1
