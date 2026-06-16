"""Fake-transport edges: lifecycle no-ops, the no-real-gateway guard, CDN misses,
and webhook multipart handling — the seams the feature tests never touch.
"""

import io

import discord
import pytest

import simcord


async def test_fake_http_lifecycle_and_guards(env):
    client = env.bot.http

    # close() is a no-op; ws_connect refuses (simcord never opens a real socket).
    await client.close()
    with pytest.raises(RuntimeError):
        await client.ws_connect("wss://gateway.discord.gg")

    # A CDN asset that was never stored surfaces as NotFound, not a crash.
    with pytest.raises(discord.NotFound):
        await client.get_from_cdn("https://cdn.discordapp.com/never-stored.png")


async def test_execute_unknown_webhook_token_fails_loudly(env):
    from simcord.http import router

    with pytest.raises(simcord.BackendError):
        router.dispatch(
            env.backend,
            "POST",
            "/webhooks/123456789/not-a-real-token",
            json={"content": "hi"},
        )


async def test_webhook_send_with_file_multipart(env, channel):
    cached = env.bot.get_channel(channel.id)
    webhook = await cached.create_webhook(name="Filer")

    # A webhook send carrying a file rides as multipart through the adapter.
    file = discord.File(io.BytesIO(b"payload"), filename="note.txt")
    await webhook.send("here", file=file, wait=True)
    await env.settle()

    last = channel.last_message
    assert last.attachments[0].filename == "note.txt"
