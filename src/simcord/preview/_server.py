"""Small loopback bridge for Preview; aiohttp is imported only when entered."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import Preview


class PreviewServer:
    def __init__(self, preview: Preview) -> None:
        self.preview = preview
        self.runner: Any = None
        self.site: Any = None
        self.port: int | None = None

    async def start(self) -> None:
        try:
            from aiohttp import web
        except ImportError as exc:  # pragma: no cover - dependency is an optional runtime extra
            raise RuntimeError("Preview requires aiohttp; install simcord's preview extra") from exc
        app = web.Application(handler_args={"handler_cancellation": False})
        app.router.add_get("/", self._index)
        app.router.add_post("/api/pages", self._pages)
        app.router.add_delete("/api/pages/{context_id}", self._delete_page)
        app.router.add_get("/api/state", self._state)
        app.router.add_post("/api/action", self._action)
        app.router.add_get("/api/assets/{asset_id}", self._asset)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        sockets = getattr(self.site, "_server", None)
        if sockets is None or not sockets.sockets:
            raise RuntimeError("Preview server did not bind a socket")
        self.port = int(sockets.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            self.port = None

    def _authorized(self, request: Any, *, context: str | None = None) -> bool:
        capability = request.headers.get("X-Simcord-Capability")
        supplied_context = request.headers.get("X-Simcord-Context")
        if capability != self.preview.capability:
            return False
        if context is not None and supplied_context != context:
            return False
        host = request.headers.get("Host", "")
        if self.port is not None and host not in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}:
            return False
        origin = request.headers.get("Origin")
        if origin is not None and origin not in {self.preview.origin, "null"}:
            return False
        return True

    async def _index(self, request: Any) -> Any:
        from aiohttp import web

        if request.headers.get("Host", "").split(":", 1)[0] not in {"127.0.0.1", "localhost"}:
            raise web.HTTPForbidden()
        return web.Response(
            text="<!doctype html><meta charset=utf-8><title>SimCord Preview</title>",
            content_type="text/html",
            headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'"},
        )

    async def _pages(self, request: Any) -> Any:
        from aiohttp import web

        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise web.HTTPBadRequest(text="invalid JSON") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        try:
            page = self.preview.open_page(body.get("viewer_id"), body.get("target_id"))
        except Exception as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        return web.json_response(self.preview.page_payload(page), headers={"Cache-Control": "no-store"})

    async def _delete_page(self, request: Any) -> Any:
        from aiohttp import web

        context_id = request.match_info["context_id"]
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        self.preview.close_page(context_id)
        return web.json_response({"closed": True}, headers={"Cache-Control": "no-store"})

    async def _state(self, request: Any) -> Any:
        from aiohttp import web

        context_id = request.headers.get("X-Simcord-Context")
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        try:
            page = self.preview.get_page(context_id)
            payload = self.preview.page_payload(page)
        except Exception as exc:
            raise web.HTTPGone(text=str(exc)) from exc
        return web.json_response(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def _action(self, request: Any) -> Any:
        from aiohttp import web

        context_id = request.headers.get("X-Simcord-Context")
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise web.HTTPBadRequest(text="invalid JSON") from None
        try:
            result = await self.preview.action(context_id, body)
        except Exception as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    async def _asset(self, request: Any) -> Any:
        from aiohttp import web

        context_id = request.headers.get("X-Simcord-Context")
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        try:
            content_type, body, filename = self.preview.asset(context_id, request.match_info["asset_id"])
        except Exception as exc:
            raise web.HTTPNotFound(text=str(exc)) from exc
        safe_filename = filename.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_")
        return web.Response(
            body=body,
            content_type=content_type or "application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="{safe_filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
