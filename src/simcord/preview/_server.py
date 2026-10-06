"""Small loopback bridge for Preview; aiohttp is imported only when entered."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from ..backend.errors import BackendError, SetupError
from ._diagnostics import make_diagnostic

if TYPE_CHECKING:
    from . import Preview


async def _read_json_body(request: Any) -> Any:
    """Read a JSON body under the shared 256 KiB cap, rejecting oversize early."""
    from aiohttp import web

    raw = bytearray()
    while len(raw) <= 256 * 1024 and not request.content.at_eof():
        chunk = await request.content.read(min(64 * 1024, 256 * 1024 + 1 - len(raw)))
        if not chunk:  # pragma: no cover - at_eof guards exhausted streams
            break
        raw.extend(chunk)
    if len(raw) > 256 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=len(raw))
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="invalid JSON") from exc


class PreviewServer:
    _STATIC_FILES: ClassVar[dict[str, str]] = {
        "/": "index.html",
        "/app.js": "app.js",
        "/workbench.js": "workbench.js",
        "/select-drafts.js": "select-drafts.js",
        "/timeline.js": "timeline.js",
        "/transport.js": "transport.js",
        "/components.js": "components.js",
        "/selects.js": "selects.js",
        "/listbox.js": "listbox.js",
        "/composer.js": "composer.js",
        "/commands.js": "commands.js",
        "/messages.js": "messages.js",
        "/media.js": "media.js",
        "/text.js": "text.js",
        "/vendor/lottie/LICENSE": "vendor/lottie/LICENSE",
        "/vendor/lottie/SHA256SUMS": "vendor/lottie/SHA256SUMS",
        "/vendor/lottie/5.12.2/lottie_light_canvas.min.js": "vendor/lottie/5.12.2/lottie_light_canvas.min.js",
        "/vendor/highlight/LICENSE": "vendor/highlight/LICENSE",
        "/vendor/highlight/SHA256SUMS": "vendor/highlight/SHA256SUMS",
        "/vendor/highlight/es/core.min.js": "vendor/highlight/es/core.min.js",
        "/vendor/highlight/es/languages/bash.min.js": "vendor/highlight/es/languages/bash.min.js",
        "/vendor/highlight/es/languages/cpp.min.js": "vendor/highlight/es/languages/cpp.min.js",
        "/vendor/highlight/es/languages/csharp.min.js": "vendor/highlight/es/languages/csharp.min.js",
        "/vendor/highlight/es/languages/css.min.js": "vendor/highlight/es/languages/css.min.js",
        "/vendor/highlight/es/languages/diff.min.js": "vendor/highlight/es/languages/diff.min.js",
        "/vendor/highlight/es/languages/dockerfile.min.js": "vendor/highlight/es/languages/dockerfile.min.js",
        "/vendor/highlight/es/languages/go.min.js": "vendor/highlight/es/languages/go.min.js",
        "/vendor/highlight/es/languages/ini.min.js": "vendor/highlight/es/languages/ini.min.js",
        "/vendor/highlight/es/languages/java.min.js": "vendor/highlight/es/languages/java.min.js",
        "/vendor/highlight/es/languages/javascript.min.js": "vendor/highlight/es/languages/javascript.min.js",
        "/vendor/highlight/es/languages/json.min.js": "vendor/highlight/es/languages/json.min.js",
        "/vendor/highlight/es/languages/markdown.min.js": "vendor/highlight/es/languages/markdown.min.js",
        "/vendor/highlight/es/languages/python.min.js": "vendor/highlight/es/languages/python.min.js",
        "/vendor/highlight/es/languages/rust.min.js": "vendor/highlight/es/languages/rust.min.js",
        "/vendor/highlight/es/languages/sql.min.js": "vendor/highlight/es/languages/sql.min.js",
        "/vendor/highlight/es/languages/typescript.min.js": "vendor/highlight/es/languages/typescript.min.js",
        "/vendor/highlight/es/languages/xml.min.js": "vendor/highlight/es/languages/xml.min.js",
        "/vendor/highlight/es/languages/yaml.min.js": "vendor/highlight/es/languages/yaml.min.js",
        "/dom.js": "dom.js",
        "/preview.css": "preview.css",
        "/protocol.schema.json": "protocol.schema.json",
        "/fonts/noto-sans-latin-v2.015.ttf": "fonts/noto-sans-latin-v2.015.ttf",
        "/fonts/noto-sans-latin-italic-v2.015.ttf": "fonts/noto-sans-latin-italic-v2.015.ttf",
        "/fonts/noto-sans-mono-v2.014.ttf": "fonts/noto-sans-mono-v2.014.ttf",
        "/fonts/noto-sans-arabic-v2.012.ttf": "fonts/noto-sans-arabic-v2.012.ttf",
        "/fonts/noto-sans-hebrew-v3.001.ttf": "fonts/noto-sans-hebrew-v3.001.ttf",
        "/fonts/noto-sans-devanagari-v2.007.ttf": "fonts/noto-sans-devanagari-v2.007.ttf",
        "/fonts/noto-sans-sc-v2.004.ttf": "fonts/noto-sans-sc-v2.004.ttf",
        "/fonts/noto-color-emoji-v2.051.ttf": "fonts/noto-color-emoji-v2.051.ttf",
    }
    _FONT_FILES = frozenset(value for value in _STATIC_FILES.values() if value.startswith("fonts/"))
    _STATIC_CONTENT_TYPES: ClassVar[dict[str, str]] = {
        "index.html": "text/html",
        "preview.css": "text/css",
        "protocol.schema.json": "application/schema+json",
        "app.js": "application/javascript",
        "workbench.js": "application/javascript",
        "select-drafts.js": "application/javascript",
        "timeline.js": "application/javascript",
        "transport.js": "application/javascript",
        "components.js": "application/javascript",
        "media.js": "application/javascript",
        "messages.js": "application/javascript",
        "dom.js": "application/javascript",
        "text.js": "application/javascript",
        "vendor/highlight/LICENSE": "text/plain",
        "vendor/highlight/SHA256SUMS": "text/plain",
        "vendor/lottie/LICENSE": "text/plain",
        "vendor/lottie/SHA256SUMS": "text/plain",
    }
    _SECURITY_HEADERS: ClassVar[dict[str, str]] = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "font-src 'self'; connect-src 'self' blob:; img-src 'self' blob:; media-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        ),
    }

    def __init__(self, preview: Preview, port: int = 0) -> None:
        self.preview = preview
        self._requested_port = port
        self.runner: Any = None
        self.port: int | None = None

    async def start(self) -> None:
        global web
        try:
            from aiohttp import web
        except ImportError as exc:  # pragma: no cover - dependency is an optional runtime extra
            raise SetupError("Preview requires aiohttp; install simcord[preview]") from exc
        app = web.Application(handler_args={"handler_cancellation": False})
        app.router.add_get("/", self._index)
        for route in (route for route in self._STATIC_FILES if route != "/"):
            app.router.add_get(route, self._static)
        app.router.add_post("/api/pages", self._pages)
        app.router.add_delete("/api/pages/{context_id}", self._delete_page)
        app.router.add_get("/api/state", self._state)
        app.router.add_get("/api/commands", self._commands)
        app.router.add_post("/api/action", self._action)
        app.router.add_get("/api/assets/{asset_id}", self._asset)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", self._requested_port)
        try:
            await self.site.start()
        except OSError as exc:
            raise SetupError(f"preview could not bind port {self._requested_port}") from exc
        sockets = getattr(self.site, "_server", None)
        if sockets is None or not sockets.sockets:  # pragma: no cover - aiohttp binding invariant
            raise RuntimeError("Preview server did not bind a socket")
        self.port = int(sockets.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            self.port = None

    def _allowed_hosts(self) -> set[str]:
        if self.port is None:
            return set()
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.port == 80:
            # Browsers elide the http scheme-default port from Host.
            hosts.update({"127.0.0.1", "localhost"})
        return hosts

    def _authorized(self, request: Any, *, context: str | None = None) -> bool:
        capability = request.headers.get("X-Simcord-Capability")
        supplied_context = request.headers.get("X-Simcord-Context")
        if capability != self.preview.capability:
            return False
        if context is not None and supplied_context != context:
            return False
        if self.port is not None and request.headers.get("Host", "") not in self._allowed_hosts():
            return False
        origin = request.headers.get("Origin")
        allowed = {self.preview._origin, "null"}
        if self.port is not None:
            allowed.add(f"http://localhost:{self.port}")
            if self.port == 80:
                # The Origin header elides the scheme-default port too.
                allowed.update({"http://127.0.0.1", "http://localhost"})
        if origin is not None and origin not in allowed:
            return False
        return True

    async def _index(self, request: Any) -> Any:
        if self.port is None or request.headers.get("Host", "") not in self._allowed_hosts():
            raise web.HTTPForbidden()
        return await self._static(request)

    async def _static(self, request: Any) -> Any:
        if self.port is None or request.headers.get("Host", "") not in self._allowed_hosts():
            raise web.HTTPForbidden()
        filename = self._STATIC_FILES.get(request.path)
        if filename is None:  # pragma: no cover - only registered static routes call this
            raise web.HTTPNotFound()
        path = (
            Path(__file__).with_name(filename)
            if filename == "protocol.schema.json"
            else Path(__file__).with_name("static") / filename
        )
        content_type = (
            "font/ttf"
            if filename in self._FONT_FILES
            else self._STATIC_CONTENT_TYPES.get(
                filename, "application/javascript" if filename.endswith(".js") else None
            )
        )
        if content_type is None:  # pragma: no cover - static map is class-owned
            raise web.HTTPNotFound()
        try:
            if filename in self._FONT_FILES:
                body = path.read_bytes()
                return web.Response(body=body, content_type=content_type, headers=self._SECURITY_HEADERS)
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise web.HTTPNotFound() from None
        return web.Response(text=text, content_type=content_type, headers=self._SECURITY_HEADERS)

    async def _pages(self, request: Any) -> Any:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        body = await _read_json_body(request)
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        try:
            page = self.preview._open_page(body.get("viewer_id"), body.get("target_id"))
        except (SetupError, BackendError):
            return web.json_response(
                {"error": make_diagnostic("control-unavailable")}, status=400, headers=self._SECURITY_HEADERS
            )
        return web.json_response(self.preview._page_payload(page), headers=self._SECURITY_HEADERS)

    async def _delete_page(self, request: Any) -> Any:
        context_id = request.match_info["context_id"]
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        try:
            self.preview._close_page(context_id)
        except (SetupError, BackendError):
            return web.json_response(
                {"error": make_diagnostic("context-unavailable")}, status=400, headers=self._SECURITY_HEADERS
            )
        return web.json_response({"closed": True}, headers=self._SECURITY_HEADERS)

    async def _state(self, request: Any) -> Any:
        context_id = request.headers.get("X-Simcord-Context")
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        try:
            page = self.preview._get_page(context_id)
            payload = self.preview._page_payload(page)
        except (SetupError, BackendError):
            return web.json_response(
                {"error": make_diagnostic("context-unavailable")}, status=410, headers=self._SECURITY_HEADERS
            )
        return web.json_response(payload, headers=self._SECURITY_HEADERS)

    async def _commands(self, request: Any) -> Any:
        from ..backend.access import can_access_channel
        from ._commands import unavailable_catalog

        context_id = request.headers.get("X-Simcord-Context")
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        try:
            page = self.preview._get_page(context_id)
        except (SetupError, BackendError):
            return web.json_response(
                {"error": make_diagnostic("context-unavailable")}, status=410, headers=self._SECURITY_HEADERS
            )
        if page.snapshot.get("commands", {}).get("state") == "unavailable" or not can_access_channel(
            self.preview.env, page.channel_id, page.viewer, history=True
        ):
            catalog = unavailable_catalog()
        else:
            catalog = page.command_catalog
        return web.json_response(
            catalog,
            headers=self._SECURITY_HEADERS,
            dumps=lambda value: json.dumps(value, separators=(",", ":")),
        )

    async def _action(self, request: Any) -> Any:
        context_id = request.headers.get("X-Simcord-Context")
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        if request.content_type.startswith("multipart/"):
            body = await self._multipart_action(request)
        else:
            body = await _read_json_body(request)
        # Malformed envelopes are not transport errors: _action() answers every
        # parseable body with a structured result (rejections carry HTTP 200).
        try:
            result = await self.preview._action(context_id, body)
        except (SetupError, BackendError):
            return web.json_response(
                {"error": make_diagnostic("context-unavailable")}, status=400, headers=self._SECURITY_HEADERS
            )
        return web.json_response(result, headers=self._SECURITY_HEADERS)

    async def _multipart_action(self, request: Any) -> dict[str, Any]:
        reader = await request.multipart()
        payload: dict[str, Any] | None = None
        files: dict[str, list[tuple[str, bytes]]] = {}
        parts = 0
        total = 0
        aggregate = 0
        async for part in reader:
            parts += 1
            if parts > 11:
                raise web.HTTPRequestEntityTooLarge(max_size=11, actual_size=parts)
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = await part.read_chunk(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 26 * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(max_size=26 * 1024 * 1024, actual_size=total)
                size += len(chunk)
                if part.name == "payload" and size > 256 * 1024:
                    raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=size)
                if part.name != "payload" and size > 10 * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(max_size=10 * 1024 * 1024, actual_size=size)
                chunks.append(chunk)
            blob = b"".join(chunks)
            if part.name == "payload":
                try:
                    payload = json.loads(blob)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise web.HTTPBadRequest(text="invalid multipart JSON envelope") from exc
            elif isinstance(part.name, str) and part.name.startswith("file:"):
                custom_id = part.name.removeprefix("file:")
                files.setdefault(custom_id, []).append((part.filename or "upload", blob))
                aggregate += size
                if aggregate > 25 * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(max_size=25 * 1024 * 1024, actual_size=aggregate)
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="multipart payload is required")
        if payload.get("kind") == "run_command":
            options = payload.get("options")
            if not isinstance(options, dict):
                raise web.HTTPBadRequest(text="multipart command options are required")
            for index, (option_name, uploads) in enumerate(files.items()):
                if len(uploads) != 1 or not option_name:
                    raise web.HTTPBadRequest(text="each command attachment option needs exactly one file")
                reference = options.get(option_name)
                if not isinstance(reference, dict) or reference != {"upload": index}:
                    raise web.HTTPBadRequest(text="command upload references must match multipart file order")
                options[option_name] = uploads[0]
            return payload
        values = payload.get("values")
        if not isinstance(values, dict):
            raise web.HTTPBadRequest(text="multipart values are required")
        for custom_id, uploads in files.items():
            values[custom_id] = uploads
        return payload

    async def _asset(self, request: Any) -> Any:
        context_id = request.headers.get("X-Simcord-Context")
        if not self._authorized(request, context=context_id):
            raise web.HTTPUnauthorized()
        download = request.query.get("download") in {"1", "true"}
        capture = request.query.get("capture") in {"1", "true"}
        poster = request.query.get("poster") in {"1", "true"}
        media_time: float | None = None
        if "media_time" in request.query:
            try:
                media_time = float(request.query["media_time"])
            except (TypeError, ValueError) as exc:
                raise web.HTTPBadRequest(text="media_time must be a finite non-negative number") from exc
            if not math.isfinite(media_time) or media_time < 0:
                raise web.HTTPBadRequest(text="media_time must be a finite non-negative number")
            capture = True
        try:
            content_type, body, filename = await self.preview._prepare_asset(
                context_id,
                request.match_info["asset_id"],
                download=download,
                capture=capture,
                poster=poster,
                media_time=media_time,
            )
        except (SetupError, BackendError):
            return web.json_response(
                {"error": make_diagnostic("asset-unavailable")}, status=404, headers=self._SECURITY_HEADERS
            )
        safe_filename = filename.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_")
        record = self.preview._get_page(context_id).assets.get(request.match_info["asset_id"])
        active_document = bool(record is not None and self.preview._active_document(record, body))
        disposition = "attachment" if download or active_document else "inline"
        if active_document:
            content_type = "application/octet-stream"
        headers = {
            **self._SECURITY_HEADERS,
            "Content-Disposition": f'{disposition}; filename="{safe_filename}"',
        }
        if record is not None and record.validated:
            if record.width and record.height:
                headers["X-Display-Width"] = str(record.width)
                headers["X-Display-Height"] = str(record.height)
            if record.duration is not None:
                headers["X-Media-Duration"] = str(record.duration)
            if record.effectiveMediaTime is not None:
                headers["X-Media-Time"] = str(record.effectiveMediaTime)
            if record.mediaKind is not None:
                headers["X-Media-Kind"] = record.mediaKind
            headers["X-Simcord-Media-Metadata"] = json.dumps(
                {
                    "mediaKind": record.mediaKind,
                    "duration": record.duration,
                    "frames": record.frames,
                    "sourceCodecs": record.sourceCodecs or {},
                    "displayCodecs": record.displayCodecs or {},
                    "transformation": record.transformation,
                    "qualityDifferences": record.qualityDifferences,
                    "effectiveMediaTime": record.effectiveMediaTime,
                    "waveform": record.waveform,
                    "workerMemoryLimited": record.workerMemoryLimited,
                },
                separators=(",", ":"),
            )
        return web.Response(
            body=body,
            content_type=content_type or "application/octet-stream",
            headers=headers,
        )
