"""Every touch of a private discord.py API, quarantined in one module.

If a discord.py release changes one of these internals, the import-time
self-check below fails with a clear message instead of users' tests breaking
mysteriously. Keep this inventory in sync with what the framework touches.
"""

from __future__ import annotations

from typing import Any

import discord
from discord.gateway import DiscordWebSocket
from discord.http import HTTPClient
from discord.state import ConnectionState
from discord.ui.view import View
from discord.webhook.async_ import async_context

#: discord.py coroutine qualnames that are long-lived background machinery
#: (not work to settle on). Their leaf names are matched against running tasks
#: in :meth:`Env.settle`; keep them here so a discord.py rename is caught by
#: ``verify()`` rather than by users' tests hanging mysteriously.
BACKGROUND_CORO_NAMES = ("__timeout_task_impl",)


def verify() -> None:
    """Sanity-check the discord.py internals this framework relies on."""
    if discord.version_info.major != 2 or discord.version_info.minor < 7:
        raise ImportError(
            f"discord-py-test requires discord.py 2.7+; found {discord.__version__}. "
            "Check https://github.com/SilentHacks/discord-py-test for supported versions."
        )
    problems = []
    for cls, attr in (
        (HTTPClient, "request"),
        (HTTPClient, "static_login"),
        (HTTPClient, "get_from_cdn"),
        (ConnectionState, "parse_ready"),
        (ConnectionState, "parse_message_create"),
        (ConnectionState, "parse_interaction_create"),
        # Intent simulation and member chunking rely on these:
        (ConnectionState, "intents"),
        (ConnectionState, "parse_guild_members_chunk"),
        (DiscordWebSocket, "request_chunks"),
        (discord.Client, "_get_websocket"),
    ):
        if not hasattr(cls, attr):
            problems.append(f"{cls.__name__}.{attr}")
    # The background-coro names are matched by leaf qualname; confirm they still
    # exist on View so a rename surfaces here instead of in settle().
    for name in BACKGROUND_CORO_NAMES:
        if not any(attr.endswith(name) for attr in dir(View)):
            problems.append(f"View.*{name}")
    if problems:
        raise ImportError(
            "This discord.py version changed internals discord-py-test depends on: "
            + ", ".join(problems)
            + ". Please report this at https://github.com/SilentHacks/discord-py-test/issues."
        )


def get_state(client: discord.Client) -> Any:
    """The client's ConnectionState (cache + gateway event parsers)."""
    return client._connection


def parsers(client: discord.Client) -> dict[str, Any]:
    return get_state(client).parsers


def install_http(client: discord.Client, http: HTTPClient) -> None:
    """Point every captured HTTP reference at the fake transport."""
    client.http = http  # type: ignore[misc]
    get_state(client).http = http
    tree = getattr(client, "tree", None)
    if tree is not None:
        # CommandTree captures its own HTTP reference at construction time.
        tree._http = http


def install_websocket(client: discord.Client, ws: Any) -> None:
    """Install the fake upstream gateway as ``client.ws``.

    ``Client._get_websocket`` (which ConnectionState uses for chunk requests)
    returns ``self.ws``, so this one assignment routes REQUEST_GUILD_MEMBERS
    — startup chunking, ``Guild.chunk()``, ``Guild.query_members()`` — to the
    fake, instead of crashing on the ``None`` ws of a never-connected client.
    """
    client.ws = ws


def set_guild_ready_timeout(client: discord.Client, timeout: float) -> None:
    """No guilds arrive before our READY; don't wait for stragglers."""
    get_state(client).guild_ready_timeout = timeout


def set_webhook_adapter(adapter: Any) -> Any:
    """Interaction responses go through this context-local adapter, not HTTPClient."""
    return async_context.set(adapter)


def reset_webhook_adapter(token: Any) -> None:
    async_context.reset(token)
