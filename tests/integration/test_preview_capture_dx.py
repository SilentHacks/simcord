import io

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
async def test_browser_decode_failure_marks_visible_media_incomplete(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    image_url = "https://cdn.example.test/broken-in-browser.png"
    await env.bot.get_channel(channel.id).send(embed=discord.Embed().set_image(url=image_url))
    async with env.preview(
        channel,
        viewers=[alice],
        assets={image_url: ("image.png", png_bytes())},
    ) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.add_init_script(
                    "HTMLImageElement.prototype.decode = () => Promise.reject(new Error('decode failed'))"
                )
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                status = await page.evaluate("() => window.simcordPreview")
                assert status["complete"] is False
                assert any(item["code"] == "media-unavailable" for item in status["diagnostics"])
                assert await page.locator(".media-unavailable").count() >= 1
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_attachment_dimensions_spoilers_files_and_local_lightbox(env, channel, alice):
    pytest.importorskip("playwright")
    pytest.importorskip("PIL")
    from PIL import Image
    from playwright.async_api import async_playwright

    def png(size, color):
        output = io.BytesIO()
        Image.new("RGB", size, color).save(output, format="PNG")
        return output.getvalue()

    await alice.send(
        channel,
        "attachment media",
        attachments=[
            ("landscape.png", png((4, 2), (10, 20, 30))),
            ("portrait.png", png((2, 4), (40, 50, 60))),
            ("SPOILER_secret.png", png((3, 5), (70, 80, 90))),
            ("unsafe.html", b"<script>window.previewInjected = true</script>"),
            ("notes.txt", b"safe text"),
        ],
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                external_requests = []
                page.on(
                    "request",
                    lambda request: (
                        external_requests.append(request.url)
                        if not request.url.startswith((preview._origin, "blob:"))
                        else None
                    ),
                )
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")

                assert await page.locator(".message-attachments").get_attribute("data-image-count") == "3"
                images = page.locator("img.attachment-image")
                assert await images.count() == 3
                assert await images.nth(0).evaluate("(image) => [image.width, image.height]") == [4, 2]
                assert await images.nth(1).evaluate("(image) => [image.width, image.height]") == [2, 4]
                assert await page.locator(".spoiler-content button button").count() == 0

                unsafe = page.locator(".attachment:not(.attachment-inline)").filter(has_text="unsafe.html")
                assert await unsafe.count() == 1
                assert await unsafe.locator("script, iframe, object, embed, svg").count() == 0
                preview_button = unsafe.get_by_role("button", name="Preview")
                assert await preview_button.is_enabled()
                assert await unsafe.locator(".attachment-size").inner_text() == "1 KB"
                await preview_button.click()
                assert await preview_button.get_attribute("aria-expanded") == "true"
                assert (
                    await unsafe.locator(".attachment-preview").text_content()
                    == "<script>window.previewInjected = true</script>"
                )
                assert await page.get_by_role("button", name="Download unsafe.html").is_enabled()

                opener = page.locator(".attachment-inline .media-lightbox-trigger").nth(0)
                await opener.click()
                lightbox = page.locator("dialog.media-lightbox")
                await page.wait_for_function("() => document.querySelector('.media-lightbox')?.open")
                view = lightbox.locator("img")
                assert await view.evaluate("(image) => [image.naturalWidth, image.naturalHeight]") == [4, 2]
                await page.keyboard.press("ArrowRight")
                assert await view.evaluate("(image) => [image.naturalWidth, image.naturalHeight]") == [2, 4]
                await page.keyboard.press("ArrowRight")
                assert await view.evaluate("(image) => [image.naturalWidth, image.naturalHeight]") == [4, 2]
                await page.keyboard.press("Escape")
                assert await opener.evaluate("(element) => element === document.activeElement")

                spoiler = page.locator(".attachment-inline").nth(2)
                await spoiler.locator(".spoiler-cover").click()
                await page.locator("#viewport-width").evaluate(
                    "(input) => { input.value = String(Number(input.value) + 1); input.dispatchEvent(new Event('change', { bubbles: true })); }"
                )
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                spoiler = page.locator(".attachment-inline").nth(2)
                assert await spoiler.locator(".spoiler-cover").count() == 0
                await spoiler.locator(".media-lightbox-trigger").click()
                await page.wait_for_function("() => document.querySelector('.media-lightbox')?.open")
                assert await lightbox.locator("img").evaluate(
                    "(image) => [image.naturalWidth, image.naturalHeight]"
                ) == [3, 5]
                await page.keyboard.press("Escape")
                assert external_requests == []
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_attachment_count_one_and_ten_remains_intrinsic(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    single = await alice.send(channel, "single image", attachments=[("one.png", png_bytes())])
    await alice.send(
        channel,
        "ten images",
        attachments=[(f"image-{index}.png", png_bytes()) for index in range(10)],
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert await page.locator(".message-attachments").get_attribute("data-image-count") == "10"
                assert await page.locator("img.attachment-image").count() == 10
                assert await page.locator("img.attachment-image").first.evaluate(
                    "(image) => [image.width, image.height]"
                ) == [2, 2]

                await page.locator("#message-picker").select_option(str(single.id))
                await page.wait_for_function(
                    "(id) => window.simcordPreview?.ready === true && window.simcordPreview.targetId === id",
                    arg=str(single.id),
                )
                assert await page.locator(".message-attachments").get_attribute("data-image-count") == "1"
                assert await page.locator("img.attachment-image").count() == 1
                await page.locator(".media-lightbox-trigger").click()
                lightbox = page.locator("dialog.media-lightbox")
                await page.wait_for_function("() => document.querySelector('.media-lightbox')?.open")
                assert await lightbox.locator("button[aria-label='Previous image']").is_hidden()
                assert await lightbox.locator("button[aria-label='Next image']").is_hidden()
                await page.keyboard.press("Escape")
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_renders_inline_code_and_markdown_breaks(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    await env.bot.get_channel(channel.id).send("before `code`\nafter")
    async with env.preview(channel, viewers=[alice]) as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert await page.locator("code.inline-code", has_text="code").count() == 1
                assert await page.locator(".message-content br").count() == 1
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
