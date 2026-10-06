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
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit

from .. import __version__
from ..actors import MemberActor
from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..builders import UserHandle
from ._diagnostics import make_diagnostic
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
    readiness describes settled rendering, completeness whether all in-scope
    sources and rendering requirements were available, and calibration the
    reference-calibration status. ``path`` is the written file destination
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
    media_metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    geometry: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    output_width: int = 0
    output_height: int = 0
    ready: bool = False
    calibrated: bool = False
    calibration: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    action: Mapping[str, Any] | None = None
    png: bytes | None = field(default=None, repr=False)
    schema_version: int = 1
    protocol_version: int = 3
    runtime_version: str = __version__


@dataclass(slots=True)
class CapturePin:
    """A server page containing one immutable, generation-bound projection."""

    page: Any
    snapshot: dict[str, Any]
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
    async def _freeze(page: Any) -> None:
        await page.evaluate(
            """() => {
                document.querySelectorAll('*').forEach((element) => {
                    element.style.setProperty('animation-play-state', 'paused', 'important');
                    element.style.setProperty('transition', 'none', 'important');
                    element.style.setProperty('caret-color', 'transparent', 'important');
                });
                document.getAnimations?.().forEach((animation) => animation.cancel());
            }"""
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
            "fontStackConfigured": '"Noto Sans", "Noto Color Emoji", "Noto Sans Arabic", "Noto Sans Hebrew", "Noto Sans Devanagari", "Noto Sans SC", sans-serif',
            "fontResolution": "unavailable until Chromium platform-font inspection",
            "emojiFallback": "packaged Noto Color Emoji",
            "animationPolicy": "cancel-animations-and-hide-caret",
            "deviceScale": 1,
            "reducedMotion": True,
        }

    @staticmethod
    async def _platform_fonts(page: Any) -> dict[str, Any]:
        """Ask Chromium which platform faces supplied glyphs for the rendered surface."""
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("DOM.enable")
            await cdp.send("CSS.enable")
            document = await cdp.send("DOM.getDocument")
            root_id = int(document["root"]["nodeId"])
            nodes = await cdp.send(
                "DOM.querySelectorAll",
                {
                    "nodeId": root_id,
                    "selector": "#preview-app .message-author, #preview-app .message-content, "
                    "#preview-app .text-display, #preview-app .embed-title, #preview-app .embed-description, "
                    "#preview-app .embed-field-name, #preview-app .embed-field-value, #preview-app button, "
                    "#preview-app label, #preview-app input, #preview-app textarea, #preview-app .modal-title, "
                    "#preview-app .control-label, #preview-app .field-description, #preview-app .select-value-label, "
                    "#preview-app .select-chip-label, #preview-app .reaction-emoji, #preview-app .file-name, "
                    "#preview-app .file-description, #preview-app .upload-file-name",
                },
            )
            faces: list[dict[str, Any]] = []
            surfaces: list[dict[str, Any]] = []
            for index, node_id in enumerate(nodes.get("nodeIds", [])):
                result = await cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node_id})
                used = [dict(face) for face in result.get("fonts", []) if isinstance(face, Mapping)]
                if used:
                    surfaces.append({"index": index, "faces": used})
                    faces.extend(used)
            await cdp.detach()
            return {"available": bool(faces), "faces": faces, "surfaces": surfaces}
        except Exception as exc:  # pragma: no cover - depends on Chromium CDP support
            return {"available": False, "faces": [], "error": str(exc)}

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
        page_errors: list[str] = []
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
                if callable(on := getattr(page, "on", None)):
                    on("pageerror", lambda error: page_errors.append(str(error)))
                await page.route("**/*", lambda route: self._route(route, pin, origin))
                await page.goto(
                    f"{origin}/#{self.preview.capability}", wait_until="domcontentloaded", timeout=30000
                )
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                try:
                    await page.wait_for_function(
                        "() => window.simcordPreview && window.simcordPreview.ready === true",
                        timeout=max(1, remaining - 1000),
                    )
                except Exception as exc:
                    status = (
                        await cast(Any, page).evaluate("() => window.simcordPreview || null")
                        if hasattr(page, "evaluate")
                        else None
                    )
                    diagnostics = status.get("diagnostics", []) if isinstance(status, Mapping) else []
                    details = page_errors + [
                        str(item.get("message"))
                        for item in diagnostics
                        if isinstance(item, Mapping) and item.get("message")
                    ]
                    detail = f": {'; '.join(details)}" if details else ""
                    raise SetupError(f"managed capture readiness deadline exceeded{detail}") from exc

                self.preview._assert_capture_live(pin.page)
                status = await self._status(page)
                if not status.get("authorized") or status.get("transport", {}).get("state") not in {
                    "healthy",
                    "recovered",
                }:
                    raise SetupError("managed capture requires a healthy authorized read")
                platform_fonts = await self._platform_fonts(page)
                runtime_fonts = await page.evaluate(
                    """() => ({
                        computed: getComputedStyle(document.documentElement).fontFamily,
                        loaded: document.fonts ? [...document.fonts]
                          .filter((font) => font.status === "loaded")
                          .map((font) => ({ family: font.family, style: font.style, weight: font.weight })) : [],
                        status: window.simcordPreview?.profile?.fontStatus || null,
                    })"""
                )
                runtime_fonts["platform"] = platform_fonts
                profile["fontResolution"] = runtime_fonts
                last_action = status.get("lastAction")
                if isinstance(last_action, Mapping) and last_action.get("settlement") != "settled":
                    raise SetupError("managed capture requires a settled action")
                diagnostics = self._diagnostics(status)
                if not platform_fonts.get("available"):
                    diagnostics.append(make_diagnostic("font-platform-inspection"))
                render_state = status.get("renderState")
                media_metadata = {
                    "captureTimes": dict(render_state.get("mediaCaptureTimes", {}))
                    if isinstance(render_state, Mapping)
                    else {},
                    "assets": dict(render_state.get("mediaMetadata", {}))
                    if isinstance(render_state, Mapping)
                    else {},
                }
                complete = bool(status.get("complete", True)) and bool(platform_fonts.get("available"))
                if not complete and not allow_incomplete:
                    detail = "; ".join(str(item.get("message", "")) for item in diagnostics)
                    raise SetupError(f"managed capture is incomplete ({detail}); pass allow_incomplete=True")

                await self._freeze(page)
                await self._raf(page)
                geometry = await page.evaluate(
                    """({mode, modal, targetId}) => {
                        const app = document.getElementById('preview-app');
                        const channel = document.getElementById('channel-layout');
                        const inChannel = channel && !channel.hidden;
                        const target = modal ? document.querySelector('.modal-dialog')
                          : inChannel
                            ? [...channel.querySelectorAll('.channel-message[data-message-id]')]
                                .find(element => element.dataset.messageId === targetId)
                            : document.getElementById('focused-content');
                        const owner = modal ? app : inChannel
                          ? document.getElementById('channel-timeline')
                          : document.getElementById('focused-content') || app;
                        const scroll = modal ? document.querySelector('.modal-body') || target : owner;
                        if (!app || !owner || (mode === 'surface' && !target)) return null;
                        const rect = element => {
                          const box = element.getBoundingClientRect();
                          return {x:box.x,y:box.y,width:box.width,height:box.height,
                            right:box.right,bottom:box.bottom};
                        };
                        const viewport = rect(app), bounds = rect(owner);
                        const content = rect(target || app);
                        const crop = mode === 'viewport' ? viewport : {
                          x: Math.max(viewport.x, bounds.x, content.x, 0),
                          y: Math.max(viewport.y, bounds.y, content.y, 0),
                          right: Math.min(viewport.right, bounds.right, content.right, innerWidth),
                          bottom: Math.min(viewport.bottom, bounds.bottom, content.bottom, innerHeight),
                        };
                        crop.width = crop.right - crop.x;
                        crop.height = crop.bottom - crop.y;
                        return {scope:'visible', logicalViewport:viewport, contentExtent:{
                          width:Math.max(content.width, scroll?.scrollWidth || 0),
                          height:Math.max(content.height, scroll?.scrollHeight || 0)},
                          visibleCrop:crop, scrollOffset:{x:scroll?.scrollLeft || 0,y:scroll?.scrollTop || 0},
                          overflow:{horizontal:(scroll?.scrollWidth || 0) > (scroll?.clientWidth || 0),
                            vertical:(scroll?.scrollHeight || 0) > (scroll?.clientHeight || 0)}};
                    }""",
                    {"mode": mode, "modal": pin.modal_id is not None, "targetId": pin.target_id},
                )
                if not isinstance(geometry, Mapping):
                    raise SetupError("managed capture surface is unavailable")
                crop = geometry["visibleCrop"]
                clip = {
                    "x": math.ceil(float(crop["x"])),
                    "y": math.ceil(float(crop["y"])),
                    "width": math.floor(float(crop["right"])) - math.ceil(float(crop["x"])),
                    "height": math.floor(float(crop["bottom"])) - math.ceil(float(crop["y"])),
                }
                output_width, output_height = self._validate_dimensions(clip["width"], clip["height"])
                if mode == "viewport" and (output_width, output_height) != (width, height):
                    raise SetupError("managed capture logical viewport differs from its exact profile")

                temporary_name = f".{destination.name}.simcord-{os.getpid()}-{id(pin):x}.tmp"
                temporary = parent / temporary_name
                await page.screenshot(path=str(temporary), type="png", clip=clip)
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
                        **geometry,
                        "viewportWidth": width,
                        "viewportHeight": height,
                        "outputWidth": output_width,
                        "outputHeight": output_height,
                    },
                    "action": dict(last_action) if isinstance(last_action, Mapping) else None,
                    "media_metadata": media_metadata,
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

    def _pin_capture(
        self,
        viewer: Any,
        target: Any,
        media_time: float,
        viewport: tuple[int, int],
        layout: Literal["message", "channel"],
    ) -> CapturePin:
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
            layout=layout,
            window_end_id=target_id if layout == "channel" else None,
            display="fixed",
            width=viewport[0],
            height=viewport[1],
            host_width=viewport[0],
            host_height=viewport[1],
        )
        if modal is not None:
            capture_page.modal = modal
            capture_page.modal_handle = "m_" + secrets.token_urlsafe(12)
        capture_page.last_action = deepcopy(source.last_action)
        try:
            snapshot = build_snapshot(cast("Preview", self), capture_page)
            snapshot.setdefault("profile", {})["mediaTime"] = media_time
            if target_id is None and modal is None:
                snapshot["diagnostics"] = [
                    *snapshot.get("diagnostics", []),
                    make_diagnostic("target-unavailable"),
                ]
            capture_page.snapshot = snapshot
            capture_page.pinned_snapshot = deepcopy(snapshot)
            capture_page.pinned_generation = self.env._generation
            self._pages[capture_page.id] = capture_page
        except Exception:
            # The page was never registered: release the blob refs the failed
            # snapshot retained so they cannot leak until close().
            self._clear_page_assets(capture_page)
            raise
        profile = dict(snapshot.get("profile", {}))
        profile.update({"deviceScale": 1, "reducedMotion": True})
        return CapturePin(
            capture_page,
            capture_page.pinned_snapshot,
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
        media_time: float = 0.0,
        allow_incomplete: bool = False,
        viewport: tuple[int, int] | None = None,
        layout: Literal["message", "channel"] | None = None,
    ) -> PreviewCapture:
        """Capture one deterministic PNG of the preview, returning a report.

        ``path`` is the file destination (``str`` or ``os.PathLike``) for the
        PNG, or ``None`` to keep the capture in memory and expose the bytes on
        ``PreviewCapture.png``.
        ``viewer`` defaults to the Python presentation viewer. ``target`` may
        be a Message, ResponseMessage, InteractionResult, or snowflake; the
        default is the focused message, falling back to the latest visible
        message. ``mode`` is ``"surface"`` (the visible message or modal dialog
        at its viewport-constrained geometry) or ``"viewport"`` (the full preview
        viewport). ``allow_incomplete`` permits known missing rendering, but
        never bypasses live authorization or capture-source invalidation.
        ``media_time`` selects a deterministic frame time, and
        ``media_metadata`` reports effective times and validated codecs.
        ``viewport`` overrides this capture's exact dimensions without changing
        session defaults. ``layout`` overrides the Python presentation layout.
        Surface captures include only the visible intersection with the owning
        viewport, not all content in a scroll region.
        Channel captures pin a bounded history window containing the target;
        all projected messages and available assets are revalidated before
        rendering and before atomic PNG installation.

        Returns an immutable ``PreviewCapture`` report; ``ready``,
        ``complete`` and ``calibrated`` are independent signals, and ``png``
        holds the image bytes when ``path`` is ``None``.
        """
        if mode not in {"surface", "viewport"}:
            raise SetupError("capture mode must be 'surface' or 'viewport'")
        selected_viewport = viewport
        if selected_viewport is None:
            selected_viewport = (cast("Preview", self).width, cast("Preview", self).height)
        if not isinstance(selected_viewport, tuple) or len(selected_viewport) != 2:
            raise SetupError("capture viewport must be a (width, height) tuple")
        selected_viewport = ManagedCapture._validate_dimensions(*selected_viewport)
        selected_layout = layout
        if selected_layout is None:
            selected_layout = (
                self._python.layout if self._python is not None else cast("Preview", self).layout
            )
        if selected_layout not in {"message", "channel"}:
            raise SetupError("capture layout must be 'message' or 'channel'")
        if not isinstance(allow_incomplete, bool):
            raise SetupError("allow_incomplete must be a boolean")
        if (
            isinstance(media_time, bool)
            or not isinstance(media_time, (int, float))
            or not math.isfinite(media_time)
            or media_time < 0
        ):
            raise SetupError("media_time must be a finite non-negative number")
        media_time = float(media_time)
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
                pin = self._pin_capture(
                    self._capture_viewer(viewer),
                    target,
                    media_time,
                    selected_viewport,
                    cast(Literal["message", "channel"], selected_layout),
                )
            finally:
                self.env._end_operation(token)
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
                media_metadata=_freeze_capture(data["media_metadata"]),
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
            self._capture_task = None


__all__ = ["CapturePin", "ManagedCapture", "PreviewCapture"]
