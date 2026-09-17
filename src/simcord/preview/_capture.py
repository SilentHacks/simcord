"""Optional Playwright-backed deterministic screenshot capture."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import math
import os
import secrets
import tempfile
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from ..actors import MemberActor
from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..builders import UserHandle
from ._pages import _Page
from ._snapshot import build_snapshot

if TYPE_CHECKING:
    from ..builders import ChannelHandle
    from ..env import Env
    from ..results import InteractionResult
    from . import Preview

_CAPTURE_DEADLINE = 30.0
_MAX_AXIS = 32768
_MAX_PIXELS = 32 * 1024 * 1024


def _freeze_capture(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_capture(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_capture(item) for item in value)
    return value


def _capture_destination(path: Any) -> Path:
    try:
        destination = Path(path)
    except TypeError as exc:
        raise SetupError("capture path must be filesystem-like") from exc
    if destination.is_dir():
        raise SetupError("capture path must be a file")
    if not destination.parent.is_dir():
        raise SetupError("capture destination directory does not exist")
    return destination


@dataclass(frozen=True, slots=True)
class PreviewCapture:
    """Immutable capture report returned by ``Preview.screenshot``.

    ``ready``, ``complete`` and ``calibrated`` are independent signals:
    readiness describes the rendered page, completeness whether a focus
    target was available, and calibration whether the bundled page measured
    its own rendering environment. ``path`` is the written file destination
    (``None`` for in-memory captures) and ``png`` holds the image bytes when
    the capture was taken without a path.
    """

    path: str | None
    published_revision: int
    render_generation: int
    viewer_id: str
    channel_id: str
    target_id: str | None
    mode: str = "surface"
    complete: bool = False
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    modal_id: str | None = None
    profile: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    geometry: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    output_width: int = 0
    output_height: int = 0
    ready: bool = False
    calibrated: bool = False
    calibration: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    action: Mapping[str, Any] | None = None
    png: bytes | None = field(default=None, repr=False)


@dataclass(slots=True)
class CapturePin:
    """A server page containing one immutable, generation-bound projection."""

    page: Any
    snapshot: dict[str, Any]
    bot_generation: int
    published_revision: int
    viewer_id: str
    channel_id: str
    target_id: str | None
    modal_id: str | None
    profile: dict[str, Any]


class ManagedCapture:
    """Lazily owns one Playwright browser shared by a Preview session."""

    def __init__(self, preview: Any) -> None:
        self.preview = preview
        self._playwright: Any = None
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - optional runtime
            raise SetupError("Preview screenshots require Playwright; install simcord[screenshot]") from exc
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch()
        except BaseException as exc:  # pragma: no cover - browser installation failure
            await self.close()
            raise SetupError(
                "Preview screenshots require an installed Playwright browser; "
                "run playwright install --with-deps chromium "
                "(or playwright install chromium when system dependencies exist)"
            ) from exc
        return self._browser

    @staticmethod
    def _json_body(value: Mapping[str, Any]) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)

    async def _route(self, route: Any, pin: CapturePin, origin: str) -> None:
        request = route.request
        url = str(request.url)
        parsed = urlsplit(url)
        expected = urlsplit(origin)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
            await route.abort()
            return
        if url == f"{origin}/api/pages":
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=self._json_body(pin.snapshot),
            )
            return
        await route.continue_()

    @staticmethod
    async def _raf(page: Any) -> None:
        await page.evaluate(
            """() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))"""
        )

    @staticmethod
    async def _freeze(page: Any, *, expand_modal: bool = False) -> None:
        await page.evaluate(
            """(expand) => {
                document.querySelectorAll('*').forEach((element) => {
                    element.style.setProperty('animation-play-state', 'paused', 'important');
                    element.style.setProperty('transition', 'none', 'important');
                    element.style.setProperty('caret-color', 'transparent', 'important');
                });
                document.getAnimations?.().forEach((animation) => animation.cancel());
                if (expand) {
                    const modal = document.querySelector('.modal-dialog');
                    if (modal) {
                        modal.style.setProperty('max-height', 'none', 'important');
                        modal.style.setProperty('overflow', 'visible', 'important');
                    }
                }
            }""",
            expand_modal,
        )

    @staticmethod
    async def _status(page: Any) -> Mapping[str, Any]:
        value = await page.evaluate("() => window.simcordPreview")
        if not isinstance(value, Mapping):  # pragma: no cover - bundled page contract
            raise SetupError("managed capture page did not expose simcordPreview status")
        return value

    @staticmethod
    def _validate_dimensions(width: Any, height: Any) -> tuple[int, int]:
        if (
            isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or not isinstance(height, int)
            or width < 1
            or height < 1
            or width > _MAX_AXIS
            or height > _MAX_AXIS
            or width * height > _MAX_PIXELS
        ):
            raise SetupError("capture raster exceeds 32768 pixels per axis or 32 megapixels")
        return width, height

    @staticmethod
    def _diagnostics(status: Mapping[str, Any]) -> list[dict[str, Any]]:
        diagnostics = status.get("diagnostics", [])
        return [dict(item) for item in diagnostics if isinstance(item, Mapping)]

    @staticmethod
    def _browser_metadata(browser: Any) -> dict[str, Any]:
        version = getattr(browser, "version", "unknown")
        try:
            playwright_version = importlib.metadata.version("playwright")
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - fake/test runtime
            playwright_version = "unknown"
        return {
            "playwrightVersion": str(playwright_version),
            "browserVersion": str(version),
            "fontIdentity": "system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
            "emojiFallback": "environment",
            "animationPolicy": "cancel-animations-and-hide-caret",
            "deviceScale": 1,
            "reducedMotion": True,
        }

    async def render(
        self,
        pin: CapturePin,
        destination: Path,
        *,
        mode: str,
        allow_incomplete: bool,
    ) -> dict[str, Any]:
        """Render one pin and atomically install its PNG, returning report data."""
        browser = await self._ensure_browser()
        profile = dict(pin.profile)
        width, height = self._validate_dimensions(profile.get("width"), profile.get("height"))
        profile.update(self._browser_metadata(browser))
        origin = self.preview._origin

        parent = destination.parent
        temporary: Path | None = None
        context: Any = None
        page: Any = None
        deadline = time.monotonic() + _CAPTURE_DEADLINE
        try:
            async with asyncio.timeout(_CAPTURE_DEADLINE):
                context = await browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    reduced_motion="reduce",
                    locale=str(profile.get("locale", "en-US")),
                    timezone_id=str(profile.get("timezone", "UTC")),
                    extra_http_headers={"X-Simcord-Capability": self.preview.capability},
                )
                page = await context.new_page()
                await page.route("**/*", lambda route: self._route(route, pin, origin))
                await page.goto(
                    f"{origin}/#{self.preview.capability}", wait_until="domcontentloaded", timeout=30000
                )
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                try:
                    await page.wait_for_function(
                        "() => window.simcordPreview && window.simcordPreview.ready === true",
                        timeout=remaining,
                    )
                except Exception as exc:
                    raise SetupError("managed capture readiness deadline exceeded") from exc

                self.preview._assert_capture_live(pin.page)
                status = await self._status(page)
                last_action = status.get("lastAction")
                if isinstance(last_action, Mapping) and last_action.get("settlement") != "settled":
                    raise SetupError("managed capture requires a settled action")
                diagnostics = self._diagnostics(status)
                complete = bool(status.get("complete", True))
                if not complete and not allow_incomplete:
                    raise SetupError("managed capture is incomplete; pass allow_incomplete=True")

                await self._freeze(page, expand_modal=pin.modal_id is not None and mode == "surface")
                await self._raf(page)
                self.preview._assert_capture_live(pin.page)
                if mode == "surface":
                    selector = ".modal-dialog" if pin.modal_id is not None else ".message-surface"
                    surface = page.locator(selector)
                    box = await surface.bounding_box()
                    if not isinstance(box, Mapping):  # pragma: no cover - bundled DOM contract
                        raise SetupError("managed capture surface is unavailable")
                    output_width = math.ceil(float(box.get("width", 0)))
                    output_height = math.ceil(float(box.get("height", 0)))
                    output_width, output_height = self._validate_dimensions(output_width, output_height)
                else:
                    await page.evaluate(
                        """() => {
                            document.getElementById('toolbar')?.setAttribute('hidden', '');
                            document.getElementById('diagnostics')?.setAttribute('hidden', '');
                        }"""
                    )
                    await self._raf(page)
                    output_width, output_height = width, height

                temporary_name = f".{destination.name}.simcord-{os.getpid()}-{id(pin):x}.tmp"
                temporary = parent / temporary_name
                if mode == "surface":
                    await surface.screenshot(path=str(temporary), type="png")
                else:
                    await page.screenshot(
                        path=str(temporary),
                        type="png",
                        clip={"x": 0, "y": 0, "width": output_width, "height": output_height},
                    )
                self.preview._assert_capture_live(pin.page)
                os.replace(temporary, destination)
                temporary = None
                calibration = dict(status.get("calibration", {}))
                return {
                    "path": str(destination),
                    "published_revision": pin.published_revision,
                    "render_generation": int(status.get("renderGeneration", 0) or 0),
                    "viewer_id": pin.viewer_id,
                    "channel_id": pin.channel_id,
                    "target_id": pin.target_id,
                    "modal_id": pin.modal_id,
                    "mode": mode,
                    "output_width": output_width,
                    "output_height": output_height,
                    "complete": complete,
                    "ready": bool(status.get("ready", False)),
                    "calibrated": str(calibration.get("status", "uncalibrated")) == "calibrated",
                    "calibration": dict(calibration),
                    "profile": profile,
                    "geometry": {
                        "mode": mode,
                        "viewportWidth": width,
                        "viewportHeight": height,
                        "outputWidth": output_width,
                        "outputHeight": output_height,
                        "surfaceExpanded": bool(pin.modal_id is not None and mode == "surface"),
                    },
                    "action": dict(last_action) if isinstance(last_action, Mapping) else None,
                    "diagnostics": diagnostics,
                }
        except TimeoutError as exc:
            raise SetupError("managed capture readiness deadline exceeded") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            await asyncio.gather(
                *(
                    resource.close() for resource in (page, context) if resource is not None
                ),  # pragma: no branch
                return_exceptions=True,
            )

    async def close(self) -> None:
        resources = ((self._browser, "close"), (self._playwright, "stop"))
        self._browser = None
        self._playwright = None
        await asyncio.gather(
            *(  # pragma: no branch
                getattr(resource, method)() for resource, method in resources if resource is not None
            ),
            return_exceptions=True,
        )


class _CaptureOps:
    """Pinned capture pages plus the managed screenshot entry point."""

    env: Env
    channel: ChannelHandle
    _python: _Page | None
    _pages: dict[str, _Page]
    _active: bool
    _closed: bool
    _capture_manager: ManagedCapture
    _capture_task: asyncio.Task[Any] | None
    _capture_page: _Page | None
    _capture_generation: int
    _publish: Callable[[_Page], None]
    _clear_page_assets: Callable[[_Page], None]
    _viewer: Callable[[Any], Any]
    _resolve_target: Callable[..., tuple[int | None, InteractionResult | None]]
    _initial_target: Callable[[Any, int], int | None]

    def _capture_viewer(self, viewer: Any) -> Any:
        if viewer is None:
            return cast(_Page, self._python).viewer
        if isinstance(viewer, (MemberActor, UserHandle)):
            if viewer._env is not self.env:
                raise SetupError("capture viewer belongs to another Env")
            return self._viewer(viewer.id)
        return self._viewer(viewer)

    def _capture_target(self, viewer: Any, target: Any) -> tuple[int | None, InteractionResult | None]:
        if target is None:
            target_id = cast(_Page, self._python).target_id
            if target_id is not None:
                try:
                    message = self.env.backend.get_message(self.channel.id, target_id)
                except BackendError:
                    message = None
                if message is None or not can_access_message(
                    self.env, self.channel.id, message, viewer, history=True
                ):
                    target_id = None
            if target_id is None:
                # The preview may have opened on an empty channel, or the
                # inherited focus was deleted or made inaccessible to this
                # viewer: fall back to the latest visible message.
                target_id = self._initial_target(viewer, self.channel.id)
            return target_id, None
        return self._resolve_target(viewer, target, capture=True)

    @staticmethod
    def _capture_attachment_ids(snapshot: Mapping[str, Any]) -> dict[str, str]:
        found: dict[str, str] = {}

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                asset_id = value.get("asset_id")
                attachment_id = value.get("attachment_id")
                if isinstance(asset_id, str) and attachment_id is not None:
                    found[asset_id] = str(attachment_id)
                for item in value.get("attachments", ()):
                    found[item["asset_id"]] = str(item.get("id", ""))
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(snapshot.get("selected"))
        return {key: value for key, value in found.items() if value}

    def _pin_capture(self, viewer: Any, target: Any) -> CapturePin:
        if not can_access_channel(self.env, self.channel.id, viewer, history=True):
            raise SetupError("capture viewer cannot access this channel")
        source = cast(_Page, self._python)
        target_id, modal = self._capture_target(viewer, target)
        if modal is None and target is None and source.modal is not None:
            if source.modal._interaction.user_id != viewer.id:
                raise SetupError("capture modal opener is not the requested viewer")
            modal = source.modal
        capture_page = _Page(
            cast("Preview", self),
            "capture_" + secrets.token_urlsafe(12),
            viewer,
            self.channel.id,
            target_id,
            generation=source.generation,
            revision=source.revision,
            status=source.status,
        )
        if modal is not None:
            capture_page.modal = modal
            capture_page.modal_handle = "m_" + secrets.token_urlsafe(12)
        capture_page.last_action = deepcopy(source.last_action)
        try:
            snapshot = build_snapshot(cast("Preview", self), capture_page)
            if target_id is None and modal is None:
                snapshot["diagnostics"] = [
                    *snapshot.get("diagnostics", []),
                    {
                        "code": "target-unavailable",
                        "severity": "warning",
                        "message": "No focused message is available for this capture",
                        "complete": False,
                    },
                ]
            capture_page.snapshot = snapshot
            capture_page.pinned_snapshot = deepcopy(snapshot)
            capture_page.pinned_generation = self.env._generation
            capture_page.pinned_attachment_ids = self._capture_attachment_ids(snapshot)
            self._pages[capture_page.id] = capture_page
        except Exception:
            # The page was never registered: release the blob refs the failed
            # snapshot retained so they cannot leak until close().
            self._clear_page_assets(capture_page)
            raise
        self._capture_generation += 1
        profile = dict(snapshot.get("profile", {}))
        profile.update({"deviceScale": 1, "reducedMotion": True})
        return CapturePin(
            capture_page,
            capture_page.pinned_snapshot,
            self.env._generation,
            int(snapshot.get("publishedRevision", source.revision)),
            str(snapshot.get("viewerId", viewer.id)),
            str(snapshot.get("channelId", self.channel.id)),
            str(snapshot["targetId"]) if snapshot.get("targetId") is not None else None,
            capture_page.modal_handle,
            profile,
        )

    async def screenshot(
        self,
        path: Any = None,
        *,
        viewer: Any = None,
        target: Any = None,
        mode: str = "surface",
        allow_incomplete: bool = False,
    ) -> PreviewCapture:
        """Capture one deterministic PNG of the preview, returning a report.

        ``path`` is the file destination (``str`` or ``os.PathLike``) for the
        PNG, or ``None`` to keep the capture in memory and expose the bytes on
        ``PreviewCapture.png``.
        ``viewer`` defaults to the Python presentation viewer. ``target`` may
        be a Message, ResponseMessage, InteractionResult, or snowflake; the
        default is the focused message, falling back to the latest visible
        message. ``mode`` is ``"surface"`` (just the message surface) or
        ``"viewport"`` (the full preview viewport). ``allow_incomplete``
        permits a capture whose channel has no focusable target.

        Returns an immutable ``PreviewCapture`` report; ``ready``,
        ``complete`` and ``calibrated`` are independent signals, and ``png``
        holds the image bytes when ``path`` is ``None``.
        """
        if mode not in {"surface", "viewport"}:
            raise SetupError("capture mode must be 'surface' or 'viewport'")
        if not isinstance(allow_incomplete, bool):
            raise SetupError("allow_incomplete must be a boolean")
        if not self._active or self._closed:
            raise SetupError("Preview is not active")
        destination = _capture_destination(path) if path is not None else None
        if self._capture_task is not None and not self._capture_task.done():
            raise SetupError("Preview is busy with another capture")
        self._capture_task = asyncio.current_task()
        pin: CapturePin | None = None
        try:
            token = self.env._begin_operation("preview.screenshot")
            try:
                await self.env._settle_internal()
                if self._closed or not self._active:
                    raise SetupError("Preview is closing")
                for page in tuple(self._pages.values()):  # pragma: no branch - bounded snapshot pass
                    # Earlier publishes may have pruned this page already.
                    if page.id in self._pages and page.pinned_snapshot is None:
                        self._publish(page)
                pin = self._pin_capture(self._capture_viewer(viewer), target)
            finally:
                self.env._end_operation(token)
            self._capture_page = pin.page
            png: bytes | None = None
            if destination is None:
                with tempfile.TemporaryDirectory(prefix="simcord-capture-") as temporary_dir:
                    temporary_destination = Path(temporary_dir) / "capture.png"
                    data = await self._capture_manager.render(
                        pin,
                        temporary_destination,
                        mode=mode,
                        allow_incomplete=allow_incomplete,
                    )
                    png = temporary_destination.read_bytes()
            else:
                data = await self._capture_manager.render(
                    pin,
                    destination,
                    mode=mode,
                    allow_incomplete=allow_incomplete,
                )
            return PreviewCapture(
                path=None if destination is None else data["path"],
                published_revision=data["published_revision"],
                render_generation=data["render_generation"],
                viewer_id=data["viewer_id"],
                channel_id=data["channel_id"],
                target_id=data["target_id"],
                mode=data["mode"],
                complete=data["complete"],
                diagnostics=_freeze_capture(data["diagnostics"]),
                modal_id=data["modal_id"],
                profile=_freeze_capture(data["profile"]),
                geometry=_freeze_capture(data["geometry"]),
                output_width=data["output_width"],
                output_height=data["output_height"],
                ready=data["ready"],
                calibrated=data["calibrated"],
                calibration=_freeze_capture(data["calibration"]),
                action=_freeze_capture(data["action"]) if data["action"] is not None else None,
                png=png,
            )
        finally:
            if pin is not None:
                removed = self._pages.pop(pin.page.id, None)
                if removed is not None:
                    self._clear_page_assets(removed)
            self._capture_page = None
            self._capture_task = None


__all__ = ["CapturePin", "ManagedCapture", "PreviewCapture"]
