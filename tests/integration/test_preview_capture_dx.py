import discord
import pytest
from preview_helpers import png_bytes

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
        result = await alice.slash(channel, "panel")
        cap = await preview.screenshot(tmp_path / "panel.png")
        assert cap.complete is True
        assert cap.target_id == str(result.response.id)


@pytest.mark.asyncio
async def test_screenshot_falls_back_when_focus_deleted(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    await alice.slash(channel, "panel")
    focused = channel.last_message
    async with env.preview(channel, viewers=[alice]) as preview:
        await alice.send(channel, "fallback target")
        await focused.delete()
        # The inherited focus is gone: the capture falls back to the latest
        # visible message instead of failing mid-render.
        cap = await preview.screenshot(tmp_path / "x.png")
        assert cap.complete is True
        assert cap.target_id == str(channel.last_message.id)


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


@pytest.mark.asyncio
async def test_browser_ready_waits_for_preview_media(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    image_url = "https://cdn.example.test/ready.png"
    result = await env.bot.get_channel(channel.id).send(embed=discord.Embed().set_image(url=image_url))
    async with env.preview(
        channel,
        viewers=[alice],
        assets={image_url: ("ready.png", png_bytes())},
    ) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert await page.locator("img.embed-image").count() == 1
                assert await page.locator("img.embed-image").evaluate(
                    "(image) => image.complete && image.naturalWidth > 0"
                )
                assert (await page.evaluate("() => window.simcordPreview"))["targetId"] == str(result.id)
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_settled_action_clears_pending_and_allows_second_action(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("button", name="Ping").click()
                await page.wait_for_function(
                    "() => window.simcordPreview?.lastAction?.sequence === 1 "
                    "&& window.simcordPreview.pendingAction === null"
                )
                await page.get_by_role("button", name="Ping").click()
                await page.wait_for_function(
                    "() => window.simcordPreview?.lastAction?.sequence === 2 "
                    "&& window.simcordPreview.pendingAction === null"
                )
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_select_navigation_retains_list_focus(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    await alice.slash(channel, "color")
    async with env.preview(channel, viewers=[alice]) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                trigger = page.locator('button[aria-haspopup="listbox"]')
                await trigger.focus()
                await trigger.press("ArrowDown")
                listbox = page.locator('[role="listbox"]')
                await page.wait_for_function(
                    "() => document.activeElement?.getAttribute('role') === 'listbox'"
                )
                first = await listbox.get_attribute("aria-activedescendant")
                await listbox.press("ArrowDown")
                await page.wait_for_function(
                    """(first) => document.activeElement?.getAttribute("role") === "listbox"
                    && document.activeElement.getAttribute("aria-activedescendant") !== first""",
                    arg=first,
                )
                assert await page.locator('[role="listbox"]:focus').count() == 1
                assert (await page.evaluate("() => window.simcordPreview")).get("pendingAction") is None
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_modal_traps_focus_and_escape_restores_surface(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => document.activeElement?.closest('.modal-dialog') !== null"
                )
                assert await page.locator(".message-surface").get_attribute("inert") is not None
                for _ in range(8):
                    assert await page.locator(".modal-dialog :focus").count() == 1
                    await page.keyboard.press("Tab")
                await page.keyboard.press("Escape")
                await page.wait_for_function("() => !document.querySelector('.modal-dialog')")
                assert await page.locator(".message-surface").get_attribute("inert") is None
                assert await page.locator("#viewer-picker:focus").count() == 1
                await page.reload()
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("textbox").fill("Ada")
                await page.get_by_role("button", name="Submit").click()
                await page.wait_for_function(
                    "() => window.simcordPreview?.lastAction?.settlement === 'settled' "
                    "&& window.simcordPreview.pendingAction === null"
                )
                assert channel.last_message.content == "Thanks Ada"
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_reloads_release_page_contexts(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                for _ in range(preview._MAX_PAGES + 4):
                    await page.reload()
                    await page.wait_for_function("() => window.simcordPreview?.ready === true")
            finally:
                await browser.close()
        finally:
            await playwright.stop()
