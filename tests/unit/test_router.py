import pytest

from simcord import HttpLogEntry
from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.http import router
from simcord.http import routes as _routes  # noqa: F401  — registers handlers


def test_unknown_route_raises_with_route_name_and_records_attempt():
    backend = Backend()
    params = {"nested": {"limit": 3}}
    with pytest.raises(router.RouteNotImplemented, match="GET /made/up/route"):
        router.dispatch(backend, "GET", "/made/up/route", params=params)
    entry = backend.http_requests[-1]
    assert isinstance(entry, HttpLogEntry)
    assert entry.params == params and entry.params is not params


def test_http_request_records_defaults_and_reason():
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

    payload = {"name": "g"}
    router.dispatch(backend, "GET", path, json=payload, reason="cleanup needed")
    entry = backend.http_requests[-1]
    assert entry.json == payload and entry.json is not payload
    assert entry.reason == "cleanup needed"


def test_http_request_preserves_top_level_json_arrays():
    backend = Backend()
    guild = backend.create_guild("g")
    payload = [{"name": "g"}, "raw", None]
    router.dispatch(backend, "GET", f"/guilds/{guild.id}", json=payload)
    entry = backend.http_requests[-1]
    assert entry.json == payload
    assert entry.json is not payload


def test_http_request_records_detached_nested_values_before_fault():
    backend = Backend()
    guild = backend.create_guild("g")
    path = f"/guilds/{guild.id}"
    params = {"filters": {"limit": 3}}
    payload = {"nested": {"items": [1, 2]}}
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
        router.dispatch(backend, "GET", path, params=params, json=payload)

    entry = backend.http_requests[-1]
    params["filters"]["limit"] = 99
    payload["nested"]["items"].append(3)
    assert entry.params == {"filters": {"limit": 3}}
    assert entry.json == {"nested": {"items": [1, 2]}}
    assert entry.params["filters"] is not params["filters"]
    assert entry.json["nested"] is not payload["nested"]


def test_literal_segments_beat_parameters():
    backend = Backend()
    guild = backend.create_guild("g")
    channel = backend.create_channel(guild.id, "general")
    message = backend.create_message(channel.id, backend.bot_user.id, "hi")
    backend.set_pinned(channel.id, message.id, True)
    # ".../messages/pins" must not be captured by ".../messages/{message_id}".
    result = router.dispatch(backend, "GET", f"/channels/{channel.id}/messages/pins")
    assert [int(i["message"]["id"]) for i in result["items"]] == [message.id]


def test_snowflakes_are_monotonic_and_timestamped():
    backend = Backend()
    flakes = [backend.snowflake() for _ in range(100)]
    assert flakes == sorted(flakes) and len(set(flakes)) == 100
    import discord

    # Embedded timestamps decode to the fixed virtual epoch (2026+).
    assert discord.utils.snowflake_time(flakes[0]).year >= 2026
