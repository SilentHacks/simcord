"""Disabled channel metadata is compatible; enabling is rejected atomically."""

import discord
import pytest

from simcord.http.router import UnsupportedField, dispatch


@pytest.mark.parametrize("kind", [0, 2, 4, 5, 13, 15])
@pytest.mark.parametrize("value", [True, 1, "false", None])
def test_channel_nsfw_enable_rejection_does_not_create_state(env, kind, value):
    backend = env.backend
    before_channels = set(backend.channels)
    before_events = [entry for entry in backend.transcript if entry[0] == "GATEWAY"]
    with pytest.raises(UnsupportedField) as caught:
        dispatch(
            backend,
            "POST",
            f"/guilds/{env.guild.id}/channels",
            json={"name": "blocked", "type": kind, "nsfw": value},
        )
    assert caught.value.fields == ["nsfw"]
    assert set(backend.channels) == before_channels
    assert [entry for entry in backend.transcript if entry[0] == "GATEWAY"] == before_events


async def test_native_channel_false_metadata_stays_disabled(env):
    guild = env.bot.get_guild(env.guild.id)
    channel = await guild.create_text_channel("ordinary", nsfw=False)
    await env.settle()
    assert not channel.is_nsfw()
    assert not env.bot.get_channel(channel.id).is_nsfw()
    with pytest.raises(UnsupportedField):
        await channel.edit(nsfw=True)
    fetched = await env.bot.fetch_channel(channel.id)
    assert isinstance(fetched, discord.TextChannel)
    assert not fetched.is_nsfw()
