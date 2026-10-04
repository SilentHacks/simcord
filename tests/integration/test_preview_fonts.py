"""Focused checks for deterministic, packaged Preview typography."""

from __future__ import annotations

import hashlib
import io
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
async def test_corrupt_font_manifest_fails_with_install_guidance(env, channel, alice, monkeypatch, tmp_path):
    import simcord.preview as preview_module
    from simcord.backend.errors import SetupError

    item = dict(_manifest()["fonts"][0])
    item.pop("style")
    filename = item["filename"]
    (tmp_path / filename).write_bytes(_FONT_DIR.joinpath(filename).read_bytes())
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"fonts": [item]}), encoding="utf-8")
    monkeypatch.setattr(preview_module, "_PREVIEW_FONT_MANIFEST", manifest)
    preview_module._require_preview_runtime.cache_clear()
    try:
        with pytest.raises(SetupError, match="typography assets are missing or corrupt"):
            async with env.preview(channel, viewers=[alice]):
                pass
    finally:
        preview_module._require_preview_runtime.cache_clear()


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
async def test_font_snapshot_is_detached_from_cached_manifest(env, channel, alice) -> None:
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python
        first = preview._page_payload(page)["profile"]["fontAssets"]
        assert "weight" in first[0] and "weights" not in first[0]
        first[0]["family"] = "mutated"
        first[0]["scripts"].append("mutated")
        second = preview._page_payload(page)["profile"]["fontAssets"]
        assert second[0]["family"] == "Noto Sans"
        assert "mutated" not in second[0]["scripts"]


@pytest.mark.asyncio
async def test_browser_uses_packaged_faces_without_system_noto(env, channel, alice) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    import discord
    from PIL import Image

    corpus = "Latin عربي שלום 中文 नमस्ते 👩🏽‍💻👍🏽🇺🇳1️⃣♥︎♥️"
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=corpus, emoji="👩🏽‍💻", custom_id="glyph-button"))
    view.add_item(
        discord.ui.Select(
            custom_id="glyph-select",
            options=[discord.SelectOption(label=corpus, description=corpus, value="glyph", default=True)],
        )
    )
    embed = discord.Embed(title=corpus, description=corpus)
    embed.add_field(name=corpus, value=corpus)
    await env.bot.get_channel(channel.id).send(f"{corpus}\n`{corpus}`\n*🇺🇳*", embed=embed, view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        try:
            async with playwright.async_playwright() as api:
                browser = await api.chromium.launch()
                context = await browser.new_context(viewport={"width": 1280, "height": 900})
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
                selectors = [
                    ".message-content",
                    ".embed-title",
                    ".embed-description",
                    ".embed-field-name",
                    ".embed-field-value",
                    ".component-button",
                    ".select-value",
                ]
                for selector in selectors:
                    node = await cdp.send(
                        "DOM.querySelector",
                        {
                            "nodeId": document["root"]["nodeId"],
                            "selector": f"#focused-content {selector}",
                        },
                    )
                    platform = await cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]})
                    used = [face for face in platform["fonts"] if face["glyphCount"] > 0]
                    assert used and all(face["isCustomFont"] for face in used), (selector, used)
                    assert any(face["familyName"] == "Noto Color Emoji" for face in used), (selector, used)
                node = await cdp.send(
                    "DOM.querySelector",
                    {
                        "nodeId": document["root"]["nodeId"],
                        "selector": ".message-content em",
                    },
                )
                platform = await cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]})
                assert {face["familyName"] for face in platform["fonts"] if face["glyphCount"] > 0} == {
                    "Noto Color Emoji"
                }
                # A loaded face can still lack flag ligatures (the upstream noflags build did).
                flag = Image.open(io.BytesIO(await page.locator(".message-content em").screenshot())).convert(
                    "RGB"
                )
                assert any(
                    blue >= red + 50 and blue > green + 20
                    for red, green, blue in (
                        flag.getpixel((x, y)) for x in range(flag.width) for y in range(flag.height)
                    )
                )
                await page.locator(".select-trigger").click()
                await page.wait_for_selector(".select-list:not([hidden])")
                node = await cdp.send(
                    "DOM.querySelector",
                    {
                        "nodeId": document["root"]["nodeId"],
                        "selector": ".select-option",
                    },
                )
                platform = await cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]})
                assert any(
                    face["familyName"] == "Noto Color Emoji" and face["glyphCount"] > 0
                    for face in platform["fonts"]
                )
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
