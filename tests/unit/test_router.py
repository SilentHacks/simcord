import pytest

from simcord.backend import Backend
from simcord.http import router
from simcord.http import routes as _routes  # noqa: F401  — registers handlers


def test_unknown_route_raises_with_route_name():
    backend = Backend()
    with pytest.raises(router.RouteNotImplemented, match="GET /made/up/route"):
        router.dispatch(backend, "GET", "/made/up/route")


def test_literal_segments_beat_parameters():
    backend = Backend()
    guild = backend.create_guild("g")
    channel = backend.create_channel(guild.id, "general")
    message = backend.create_message(channel.id, backend.bot_user.id, "hi")
    backend.set_pinned(channel.id, message.id, True)
    # ".../messages/pins" must not be captured by ".../messages/{message_id}".
    result = router.dispatch(backend, "GET", f"/channels/{channel.id}/messages/pins")
    assert [int(i["message"]["id"]) for i in result["items"]] == [message.id]


def test_http_log_records_calls():
    backend = Backend()
    guild = backend.create_guild("g")
    router.dispatch(backend, "GET", f"/guilds/{guild.id}")
    assert backend.http_log[-1][:2] == ("GET", f"/guilds/{guild.id}")


def test_snowflakes_are_monotonic_and_timestamped():
    backend = Backend()
    flakes = [backend.snowflake() for _ in range(100)]
    assert flakes == sorted(flakes) and len(set(flakes)) == 100
    import discord

    # Embedded timestamps decode to the fixed virtual epoch (2026+).
    assert discord.utils.snowflake_time(flakes[0]).year >= 2026
