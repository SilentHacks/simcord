"""Focused checks for deterministic, packaged Preview typography."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from typing import Any

import pytest
from aiohttp import ClientSession

_FONT_DIR = resources.files("simcord.preview").joinpath("static", "fonts")


def _manifest() -> dict[str, Any]:
    return json.loads(_FONT_DIR.joinpath("manifest.json").read_text(encoding="utf-8"))


def test_wheel_contains_pinned_fonts_and_ofl_notices() -> None:
    manifest = _manifest()
    fonts = manifest["fonts"]
    assert len(fonts) == 8
    for item in fonts:
        blob = _FONT_DIR.joinpath(item["filename"]).read_bytes()
        assert len(blob) == item["bytes"]
        assert hashlib.sha256(blob).hexdigest() == item["sha256"]
        notice = _FONT_DIR.joinpath(item["license"]).read_text(encoding="utf-8")
        assert "SIL Open Font License" in notice
        assert item["scripts"]
    assert {item["family"] for item in fonts} >= {
        "Noto Sans",
        "Noto Sans Mono",
        "Noto Color Emoji",
        "Noto Sans Arabic",
        "Noto Sans Hebrew",
        "Noto Sans Devanagari",
        "Noto Sans SC",
    }


@pytest.mark.asyncio
async def test_font_routes_are_explicit_and_same_origin(env, channel, alice) -> None:
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        host = f"localhost:{preview._server.port}"
        async with ClientSession() as client:
            response = await client.get(
                f"{preview._origin}/fonts/noto-sans-latin-v2.015.ttf",
                headers={"Host": host},
            )
            assert response.status == 200
            assert response.content_type == "font/ttf"
            assert (await response.read())[:4] == b"\x00\x01\x00\x00"
            assert "font-src 'self'" in response.headers["Content-Security-Policy"]

            missing = await client.get(f"{preview._origin}/fonts/not-allowlisted.ttf", headers={"Host": host})
            assert missing.status == 404
            traversal = await client.get(f"{preview._origin}/fonts/%2e%2e/_server.py", headers={"Host": host})
            assert traversal.status in {404, 400}


@pytest.mark.asyncio
async def test_browser_uses_packaged_faces_without_system_noto(env, channel, alice) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        try:
            async with playwright.async_playwright() as api:
                browser = await api.chromium.launch()
                context = await browser.new_context(viewport={"width": 960, "height": 720})
                page = await context.new_page()
                await page.goto(preview.url, wait_until="domcontentloaded")
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                status = await page.evaluate("() => window.simcordPreview")
                assert status["complete"] is True
                loaded = set(status["profile"]["fontStatus"]["loaded"])
                assert {
                    "Noto Sans",
                    "Noto Sans Mono",
                    "Noto Color Emoji",
                    "Noto Sans Arabic",
                    "Noto Sans Hebrew",
                    "Noto Sans Devanagari",
                    "Noto Sans SC",
                } <= loaded
                checks = await page.evaluate(
                    """() => [
                        document.fonts.check('normal 16px "Noto Color Emoji"', '👩🏽‍💻❤️‍🔥'),
                        document.fonts.check('normal 16px "Noto Sans Arabic"', 'مرحبا'),
                        document.fonts.check('normal 16px "Noto Sans Hebrew"', 'שלום'),
                        document.fonts.check('normal 16px "Noto Sans Devanagari"', 'नमस्ते'),
                        document.fonts.check('normal 16px "Noto Sans SC"', '你好'),
                    ]"""
                )
                assert checks == [True, True, True, True, True]
                cdp = await context.new_cdp_session(page)
                await cdp.send("DOM.enable")
                await cdp.send("CSS.enable")
                document = await cdp.send("DOM.getDocument")
                node = await cdp.send(
                    "DOM.querySelector",
                    {
                        "nodeId": document["root"]["nodeId"],
                        "selector": ".message-content, .message-author, .text-display, button",
                    },
                )
                platform = await cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]})
                assert any(face.get("isCustomFont") for face in platform["fonts"])
                await context.close()
                await browser.close()
        except Exception as exc:
            if "Executable doesn't exist" in str(exc) or "browserType.launch" in str(exc):
                pytest.skip(str(exc))
            raise


@pytest.mark.asyncio
async def test_font_load_failure_is_incomplete(env, channel, alice) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        try:
            async with playwright.async_playwright() as api:
                browser = await api.chromium.launch()
                context = await browser.new_context(viewport={"width": 960, "height": 720})
                page = await context.new_page()

                async def fail_latin(route: Any) -> None:
                    await route.abort()

                await page.route("**/fonts/noto-sans-latin-v2.015.ttf", fail_latin)
                await page.goto(preview.url, wait_until="domcontentloaded")
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                status = await page.evaluate("() => window.simcordPreview")
                assert status["complete"] is False
                assert any(item["code"] == "preview-fonts" for item in status["diagnostics"])
                await context.close()
                await browser.close()
        except Exception as exc:
            if "Executable doesn't exist" in str(exc) or "browserType.launch" in str(exc):
                pytest.skip(str(exc))
            raise
