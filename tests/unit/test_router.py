import pytest

from simcord import HttpLogEntry
from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.http import router
from simcord.http import routes as _routes  # noqa: F401  — registers handlers


def test_unknown_route_raises_with_route_name():
    backend = Backend()
    with pytest.raises(router.RouteNotImplemented, match="GET /made/up/route"):
        router.dispatch(backend, "GET", "/made/up/route")
    assert isinstance(backend.http_requests[-1], HttpLogEntry)


def test_literal_segments_beat_parameters():
    backend = Backend()
    guild = backend.create_guild("g")
    channel = backend.create_channel(guild.id, "general")
    message = backend.create_message(channel.id, backend.bot_user.id, "hi")
    backend.set_pinned(channel.id, message.id, True)
    # ".../messages/pins" must not be captured by ".../messages/{message_id}".
    result = router.dispatch(backend, "GET", f"/channels/{channel.id}/messages/pins")
    assert [int(i["message"]["id"]) for i in result["items"]] == [message.id]


def test_http_request_records_defaults_and_coexists_with_legacy_tuples():
    backend = Backend()
    guild = backend.create_guild("g")
    path = f"/guilds/{guild.id}"
    router.dispatch(backend, "GET", path)
    entry = backend.http_requests[-1]
    assert (entry.method, entry.path, entry.params, entry.json, entry.reason) == (
        "GET",
        path,
        {},
        None,
        None,
    )
    assert backend.http_log[-1] == ("GET", path, None)

    payload = {"name": "g"}
    router.dispatch(backend, "GET", path, json=payload)
    assert backend.http_requests[-1].json is payload
    assert backend.http_log[-1] == ("GET", path, payload)


def test_http_request_records_snapshot_transport_values_before_fault():
    backend = Backend()
    guild = backend.create_guild("g")
    path = f"/guilds/{guild.id}"
    params = {"limit": 3}
    payload = ["raw", 1]
    backend.faults.append(
        {
            "method": "GET",
            "path": path,
            "status": 500,
            "code": 0,
            "message": "boom",
            "times": 1,
        }
    )
    with pytest.raises(BackendError):
        router.dispatch(backend, "GET", path, params=params, json=payload, reason="cleanup needed")
    entry = backend.http_requests[-1]
    assert entry.params == params and entry.params is not params
    assert entry.json is payload
    assert entry.reason == "cleanup needed"


def test_snowflakes_are_monotonic_and_timestamped():
    backend = Backend()
    flakes = [backend.snowflake() for _ in range(100)]
    assert flakes == sorted(flakes) and len(set(flakes)) == 100
    import discord

    # Embedded timestamps decode to the fixed virtual epoch (2026+).
    assert discord.utils.snowflake_time(flakes[0]).year >= 2026
