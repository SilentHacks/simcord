"""Optional Playwright-backed deterministic screenshot capture."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..backend.errors import SetupError

_CAPTURE_DEADLINE = 30.0
_MAX_AXIS = 32768
_MAX_PIXELS = 32 * 1024 * 1024


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
        self._closing = False

    async def _ensure_browser(self) -> Any:
        if self._closing:
            raise SetupError("Preview capture is closing")
        if self._browser is not None:
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - optional runtime
            raise SetupError("Preview screenshots require Playwright; install simcord[screenshot]") from exc
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch()
        except BaseException as exc:
            await self.close()
            raise SetupError("Preview screenshots require an installed Playwright browser") from exc
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
        if not isinstance(value, Mapping):
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
        if callable(version):
            version = version()
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
        origin = self.preview.origin
        if not origin:
            raise SetupError("Preview is not active")

        parent = destination.parent if destination.parent != Path("") else Path(".")
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
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
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
                    if not isinstance(box, Mapping):
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
                calibration = status.get("calibration", {})
                if not isinstance(calibration, Mapping):
                    calibration = {}
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
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            if page is not None:
                try:
                    await page.close()
                except BaseException:
                    pass
            if context is not None:
                try:
                    await context.close()
                except BaseException:
                    pass

    async def close(self) -> None:
        self._closing = True
        browser, playwright = self._browser, self._playwright
        self._browser = None
        self._playwright = None
        if browser is not None:
            try:
                await browser.close()
            except BaseException:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except BaseException:
                pass


__all__ = ["CapturePin", "ManagedCapture"]
