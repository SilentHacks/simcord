import json
import socket
from importlib import resources

import discord
import jsonschema
import pytest
from aiohttp import ClientSession
from preview_helpers import control_key, preview_headers, target_message

import simcord


@pytest.mark.asyncio
async def test_preview_snapshot_matches_packaged_schema(env, channel, alice):
    schema = json.loads(resources.files("simcord.preview").joinpath("protocol.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    await alice.slash(channel, "panel")

    async with env.preview(channel, viewers=[alice]) as preview:
        snapshot = await preview.snapshot()

    validator.validate(snapshot)
    invalid = {**snapshot, "protocolVersion": 1}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


@pytest.mark.asyncio
async def test_preview_snapshot_returns_detached_projection(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        snap = await preview.snapshot()
        assert snap["protocolVersion"] == 2
        assert "selected" not in snap
        assert snap["viewerId"] == str(alice.id)
        assert target_message(snap)["content"] == "Panel"
        assert isinstance(snap["messageIndex"], list)
        assert isinstance(snap["messages"], dict)
        assert snap["targetId"] in snap["messages"]
        assert snap["timeline"] == [snap["targetId"]]
        for key in (
            "messages",
            "messageIndex",
            "timeline",
            "modal",
            "candidates",
            "assets",
            "diagnostics",
            "lastAction",
            "status",
        ):
            assert key in snap

        await alice.send(channel, "hello")
        snap = await preview.snapshot()
        assert "hello" in [item["excerpt"] for item in snap["messageIndex"]]
        assert target_message(snap)["content"] == "Panel"

        target_message(snap)["content"] = "mutated"
        assert target_message(preview._page_payload(preview._python))["content"] == "Panel"
        snap = await preview.snapshot()
        assert target_message(snap)["content"] == "Panel"


@pytest.mark.asyncio
async def test_preview_control_keys_are_scoped_per_message(env, channel, alice):
    first_view = discord.ui.View()
    first_view.add_item(discord.ui.Button(label="First", custom_id="same"))
    second_view = discord.ui.View()
    second_view.add_item(discord.ui.Button(label="Second", custom_id="same"))
    first = await env.bot.get_channel(channel.id).send(view=first_view)
    second = await env.bot.get_channel(channel.id).send(view=second_view)

    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(first)
        first_snapshot = preview._page_payload(preview._python)
        await preview.show(second)
        second_snapshot = preview._page_payload(preview._python)
        first_key = control_key(first_snapshot, "same")
        second_key = control_key(second_snapshot, "same")
        assert first_key != second_key
        assert first_key.startswith(f"message:{first.id}:")
        assert second_key.startswith(f"message:{second.id}:")


@pytest.mark.asyncio
async def test_preview_snapshot_requires_active_session(env, channel, alice):
    preview = env.preview(channel, viewers=[alice])
    with pytest.raises(simcord.SetupError, match="not active"):
        await preview.snapshot()
    async with preview:
        pass
    with pytest.raises(simcord.SetupError, match="not active"):
        await preview.snapshot()


@pytest.mark.asyncio
async def test_preview_port_pins_loopback_port(env, channel, alice):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    async with env.preview(channel, viewers=[alice], port=port) as preview:
        assert f"127.0.0.1:{port}" in preview.url


@pytest.mark.asyncio
async def test_preview_port_validation(env, channel, alice):
    for port in (-1, 65536, "8080", True):
        with pytest.raises(simcord.SetupError, match="port must be an integer"):
            env.preview(channel, viewers=[alice], port=port)


@pytest.mark.asyncio
async def test_preview_port_bind_failure(env, channel, alice):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    try:
        port = held.getsockname()[1]
        preview = env.preview(channel, viewers=[alice], port=port)
        with pytest.raises(simcord.SetupError, match="could not bind port"):
            async with preview:
                pass
        # A failed entry is dead but settled: the env is released and
        # wait_closed() returns instead of hanging.
        assert env._preview is None
        await preview.wait_closed()
        async with env.preview(channel, viewers=[alice]) as recovered:
            assert recovered.url
    finally:
        held.close()


@pytest.mark.asyncio
async def test_preview_port_80_elided_host_and_origin(env, channel, alice):
    from simcord.preview._server import PreviewServer

    async with env.preview(channel, viewers=[alice]) as preview:
        # Browsers strip the http scheme-default port from Host and Origin, so
        # a server bound to :80 must accept the port-less forms.
        server = PreviewServer(preview)
        server.port = 80
        headers = {**preview_headers(preview), "Host": "127.0.0.1", "Origin": "http://127.0.0.1"}
        assert server._authorized(_Request(headers)) is True
        headers = {**headers, "Host": "localhost", "Origin": "http://localhost"}
        assert server._authorized(_Request(headers)) is True
        assert server._authorized(_Request({**headers, "Host": "127.0.0.1:80"})) is True
        assert server._authorized(_Request({**headers, "Host": "evil.example"})) is False
        assert server._authorized(_Request({**headers, "Origin": "http://evil.example"})) is False


class _Request:
    """Minimal stand-in for an aiohttp request for ``_authorized``."""

    def __init__(self, headers):
        self.headers = headers


@pytest.mark.asyncio
async def test_preview_localhost_origin_allowed(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        port = preview._server.port
        headers = {
            **preview_headers(preview),
            "Host": f"localhost:{port}",
            "Origin": f"http://localhost:{port}",
        }
        response = await client.post(preview._origin + "/api/pages", headers=headers, json={})
        assert response.status == 200
        response = await client.post(
            preview._origin + "/api/pages",
            headers={**headers, "Origin": "http://evil.example"},
            json={},
        )
        assert response.status == 401
