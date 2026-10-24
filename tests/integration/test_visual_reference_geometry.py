import asyncio
import importlib.util
import io
from pathlib import Path

import pytest

_GEOMETRY = Path(__file__).resolve().parents[2] / "src/simcord/preview/static/geometry.js"


def _script(name):
    path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("host", [(420, 360), (1400, 1000)])
@pytest.mark.parametrize("state", ["idle", "open", "primary-focus", "primary-hover"])
async def test_reference_crop_preserves_live_state_and_natural_pixels(env, channel, alice, host, state):
    pytest.importorskip("playwright")
    Image = pytest.importorskip("PIL.Image")
    from playwright.async_api import async_playwright

    replay = _script("replay_visual_reference")
    catalog = replay._catalog()
    reference = "REF-30-STRING-SELECT" if state == "open" else "REF-20-BUTTONS"
    payload = catalog.gallery_payload(reference)
    try:
        message = await env.bot.get_channel(channel.id).send(**payload)
        async with env.preview(channel, viewers=[alice], width=400, height=300, display="fixed") as preview:
            await preview.show(message)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page(viewport={"width": host[0], "height": host[1]})
                    await page.goto(preview.url)
                    await replay._ready(page)
                    # Focused layout has no channel article: use the real focused surface.
                    surface = page.locator("#focused-content")
                    if state == "open":
                        await surface.get_by_role("combobox").first.click()
                    elif state == "primary-focus":
                        await surface.get_by_role("button", name="Primary", exact=True).focus()
                    elif state == "primary-hover":
                        await surface.get_by_role("button", name="Primary", exact=True).hover()
                    await replay._ready(page)
                    focused = await page.evaluate_handle("document.activeElement")
                    before_scroll = await surface.evaluate(
                        "element => ({top:element.scrollTop,left:element.scrollLeft})"
                    )
                    measured = await surface.evaluate("""async element => {
                      const {measureCapture,rasterCrop} = await import('/geometry.js');
                      const geometry = measureCapture({mode:'surface',target:element,owner:element,
                        scroll:element,app:document.querySelector('#preview-app'),stage:document.querySelector('#preview-stage')});
                      return {...geometry, clip:rasterCrop(geometry.visibleCrop)};
                    }""")
                    pixels = await page.screenshot(clip=measured["clip"])
                    with Image.open(io.BytesIO(pixels)) as image:
                        assert image.size == (measured["clip"]["width"], measured["clip"]["height"])
                    assert measured["logicalViewport"]["width"] == 400
                    assert measured["logicalViewport"]["height"] == 300
                    assert await page.evaluate("element => document.activeElement === element", focused)
                    assert (
                        await surface.evaluate("element => ({top:element.scrollTop,left:element.scrollLeft})")
                        == before_scroll
                    )
                    await focused.dispose()
                    if state == "open":
                        assert await page.get_by_role("listbox").is_visible()
                        if host[0] == 1400:
                            popup_pixels, popup_crop = await replay._natural_capture(
                                page, surface.get_by_role("combobox").first, popup=True
                            )
                            with Image.open(io.BytesIO(popup_pixels)) as image:
                                assert image.size == (popup_crop["width"], popup_crop["height"])
                    if state == "primary-hover":
                        assert await surface.get_by_role("button", name="Primary", exact=True).evaluate(
                            "el => el.matches(':hover')"
                        )
                finally:
                    await browser.close()
    finally:
        catalog.close_payload(payload)


@pytest.mark.asyncio
async def test_fractional_scrolled_intersection_and_complete_popup_policy():
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    source = await asyncio.to_thread(_GEOMETRY.read_text, encoding="utf-8")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 500, "height": 400})
            await page.route(
                "http://geometry.test/geometry.js",
                lambda route: route.fulfill(body=source, content_type="text/javascript"),
            )
            await page.route(
                "http://geometry.test/",
                lambda route: route.fulfill(
                    body="""
              <style>*{box-sizing:border-box}body{margin:0}
              #app{position:absolute;left:10.25px;top:20.25px;width:300px;height:200px}
              #stage{width:250.5px;height:150.5px;overflow:hidden}
              #owner{width:220.5px;height:120.5px;overflow:auto}
              #target{width:400px;height:300px}
              #menu{position:absolute;left:240.25px;top:30.25px;width:80.5px;height:100.5px}
              </style><div id=app><div id=stage><div id=owner><div id=target></div></div></div></div><div id=menu></div>
            """,
                    content_type="text/html",
                ),
            )
            await page.goto("http://geometry.test/")
            result = await page.evaluate("""async () => {
              const {measureCapture,ownerGeometry,rasterCrop,completeCrop} = await import('/geometry.js');
              const app=document.querySelector('#app'), stage=document.querySelector('#stage');
              const owner=document.querySelector('#owner'), target=document.querySelector('#target');
              owner.scrollTop=37; owner.scrollLeft=19;
              const measured=measureCapture({mode:'surface',app,stage,owner,target,scroll:owner});
              let rejected=false;
              document.querySelector('#menu').style.left='480px';
              try { completeCrop([owner,document.querySelector('#menu')]); } catch { rejected=true; }
              document.querySelector('#menu').style.left='240.25px';
              return {measured, clip:rasterCrop(measured.visibleCrop),
                owner:ownerGeometry(owner,app,stage), complete:completeCrop([owner,document.querySelector('#menu')]),rejected};
            }""")
            assert result["measured"]["scrollOffset"] == {"x": 19, "y": 37}
            assert result["clip"] == {"x": 11, "y": 21, "width": 219, "height": 119}
            assert result["owner"]["visibleCrop"]["width"] == 220.5
            assert result["complete"] == {"x": 10, "y": 20, "width": 311, "height": 121}
            assert result["rejected"]
        finally:
            await browser.close()
