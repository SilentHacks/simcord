import socket

import pytest
from aiohttp import ClientSession
from preview_helpers import preview_headers

import simcord


@pytest.mark.asyncio
async def test_preview_snapshot_returns_detached_projection(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        snap = await preview.snapshot()
        assert snap["protocolVersion"] == 1
        assert snap["viewerId"] == str(alice.id)
        assert snap["selected"]["content"] == "Panel"
        for key in ("messages", "modal", "candidates", "assets", "diagnostics", "lastAction", "status"):
            assert key in snap

        await alice.send(channel, "hello")
        snap = await preview.snapshot()
        assert "hello" in [item["excerpt"] for item in snap["messages"]]
        assert snap["selected"]["content"] == "Panel"

        snap["selected"]["content"] = "mutated"
        assert preview._page_payload(preview._python)["selected"]["content"] == "Panel"
        snap = await preview.snapshot()
        assert snap["selected"]["content"] == "Panel"


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
