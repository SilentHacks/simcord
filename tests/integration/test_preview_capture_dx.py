import pytest

import simcord


@pytest.mark.asyncio
async def test_screenshot_path_none_returns_png_bytes(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        cap = await preview.screenshot()
        assert cap.path is None
        assert isinstance(cap.png, bytes)
        assert cap.png.startswith(b"\x89PNG")
        assert cap.complete is True
        assert cap.ready is True

        cap2 = await preview.screenshot(tmp_path / "x.png")
        assert cap2.png is None
        assert cap2.path.endswith("x.png")


@pytest.mark.asyncio
async def test_screenshot_fallback_focuses_latest_message(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    # The preview is entered while the channel is still empty, so the Python
    # presentation never received a focus target.
    async with env.preview(channel, viewers=[alice]) as preview:
        await alice.slash(channel, "panel")
        cap = await preview.screenshot(tmp_path / "panel.png")
        assert cap.complete is True
        assert cap.target_id is not None


@pytest.mark.asyncio
async def test_screenshot_empty_channel_still_rejects(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    async with env.preview(channel, viewers=[alice]) as preview:
        with pytest.raises(simcord.SetupError, match="incomplete"):
            await preview.screenshot(tmp_path / "x.png")
        cap = await preview.screenshot(tmp_path / "x.png", mode="viewport", allow_incomplete=True)
        assert cap.complete is False
        assert any(item["code"] == "target-unavailable" for item in cap.diagnostics)


@pytest.mark.asyncio
async def test_simcord_preview_exposes_viewer_and_target_ids(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    result = await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                status = await page.evaluate("() => window.simcordPreview")
                assert status["viewerId"] == str(alice.id)
                assert status["targetId"] == str(result.response.id)
            finally:
                await browser.close()
        finally:
            await playwright.stop()
