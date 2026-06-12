"""Fake transports replacing discord.py's HTTP client and webhook adapter.

``HTTPClient.request`` is the chokepoint for nearly all REST calls;
interaction responses/followups travel through the async webhook adapter
instead. Both funnel into the same route table, and backend errors surface as
genuine ``discord.HTTPException`` subclasses with authentic Discord codes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.http import HTTPClient, Route

from ..backend import Backend, serializers
from ..backend.errors import BackendError
from . import router
from . import routes as _routes  # noqa: F401  — importing registers every route handler

_API_PREFIX = "/api/v"


class _FakeResponse:
    """Just enough of an aiohttp response for HTTPException construction."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = {400: "Bad Request", 403: "Forbidden", 404: "Not Found"}.get(status, "Error")
        self.headers: dict[str, str] = {}


def _raise_for(error: BackendError) -> None:
    response = _FakeResponse(error.status)
    data = error.to_json()
    if error.status == 403:
        raise discord.Forbidden(response, data)  # type: ignore[arg-type]
    if error.status == 404:
        raise discord.NotFound(response, data)  # type: ignore[arg-type]
    raise discord.HTTPException(response, data)  # type: ignore[arg-type]


def _path_of(url: str) -> str:
    # Strip "https://discord.com/api/v10" and the query string from a routed URL.
    path = url.split("?", 1)[0]
    idx = path.find(_API_PREFIX)
    if idx != -1:
        path = path[idx + len(_API_PREFIX) :]
        path = path[path.find("/") :]
    return path


class FakeHTTPClient(HTTPClient):
    """Drop-in ``HTTPClient`` whose requests hit the virtual backend."""

    def __init__(self, backend: Backend, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(loop)
        self.backend = backend
        # Interaction.__init__ reads the (name-mangled) session attribute.
        self._HTTPClient__session = None  # type: ignore[assignment]

    async def static_login(self, token: str) -> Any:
        self.token = token
        return dict(serializers.user_payload(self.backend.bot_user))

    async def close(self) -> None:
        pass

    async def ws_connect(self, url: str, *, compress: int = 0) -> Any:
        raise RuntimeError("discord-py-test never opens a real gateway connection")

    async def get_from_cdn(self, url: str) -> bytes:
        blob = self.backend.cdn.get(url)
        if blob is None:
            _raise_for(BackendError(404, 0, "asset not found"))
            raise AssertionError  # unreachable
        return blob

    async def request(
        self,
        route: Route,
        *,
        files: Any | None = None,
        form: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        json = kwargs.get("json")
        file_list = list(files or [])
        if form is not None:
            payload, file_list = router.parse_form(list(form), file_list)
            if payload is not None:
                json = payload
        try:
            return router.dispatch(
                self.backend,
                route.method,
                _path_of(route.url),
                json=json,
                params=kwargs.get("params"),
                files=file_list,
            )
        except router.RouteNotImplemented:
            # Surface unimplemented routes loudly: don't disguise them as an
            # HTTPException a broad `except discord.HTTPException` could swallow.
            raise
        except BackendError as exc:
            _raise_for(exc)


class FakeWebhookAdapter:
    """Replacement for ``discord.webhook.async_.AsyncWebhookAdapter``.

    The real adapter's named helpers (``create_interaction_response``,
    ``execute_webhook``, ...) are thin wrappers around its generic
    ``request``; delegating them against this fake's ``request`` keeps all of
    that real code running.
    """

    def __init__(self, backend: Backend) -> None:
        self.backend = backend

    def __getattr__(self, name: str) -> Any:
        from discord.webhook.async_ import AsyncWebhookAdapter

        real = getattr(AsyncWebhookAdapter, name)
        return real.__get__(self, FakeWebhookAdapter)

    async def request(
        self,
        route: Any,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        multipart: list[dict[str, Any]] | None = None,
        files: Any | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        json: Any = payload
        file_list = list(files or [])
        if multipart:
            parsed, file_list = router.parse_form(multipart, file_list)
            if parsed is not None:
                json = parsed
        try:
            return router.dispatch(
                self.backend,
                route.method,
                _path_of(route.url),
                json=json,
                params=params,
                files=file_list,
            )
        except router.RouteNotImplemented:
            raise
        except BackendError as exc:
            _raise_for(exc)
