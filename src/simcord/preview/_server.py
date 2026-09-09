"""Small loopback bridge for Preview; aiohttp is imported only when entered."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from . import Preview


class PreviewServer:
    _STATIC_FILES: ClassVar[dict[str, str]] = {
        "/": "index.html",
        "/app.js": "app.js",
        "/components.js": "components.js",
        "/preview.css": "preview.css",
    }
    _SECURITY_HEADERS: ClassVar[dict[str, str]] = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        ),
    }

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
        for route in ("/app.js", "/components.js", "/preview.css"):
            app.router.add_get(route, self._static)
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

        if self.port is None or request.headers.get("Host", "") not in {
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
        }:
            raise web.HTTPForbidden()
        return await self._static(request)

    async def _static(self, request: Any) -> Any:
        from aiohttp import web

        filename = self._STATIC_FILES.get(request.path)
        if filename is None:
            raise web.HTTPNotFound()
        path = Path(__file__).with_name("static") / filename
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise web.HTTPNotFound() from None
        content_type = {
            "index.html": "text/html",
            "app.js": "application/javascript",
            "components.js": "application/javascript",
            "preview.css": "text/css",
        }[filename]
        return web.Response(text=text, content_type=content_type, headers=self._SECURITY_HEADERS)

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
        return web.json_response(self.preview.page_payload(page), headers=self._SECURITY_HEADERS)

    async def _delete_page(self, request: Any) -> Any:
        from aiohttp import web

        context_id = request.match_info["context_id"]
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        self.preview.close_page(context_id)
        return web.json_response({"closed": True}, headers=self._SECURITY_HEADERS)

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
        return web.json_response(payload, headers=self._SECURITY_HEADERS)

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
        return web.json_response(result, headers=self._SECURITY_HEADERS)

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
                **self._SECURITY_HEADERS,
                "Content-Disposition": f'inline; filename="{safe_filename}"',
            },
        )
