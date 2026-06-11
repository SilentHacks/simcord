"""Route table mapping (method, path template) -> backend handlers.

Unknown routes always fail loudly with :class:`RouteNotImplemented` — a
testing tool must never silently fake success. The router also services
test-injected faults (``env.inject_error``) and records every call to
``backend.http_log`` for advanced assertions.
"""

from __future__ import annotations

import fnmatch
import json as _json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..backend import Backend
from ..backend.errors import BackendError


class RouteNotImplemented(BackendError):
    def __init__(self, method: str, path: str) -> None:
        super().__init__(
            501,
            0,
            f"discord-py-test does not implement '{method} {path}' yet. "
            "See the parity matrix in the docs; please open an issue if your bot needs this route.",
        )
        self.method = method
        self.path = path


@dataclass
class RequestContext:
    backend: Backend
    args: dict[str, str]
    json: Optional[Any] = None
    params: dict[str, Any] = field(default_factory=dict)
    files: list[Any] = field(default_factory=list)

    def int_arg(self, name: str) -> int:
        return int(self.args[name])

    def body(self) -> dict[str, Any]:
        return self.json if isinstance(self.json, dict) else {}

    def store_files(self, channel_id: int) -> list[dict[str, Any]]:
        attachments = []
        for f in self.files:
            data = f.fp.read()
            attachments.append(
                self.backend.cdn.store_attachment(
                    self.backend.snowflake(), channel_id, f.filename, data, f.description
                )
            )
        return attachments


Handler = Callable[[RequestContext], Any]
_ROUTES: dict[str, list[tuple[list[str], Handler]]] = {}


def route(method: str, template: str) -> Callable[[Handler], Handler]:
    def decorator(func: Handler) -> Handler:
        _ROUTES.setdefault(method, []).append((template.strip("/").split("/"), func))
        return func

    return decorator


def dispatch(
    backend: Backend,
    method: str,
    path: str,
    *,
    json: Optional[Any] = None,
    params: Optional[dict[str, Any]] = None,
    files: Optional[list[Any]] = None,
) -> Any:
    backend.http_log.append((method, path, json if isinstance(json, dict) else None))
    _check_faults(backend, method, path)
    segments = path.strip("/").split("/")
    # Prefer the most-literal match so e.g. ".../messages/pins" beats ".../messages/{message_id}".
    best: Optional[tuple[int, dict[str, str], Handler]] = None
    for template, handler in _ROUTES.get(method, ()):
        if len(template) != len(segments):
            continue
        args: dict[str, str] = {}
        literals = 0
        for tpl, seg in zip(template, segments):
            if tpl.startswith("{") and tpl.endswith("}"):
                args[tpl[1:-1]] = seg
            elif tpl == seg:
                literals += 1
            else:
                break
        else:
            if best is None or literals > best[0]:
                best = (literals, args, handler)
    if best is None:
        raise RouteNotImplemented(method, path)
    ctx = RequestContext(backend, best[1], json=json, params=params or {}, files=files or [])
    return best[2](ctx)


def _check_faults(backend: Backend, method: str, path: str) -> None:
    for fault in list(backend.faults):
        if fault["method"] not in (method, "*"):
            continue
        if not fnmatch.fnmatch(path, fault["path"]):
            continue
        if fault["times"] is not None:
            fault["times"] -= 1
            if fault["times"] <= 0:
                backend.faults.remove(fault)
        raise BackendError(fault["status"], fault["code"], fault["message"])


def parse_form(form: list[dict[str, Any]], files: list[Any]) -> tuple[Optional[Any], list[Any]]:
    """Extract the JSON payload from a multipart form built by discord.py."""
    payload = None
    for part in form:
        if part.get("name") == "payload_json":
            payload = _json.loads(part["value"])
    return payload, list(files or [])
