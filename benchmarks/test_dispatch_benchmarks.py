"""Reported benchmarks for the in-memory hot path (pytest-benchmark).

These measure and report; they do not gate. The gate lives in
``test_perf_guards.py``. Run with ``pytest benchmarks --benchmark-json=...`` to
capture the numbers; CI uploads that JSON as a trend artifact.

Each synchronous dispatch round starts from a newly seeded backend; setup is
excluded from timing. This includes fresh HTTP/transcript logs for reads and
edits as well as writes. There is one dispatch per round, without calibration.
"""

from __future__ import annotations

import asyncio

import discord
from _helpers import backend_with_channel
from discord.ext import commands
from pytest_benchmark.fixture import BenchmarkFixture

import simcord
from simcord.http.router import dispatch


def test_send_message(benchmark: BenchmarkFixture) -> None:
    """POST once into an initially 50-message channel."""

    def setup():
        backend, _gid, channel_id = backend_with_channel(50)
        return (backend, "POST", f"/channels/{channel_id}/messages"), {"json": {"content": "hello"}}

    result = benchmark.pedantic(dispatch, setup=setup, rounds=100, iterations=1)
    assert result["content"] == "hello"
    assert int(result["id"]) > 0


def test_edit_message(benchmark: BenchmarkFixture) -> None:
    """PATCH once in a fresh channel with 50 seed messages and one edit target."""

    def setup():
        backend, _gid, channel_id = backend_with_channel(50)
        message = backend.create_message(channel_id, backend.bot_user.id, "original")
        path = f"/channels/{channel_id}/messages/{message.id}"
        return (backend, "PATCH", path), {"json": {"content": "edited"}}

    result = benchmark.pedantic(dispatch, setup=setup, rounds=100, iterations=1)
    assert result["content"] == "edited"
    assert int(result["id"]) > 0


def test_get_history(benchmark: BenchmarkFixture) -> None:
    """GET the newest 50 messages once from an initially 200-message channel."""

    def setup():
        backend, _gid, channel_id = backend_with_channel(200)
        return (backend, "GET", f"/channels/{channel_id}/messages"), {"params": {"limit": 50}}

    result = benchmark.pedantic(dispatch, setup=setup, rounds=100, iterations=1)
    assert [message["content"] for message in result] == [f"message {index}" for index in range(199, 149, -1)]


def test_env_setup(benchmark: BenchmarkFixture) -> None:
    """Full ``async with simcord.run(bot)`` — the fixed per-test cost a user pays."""

    def setup_teardown() -> None:
        async def go() -> None:
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
            async with simcord.run(bot):
                pass

        asyncio.run(go())

    benchmark.pedantic(setup_teardown, rounds=10, iterations=1)
