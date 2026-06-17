"""Reported benchmarks for the in-memory hot path (pytest-benchmark).

These measure and report; they do not gate. The gate lives in
``test_perf_guards.py``. Run with ``pytest benchmarks --benchmark-json=...`` to
capture the numbers; CI uploads that JSON as a trend artifact.
"""

from __future__ import annotations

import asyncio

import discord
from _helpers import backend_with_channel
from discord.ext import commands

import simcord
from simcord.http.router import dispatch


def test_send_message(benchmark: object) -> None:
    """POST a message: the write hot path (route match -> backend -> serialize)."""
    backend, _gid, channel_id = backend_with_channel(50)
    path = f"/channels/{channel_id}/messages"
    benchmark(lambda: dispatch(backend, "POST", path, json={"content": "hello"}))  # type: ignore[operator]


def test_edit_message(benchmark: object) -> None:
    """PATCH an existing message: the honesty-vetted edit path."""
    backend, _gid, channel_id = backend_with_channel(50)
    message = backend.create_message(channel_id, backend.bot_user.id, "original")
    path = f"/channels/{channel_id}/messages/{message.id}"
    benchmark(lambda: dispatch(backend, "PATCH", path, json={"content": "edited"}))  # type: ignore[operator]


def test_get_history(benchmark: object) -> None:
    """GET channel history: the read path over a realistically-sized channel."""
    backend, _gid, channel_id = backend_with_channel(200)
    path = f"/channels/{channel_id}/messages"
    benchmark(lambda: dispatch(backend, "GET", path, params={"limit": 50}))  # type: ignore[operator]


def test_env_setup(benchmark: object) -> None:
    """Full ``async with simcord.run(bot)`` — the fixed per-test cost a user pays."""

    def setup_teardown() -> None:
        async def go() -> None:
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
            async with simcord.run(bot):
                pass

        asyncio.run(go())

    benchmark.pedantic(setup_teardown, rounds=10, iterations=1)  # type: ignore[attr-defined]
