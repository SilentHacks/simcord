"""Route table mapping (method, path template) -> backend handlers.

Unknown routes always fail loudly with :class:`RouteNotImplemented` — a
testing tool must never silently fake success. The router also services
test-injected faults (``env.inject_error``) and records every call to
``backend.http_log`` for advanced assertions.
"""

from __future__ import annotations

import fnmatch
import json as _json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..backend import Backend
from ..backend.errors import BackendError
from ..backend.models import Channel


class RouteNotImplemented(BackendError):
    """An unimplemented route — a parity gap that must surface loudly.

    Although it subclasses :class:`BackendError`, the transports catch it (and
    :class:`UnsupportedField`) *before* the generic ``except BackendError`` that
    maps backend failures onto Discord errors, so it is never disguised as an
    ``HTTPException`` a broad ``except`` could swallow. For the same reason a
    handler must never wrap a route lookup or :meth:`RequestContext.fields` in a
    broad ``except BackendError`` — doing so would re-hide the parity gap.
    """

    def __init__(self, method: str, path: str) -> None:
        super().__init__(501, 0, f"simcord does not implement '{method} {path}' yet.")
        self.method = method
        self.path = path
        self.add_note(
            "See the parity matrix in the docs; please open an issue if your bot needs this route: "
            "https://github.com/SilentHacks/simcord/issues"
        )


class UnsupportedField(BackendError):
    """A handler was sent a request field it does not honour.

    The field-level analogue of :class:`RouteNotImplemented`: silently dropping
    an edit field would let a bot test pass while the real edit diverges. Like
    ``RouteNotImplemented`` it is *not* disguised as an ``HTTPException`` (the
    transports re-raise it), so a broad ``except discord.HTTPException`` cannot
    swallow a parity gap. It does subclass :class:`BackendError`, so — as with
    ``RouteNotImplemented`` — a handler must never wrap :meth:`RequestContext.fields`
    in a broad ``except BackendError``, which would re-hide the gap.
    """

    def __init__(self, method: str, path: str, fields: list[str]) -> None:
        joined = ", ".join(fields)
        super().__init__(
            501, 0, f"simcord does not implement the field(s) [{joined}] on '{method} {path}' yet."
        )
        self.method = method
        self.path = path
        self.fields = list(fields)
        self.add_note(
            "See the parity matrix in the docs; please open an issue if your bot needs this field: "
            "https://github.com/SilentHacks/simcord/issues"
        )


@dataclass
class RequestContext:
    backend: Backend
    args: dict[str, str]
    json: Any | None = None
    params: dict[str, Any] = field(default_factory=dict)
    files: list[Any] = field(default_factory=list)
    #: The ``X-Audit-Log-Reason`` discord.py attached to this call, if any.
    reason: str | None = None
    method: str = ""
    path: str = ""

    def int_arg(self, name: str) -> int:
        return int(self.args[name])

    def fields(self, *handled: str, ignore: tuple[str, ...] = ()) -> dict[str, Any]:
        """Pull the recognised ``handled`` keys out of the JSON body, failing
        loudly on any other key.

        Replaces the ``{k: body[k] for k in editable if k in body}`` idiom that
        silently dropped unrecognised edit fields. ``ignore`` names keys we
        deliberately accept-and-discard (e.g. a field Discord lets you send but
        that has no meaning for an offline test). Anything neither handled nor
        ignored raises :class:`UnsupportedField` — a parity gap must be loud.
        """
        body = self.body()
        allowed = set(handled) | set(ignore)
        unsupported = sorted(key for key in body if key not in allowed)
        if unsupported:
            raise UnsupportedField(self.method, self.path, unsupported)
        return {key: body[key] for key in handled if key in body}

    def list_fields(self, *handled: str, ignore: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """The :meth:`fields` honesty guarantee for JSON-array bodies.

        The bulk reorder endpoints (``PATCH /guilds/{id}/roles`` and
        ``PATCH /guilds/{id}/channels``) send a *list* of per-item dicts rather
        than a single object, so :meth:`fields` does not apply. Vet every item's
        keys the same way — anything neither handled nor ignored across any item
        raises :class:`UnsupportedField` — and return the items unchanged so a
        parity gap in a bulk payload is just as loud as in a scalar one.
        """
        items = [item for item in (self.json or []) if isinstance(item, Mapping)]
        allowed = set(handled) | set(ignore)
        unsupported = sorted({key for item in items for key in item if key not in allowed})
        if unsupported:
            raise UnsupportedField(self.method, self.path, unsupported)
        return [dict(item) for item in items]

    def require_channel_permissions(self, channel_id: int, *names: str) -> Channel:
        """Check the bot has ``names`` in a channel; return the channel.

        Consolidates the get-channel-then-permission-check preamble most route
        handlers share, so a missing check is a missing call rather than buried
        in a handler body.
        """
        channel = self.backend.get_channel(channel_id)
        self.backend.require_permissions(channel.guild_id, self.backend.bot_user.id, channel.id, *names)
        return channel

    def require_guild_permissions(self, guild_id: int, *names: str) -> None:
        """Check the bot has guild-level ``names`` (no channel context)."""
        self.backend.require_permissions(guild_id, self.backend.bot_user.id, None, *names)

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
    json: Any | None = None,
    params: dict[str, Any] | None = None,
    files: list[Any] | None = None,
    reason: str | None = None,
) -> Any:
    backend.http_log.append((method, path, json if isinstance(json, dict) else None))
    backend.transcript.append(("HTTP", f"{method} {path}", json if isinstance(json, dict) else None))
    _check_faults(backend, method, path)
    segments = path.strip("/").split("/")
    # Prefer the most-literal match so e.g. ".../messages/pins" beats ".../messages/{message_id}".
    best: tuple[int, dict[str, str], Handler] | None = None
    for template, handler in _ROUTES.get(method, ()):
        if len(template) != len(segments):
            continue
        args: dict[str, str] = {}
        literals = 0
        for tpl, seg in zip(template, segments, strict=True):
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
    ctx = RequestContext(
        backend,
        best[1],
        json=json,
        params=params or {},
        files=files or [],
        reason=reason,
        method=method,
        path=path,
    )
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


def parse_form(form: list[dict[str, Any]], files: list[Any]) -> tuple[Any | None, list[Any]]:
    """Extract the JSON payload from a multipart form built by discord.py.

    Most multipart calls (messages/webhooks with attachments) carry the body in a
    single ``payload_json`` part. A few — notably sticker creation — instead send
    each field as its own scalar part (``name``/``description``/``tags``) beside
    the file. Reconstruct those into a body dict so the route handler sees them
    (and can vet them through :meth:`RequestContext.fields`); ``payload_json``
    still wins when present.
    """
    payload = None
    scalar: dict[str, Any] = {}
    for part in form:
        name = part.get("name")
        if name == "payload_json":
            payload = _json.loads(part["value"])
        elif name is not None and "filename" not in part:
            scalar[name] = part["value"]
    if payload is None and scalar:
        payload = scalar
    return payload, list(files or [])
