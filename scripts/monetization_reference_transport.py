"""Durable, redacted evidence streams and non-replaying HTTP transport."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp

REDACTED = "[REDACTED]"


def redact(value, secret=""):
    if isinstance(value, dict):
        return {
            key: REDACTED
            if key.lower()
            in {"token", "authorization", "session_id", "access_token", "refresh_token", "client_secret"}
            else redact(item, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, REDACTED)
    return value


def stream_counts(directory):
    counts = {}
    for stream in ("requests", "rest", "gateway", "interactions", "health", "actions"):
        path = directory / f"{stream}.jsonl"
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            for line in lines:
                if not line.endswith("\n"):
                    raise RuntimeError(f"Truncated evidence in {stream}.jsonl; preserve it before recovery.")
                if not isinstance(json.loads(line), dict):
                    raise RuntimeError(f"Malformed evidence in {stream}.jsonl; preserve it before recovery.")
            counts[stream] = len(lines)
    return counts


class Evidence:
    def __init__(self, directory, secret, *, resume=False):
        directory.mkdir(mode=0o700, parents=True, exist_ok=resume)
        self.directory = directory
        self.secret = secret
        self.action = "setup"
        self.counts = stream_counts(directory)

    def record(self, stream, **data):
        row = {
            "utc": datetime.now(UTC).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "action": self.action,
            **data,
        }
        with (self.directory / f"{stream}.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(redact(row, self.secret), ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        self.counts[stream] = self.counts.get(stream, 0) + 1
        return f"{stream}.jsonl:{self.counts[stream]}"

    def mark(self, action):
        self.action = action
        self.record("actions", event="begin")
        print(action, flush=True)

    def manifest(self, **data):
        target = self.directory / "manifest.json"
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(json.dumps(redact(data, self.secret), indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def require_capture_transport():
    if "middlewares" not in inspect.signature(aiohttp.ClientSession).parameters:
        raise RuntimeError(
            "Monetization capture tooling requires aiohttp>=3.12 with public request middleware support; no credentials or mutations were requested."
        )


async def _single_write(request, handler):
    try:
        return await handler(request)
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as error:
        # aiohttp retries idempotent verbs internally. A recorded DELETE is still
        # an evidence mutation: prevent that retry without touching private state.
        raise aiohttp.ClientError(type(error).__name__) from None


class Rest:
    def __init__(self, session, evidence, base="https://discord.com/api/v10"):
        require_capture_transport()
        self.session = session
        self.evidence = evidence
        self.base = base

    async def request(self, method, path, *, query=None, body=None) -> tuple[int, Any]:
        for attempt in range(6):
            started = datetime.now(UTC).isoformat()
            request_id = f"requests.jsonl:{self.evidence.counts.get('requests', 0) + 1}"
            self.evidence.record(
                "requests",
                request_id=request_id,
                method=method,
                path=path,
                query=query,
                body=body,
                attempt=attempt + 1,
            )
            # Never replay a write on a transport error: its outcome may be unknown.
            try:
                async with self.session.request(
                    method,
                    self.base + path,
                    params=query,
                    json=body,
                    allow_redirects=False,
                    middlewares=(_single_write,) if method != "GET" else (),
                ) as response:
                    text = await response.text()
                    try:
                        data = json.loads(text) if text else None
                    except ValueError:
                        data = text
                    self.evidence.record(
                        "rest",
                        request_id=request_id,
                        started_utc=started,
                        method=method,
                        path_query=response.url.raw_path_qs,
                        body=body,
                        status=response.status,
                        response=data,
                        attempt=attempt + 1,
                        rate_limit={
                            key: value
                            for key, value in response.headers.items()
                            if key.lower().startswith("x-ratelimit") or key.lower() in {"retry-after", "date"}
                        },
                    )
                    if response.status != 429:
                        return response.status, data
                    if not isinstance(data, dict) or not isinstance(data.get("retry_after"), (int, float)):
                        raise RuntimeError("Rate limit response lacked retry_after; stopped.")
            except (aiohttp.ClientError, TimeoutError, asyncio.CancelledError) as error:
                self.evidence.record(
                    "rest",
                    request_id=request_id,
                    started_utc=started,
                    method=method,
                    path_query=path,
                    body=body,
                    status=None,
                    response=None,
                    error=type(error).__name__,
                    outcome_unknown=method != "GET",
                )
                raise
            await asyncio.sleep(max(0.1, data["retry_after"]))
        raise RuntimeError("Repeated rate limits; stopped. See rest.jsonl.")

    async def require(self, method, path, **kwargs):
        status, data = await self.request(method, path, **kwargs)
        if not 200 <= status < 300:
            raise RuntimeError(f"{method} {path}: HTTP {status}; see rest.jsonl. Nothing was published.")
        return data


async def await_gateway_ready(client, gateway):
    ready = asyncio.create_task(client.wait_until_ready())
    try:
        done, _ = await asyncio.wait({ready, gateway}, timeout=60, return_when=asyncio.FIRST_COMPLETED)
        if gateway in done:
            await gateway
            raise RuntimeError("Gateway ended before READY.")
        if ready not in done:
            raise RuntimeError("Gateway READY timed out.")
        await ready
    finally:
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
