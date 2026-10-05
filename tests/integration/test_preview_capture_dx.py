import asyncio
import io
import json
from pathlib import Path

import discord
import pytest
from preview_helpers import gif_bytes, png_bytes, target_message

import simcord
from simcord.backend.cdn import CDN_BASE


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
async def test_browser_adaptive_embed_composition_and_offline_provider_media(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    from fixtures.preview.catalog import close_payload, gallery_payload

    payload = gallery_payload("REF-10-LEGACY-EMBED")
    embed = payload.pop("embed")
    assert isinstance(embed, discord.Embed)
    embed.set_author(
        name="Reference Bot",
        url="https://profiles.example.test/reference",
        icon_url="https://assets.example.test/author.png",
    )
    embed.set_footer(
        text="Deterministic footer",
        icon_url="https://assets.example.test/footer.png",
    )
    for index in range(4):
        embed.add_field(name=f"Follow-up {index + 1}", value="Inline field", inline=True)

    external_url = "https://video.example.test/external.mp4"
    local_url = "https://video.example.test/offline.mp4"
    external = discord.Embed.from_dict(
        {
            "type": "video",
            "title": "External-only video",
            "provider": {"name": "External provider", "url": "https://provider.example.test/watch"},
            "video": {"url": external_url},
        }
    )
    offline = discord.Embed.from_dict(
        {
            "type": "video",
            "title": "Offline video",
            "provider": {"name": "Offline provider", "url": "https://provider.example.test/offline"},
            "video": {"url": local_url},
        }
    )
    payload["embeds"] = [embed, external, offline]
    message = await env.bot.get_channel(channel.id).send(**payload)
    media_path = Path(__file__).parents[1] / "fixtures" / "preview" / "video.mp4"
    supplied = {
        local_url: ("video.mp4", media_path.read_bytes()),
        "https://assets.example.test/author.png": ("author.png", png_bytes()),
        "https://assets.example.test/footer.png": ("footer.png", png_bytes()),
    }

    try:
        async with env.preview(channel, viewers=[alice], assets=supplied) as preview:
            await preview.show(message)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page()
                    await page.goto(preview.url)
                    await page.wait_for_function("() => window.simcordPreview?.ready === true")

                    cards = page.locator(".embed-card")
                    assert await cards.count() == 3
                    first = cards.nth(0)
                    rows = await first.locator(".embed-field-row").evaluate_all(
                        "rows => rows.map(row => [...row.children].map(field => {"
                        "const rect = field.getBoundingClientRect();"
                        "return {left: rect.left, top: rect.top, width: rect.width};"
                        "}))"
                    )
                    assert [len(row) for row in rows] == [3, 3, 1]
                    assert max(item["width"] for item in rows[0]) - min(item["width"] for item in rows[0]) < 1
                    block_width = await first.locator(".embed-fields > .embed-field").evaluate(
                        "field => field.getBoundingClientRect().width"
                    )
                    assert block_width > rows[0][0]["width"] * 2
                    assert await first.locator("img.embed-thumbnail").count() == 1
                    assert await first.locator("img.embed-image").count() == 1
                    assert await first.locator("img.embed-author-icon").count() == 1
                    assert await first.locator("img.embed-footer-icon").count() == 1
                    footer = first.locator(".embed-footer")
                    assert "Deterministic footer" in await footer.inner_text()
                    assert await footer.locator("time").get_attribute("datetime")

                    external_card = cards.nth(1)
                    assert await external_card.locator(".media-unavailable").count() == 1
                    assert (
                        await external_card.get_by_role("link", name="External provider").get_attribute(
                            "href"
                        )
                        == "https://provider.example.test/watch"
                    )
                    offline_card = cards.nth(2)
                    video = offline_card.locator("video.media-player-native")
                    assert await video.count() == 1
                    await offline_card.get_by_role("button", name="Play Embed 3 video").click()
                    await page.wait_for_function(
                        "() => !document.querySelector('.video-player video').paused"
                    )

                    status = await page.evaluate("() => window.simcordPreview")
                    assert any(item["code"] == "media-unavailable" for item in status["diagnostics"])
                    projected = target_message(preview._page_payload(preview._python))
                    assert projected is not None
                    assert projected["embeds"][0]["image"]["available"] is True
                    assert projected["embeds"][0]["thumbnail"]["available"] is True
                    assert projected["embeds"][0]["author"]["icon_available"] is True
                    assert projected["embeds"][0]["footer"]["icon_available"] is True
                    assert projected["embeds"][1]["type"] == "video"
                    assert projected["embeds"][1]["provider"]["url"] == "https://provider.example.test/watch"
                    assert projected["embeds"][1]["video"]["available"] is False
                    assert external_url not in str(projected["embeds"])
                    assert projected["embeds"][2]["video"]["available"] is True

                    await page.set_viewport_size({"width": 390, "height": 900})
                    await page.wait_for_function(
                        "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction "
                        "&& window.simcordPreview.presentation.host.width <= 390"
                    )
                    narrow = await first.evaluate(
                        "card => ({right: card.getBoundingClientRect().right, "
                        "rows: [...card.querySelectorAll('.embed-field-row')].map(row => "
                        "[...row.children].map(field => {const rect = field.getBoundingClientRect(); "
                        "return {left: rect.left, top: rect.top, width: rect.width};}))})"
                    )
                    assert narrow["right"] <= 390
                    for row in narrow["rows"]:
                        assert all(abs(field["left"] - row[0]["left"]) < 1 for field in row)
                        assert all(row[index]["top"] < row[index + 1]["top"] for index in range(len(row) - 1))
                finally:
                    await browser.close()
    finally:
        close_payload(payload)


@pytest.mark.asyncio
async def test_browser_suppressed_embed_keeps_message_and_its_attachment(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    embed = discord.Embed().set_image(url="attachment://suppressed.png")
    message = await env.bot.get_channel(channel.id).send(
        content="Visible while the embed is suppressed",
        embed=embed,
        file=discord.File(io.BytesIO(png_bytes()), filename="suppressed.png"),
    )
    env.backend.get_message(channel.id, message.id).flags |= 4

    async with env.preview(channel, viewers=[alice]) as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                projected = target_message(preview._page_payload(preview._python))
                assert projected is not None and projected["flags"] & 4
                assert await page.locator(".embed-card").count() == 0
                assert await page.locator(".message-content").inner_text() == (
                    "Visible while the embed is suppressed"
                )
                image = page.locator("img.attachment-image")
                assert await image.count() == 1
                assert await image.evaluate("(element) => element.complete && element.naturalWidth > 0")
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_image_viewer_zoom_scope_and_deleted_asset_cleanup(env, channel, alice):
    pytest.importorskip("playwright")
    pytest.importorskip("PIL")
    from PIL import Image
    from playwright.async_api import async_playwright

    output = io.BytesIO()
    Image.new("RGB", (1600, 1000), (20, 60, 90)).save(output, format="PNG")
    message = await alice.send(channel, "viewer", attachments=[("large.png", output.getvalue())])
    avatar_url = f"{CDN_BASE}/embed/avatars/{(alice.id >> 22) % 6}.png"
    async with (
        env.preview(channel, viewers=[alice], assets={avatar_url: ("avatar.png", png_bytes())}) as preview,
        async_playwright() as playwright,
    ):
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1000, "height": 700})
            await page.goto(preview.url)
            await page.wait_for_function("() => window.simcordPreview?.ready")
            await page.get_by_role("button", name="Refresh preview").click()
            await page.wait_for_function(
                "() => window.simcordPreview?.ready && !window.simcordPreview.pendingAction"
            )
            opener = page.get_by_role("button", name="Open large.png in image preview")
            await opener.click()
            viewer = page.get_by_role("dialog", name="Image preview")
            assert await viewer.locator(".media-lightbox-identity strong").inner_text() == alice.name
            await page.wait_for_function(
                "() => document.querySelector('.media-lightbox-avatar img')?.naturalWidth > 0",
                timeout=3000,
            )
            assert await viewer.bounding_box() == await page.locator("#preview-app").bounding_box()
            frame = viewer.get_by_role("region")
            await viewer.get_by_role("button", name="Zoom image").click()
            before = await frame.evaluate("element => element.scrollLeft")
            await page.keyboard.press("ArrowRight")
            assert await frame.evaluate("element => element.scrollLeft") > before
            await page.keyboard.press("ArrowDown")
            assert await frame.evaluate("element => element.scrollTop") > 0
            await viewer.get_by_role("button", name="Fit image").click()
            assert await frame.evaluate("element => element.scrollWidth === element.clientWidth")
            await page.keyboard.press("Escape")
            assert await opener.evaluate("element => element === document.activeElement")
            await opener.click()
            await page.wait_for_function(
                "() => document.querySelector('.media-lightbox-actions a')?.href.startsWith('blob:')"
            )
            await alice.delete(message)
            await preview.refresh()
            await page.wait_for_function("() => !document.querySelector('.media-lightbox').open")
            assert await page.locator(".media-lightbox-image").get_attribute("src") is None
            assert await page.locator(".media-lightbox a[href]").count() == 0
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_image_viewer_original_link_survives_pending_navigation(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    await alice.send(channel, "two images", attachments=[("a.png", png_bytes()), ("b.png", png_bytes())])
    release = asyncio.Event()

    async def delay_original(route):
        if route.request.url.endswith("?download=1"):
            await release.wait()
        await route.continue_()

    async with env.preview(channel, viewers=[alice]) as preview, async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        try:
            await page.route("**/api/assets/**", delay_original)
            await page.goto(preview.url)
            await page.wait_for_function("() => window.simcordPreview?.ready")
            await page.get_by_role("button", name="Open a.png in image preview").click()
            viewer = page.get_by_role("dialog", name="Image preview")
            await viewer.get_by_role("button", name="Next image").click()
            await viewer.get_by_role("button", name="Next image").click()
            release.set()
            original = viewer.get_by_role("link", name="Open original image")
            await original.first.wait_for()
            assert await viewer.locator(".media-lightbox-image").get_attribute("alt") == "a.png"
            assert await original.count() == 1
            assert (await original.get_attribute("href")).startswith("blob:")
        finally:
            release.set()
            await page.unroute_all(behavior="wait")
            await browser.close()


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

    spoiler_png = png((3, 5), (70, 80, 90))
    await alice.send(
        channel,
        "attachment media",
        attachments=[
            ("landscape.png", png((4, 2), (10, 20, 30))),
            ("portrait.png", png((2, 4), (40, 50, 60))),
            ("SPOILER_secret.png", spoiler_png),
            ("unsafe.html", b"<script>window.previewInjected = true</script>"),
            ("notes.txt", b"safe text"),
            ("SPOILER_secret.txt", b"safe spoiler text"),
            (
                "SPOILER_clip.mp4",
                (Path(__file__).parents[1] / "fixtures" / "preview" / "video.mp4").read_bytes(),
            ),
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

                images = page.locator("img.attachment-image")
                assert await images.count() == 3
                assert await images.nth(0).evaluate("(image) => [image.width, image.height]") == [4, 2]
                assert await images.nth(1).evaluate("(image) => [image.width, image.height]") == [2, 4]
                assert await page.locator(".spoiler-content button button").count() == 0
                video_spoiler = page.locator(".spoiler-media").filter(has=page.locator("video"))
                video = video_spoiler.locator("video")
                assert not await video.is_visible()
                await video_spoiler.locator(".spoiler-cover").click()
                assert await video.is_visible()

                unsafe = page.locator(".attachment:not(.attachment-inline)").filter(has_text="unsafe.html")
                assert await unsafe.count() == 1
                assert await unsafe.locator("script, iframe, object, embed").count() == 0
                assert await unsafe.locator(".attachment-preview").is_visible()
                assert await page.evaluate("() => window.previewInjected") is None
                assert (
                    await unsafe.locator(".attachment-preview").text_content()
                    == "<script>window.previewInjected = true</script>"
                )
                assert await page.get_by_role("button", name="Download unsafe.html").is_enabled()
                secret_file = page.locator(".attachment:not(.attachment-inline)").filter(
                    has_text="SPOILER_secret.txt"
                )
                assert await secret_file.locator(".spoiler-cover").count() == 1
                assert await secret_file.locator(".spoiler-content").count() == 1
                assert await secret_file.locator(".spoiler-content > :first-child").evaluate(
                    "(element) => element.inert && element.getAttribute('aria-hidden') === 'true'"
                )
                assert (
                    await secret_file.get_by_role("button", name="Download SPOILER_secret.txt").count() == 0
                )
                await secret_file.locator(".spoiler-cover").click()
                assert await secret_file.locator(".attachment-preview").text_content() == "safe spoiler text"
                assert await secret_file.get_by_role(
                    "button", name="Download SPOILER_secret.txt"
                ).is_visible()

                opener = page.locator(".attachment-inline .media-lightbox-trigger").nth(0)
                await opener.click()
                lightbox = page.locator("dialog.media-lightbox")
                await page.wait_for_function("() => document.querySelector('.media-lightbox')?.open")
                view = lightbox.locator(".media-lightbox-image")
                assert await view.evaluate("(image) => [image.naturalWidth, image.naturalHeight]") == [4, 2]
                await page.keyboard.press("ArrowRight")
                assert await view.evaluate("(image) => [image.naturalWidth, image.naturalHeight]") == [2, 4]
                await page.keyboard.press("ArrowRight")
                assert await view.evaluate("(image) => [image.naturalWidth, image.naturalHeight]") == [4, 2]
                await page.keyboard.press("Escape")
                assert await opener.evaluate("(element) => element === document.activeElement")

                spoiler = page.locator(".attachment-inline").nth(2)
                await spoiler.locator(".spoiler-cover").click()
                await page.set_viewport_size({"width": 900, "height": 700})
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                spoiler = page.locator(".attachment-inline").nth(2)
                assert await spoiler.locator(".spoiler-cover").count() == 0
                await spoiler.locator(".media-lightbox-trigger").click()
                await page.wait_for_function("() => document.querySelector('.media-lightbox')?.open")
                assert await lightbox.locator(".media-lightbox-image").evaluate(
                    "(image) => [image.naturalWidth, image.naturalHeight]"
                ) == [3, 5]
                await page.keyboard.press("Escape")
                image_download = page.get_by_role("button", name="Download SPOILER_secret.png")
                async with page.expect_download() as image_download_info:
                    await image_download.click()
                image_downloaded = await image_download_info.value
                assert await asyncio.to_thread(Path(await image_downloaded.path()).read_bytes) == spoiler_png
                assert external_requests == []
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_lightbox_excludes_media_inside_spoiler_container(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    view = discord.ui.LayoutView()
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://public.png")))
    view.add_item(
        discord.ui.Container(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://hidden.png")),
            spoiler=True,
        )
    )
    message = await env.bot.get_channel(channel.id).send(
        view=view,
        files=[
            discord.File(io.BytesIO(png_bytes()), filename="public.png"),
            discord.File(io.BytesIO(png_bytes()), filename="hidden.png"),
        ],
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert await page.locator(".spoiler-cover").is_visible()
                await page.locator(".component-gallery .media-lightbox-trigger").first.click()
                lightbox = page.locator("dialog.media-lightbox")
                await page.wait_for_function("() => document.querySelector('.media-lightbox')?.open")
                assert await lightbox.locator("button[aria-label='Next image']").is_hidden()
                assert await lightbox.locator("button[aria-label='Previous image']").is_hidden()
                assert await page.locator(".spoiler-cover").is_visible()
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_failed_spoiler_media_is_accessible_after_reveal(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    view = discord.ui.LayoutView()
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://broken.png", spoiler=True)))
    message = await env.bot.get_channel(channel.id).send(
        view=view,
        file=discord.File(io.BytesIO(png_bytes()), filename="broken.png"),
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.add_init_script(
                    "HTMLImageElement.prototype.decode = () => Promise.reject(new Error('decode failed'))"
                )
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                fallback = page.locator(".component-gallery .media-unavailable")
                assert await fallback.count() == 1
                assert await fallback.evaluate(
                    "(element) => element.inert && element.getAttribute('aria-hidden') === 'true'"
                )
                await page.locator(".component-gallery .spoiler-cover").click()
                assert await fallback.evaluate(
                    "(element) => !element.inert && !element.hasAttribute('aria-hidden')"
                )
            finally:
                await browser.close()


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

                await page.locator(f"#message-picker button[data-message-id='{single.id}']").click()
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
async def test_browser_v2_galleries_retain_intrinsic_ratios_and_reveal_spoilers(env, channel, alice):
    pytest.importorskip("playwright")
    pytest.importorskip("PIL")
    from PIL import Image
    from playwright.async_api import async_playwright

    sizes = [
        (120, 60),
        (60, 120),
        (96, 96),
        (150, 60),
        (60, 150),
        (100, 125),
        (150, 100),
        (64, 128),
        (128, 64),
    ]

    def png(size, color):
        output = io.BytesIO()
        Image.new("RGB", size, color).save(output, format="PNG")
        return output.getvalue()

    messages = []
    for count in (1, 3, 10):
        view = discord.ui.LayoutView()
        view.add_item(discord.ui.TextDisplay(f"Gallery with {count} item(s)"))
        uploads = {}
        items = []
        for index in range(count):
            source_index = 0 if count == 10 and index == 9 else index
            filename = f"gallery-{count}-{source_index}.png"
            if filename not in uploads:
                uploads[filename] = png(
                    sizes[source_index],
                    (source_index * 31 % 255, source_index * 53 % 255, source_index * 71 % 255),
                )
            items.append(
                discord.MediaGalleryItem(
                    f"attachment://{filename}",
                    description=f"alt-only-{count}-{index}",
                    spoiler=count == 10 and index == 1,
                )
            )
        view.add_item(discord.ui.MediaGallery(*items))
        if count == 10:
            view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay("Zero accent"),
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                    discord.ui.Section(
                        discord.ui.TextDisplay("Section text stays beside its accessory."),
                        accessory=discord.ui.Button(label="Section action", custom_id="section-action"),
                    ),
                    discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large),
                    accent_colour=discord.Colour(0),
                )
            )
            view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay("Nonzero accent"),
                    accent_colour=discord.Colour(0x123456),
                )
            )
            uploads["unreferenced-v2.txt"] = b"Not part of the component layout."

        files = [
            discord.File(io.BytesIO(content), filename=filename) for filename, content in uploads.items()
        ]
        message = await env.bot.get_channel(channel.id).send(view=view, files=files)
        messages.append((message, count))

    async with env.preview(channel, viewers=[alice]) as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.locator("#capture-open").click()
                await page.locator("#display-mode").select_option("fixed")
                await page.locator(".custom-dimensions > summary").click()
                await page.wait_for_function(
                    "() => !window.simcordPreview.pendingAction && window.simcordPreview.presentation.display === 'fixed'"
                )
                for message, count in messages:
                    await page.locator(f"#message-picker button[data-message-id='{message.id}']").click()
                    await page.wait_for_function(
                        "(id) => window.simcordPreview?.ready === true && window.simcordPreview.targetId === id",
                        arg=str(message.id),
                    )
                    gallery = page.locator(".component-gallery")
                    images = gallery.locator("img.gallery-image")
                    assert await images.count() == count
                    image_sizes = await images.evaluate_all(
                        """images => images.map(image => [
                            image.naturalWidth,
                            image.naturalHeight,
                            image.getBoundingClientRect().width,
                            image.getBoundingClientRect().height,
                        ])"""
                    )
                    for index, (width, height, rendered_width, rendered_height) in enumerate(image_sizes):
                        expected = sizes[0 if count == 10 and index == 9 else index]
                        assert (width, height) == expected
                        assert abs(rendered_width / rendered_height - width / height) < 0.03

                    if count == 10:
                        assert (
                            await page.locator(".message-content, .embed-card, .message-attachments").count()
                            == 0
                        )
                        assert await page.get_by_text("unreferenced-v2.txt").count() == 0
                        assert "alt-only-10-0" not in await page.locator("#focused-content").inner_text()
                        assert await images.first.get_attribute("alt") == "alt-only-10-0"
                        assert await images.nth(0).get_attribute("src") == await images.nth(9).get_attribute(
                            "src"
                        )
                        columns = await gallery.locator(".gallery-item").evaluate_all(
                            "items => items.map(item => Math.round(item.getBoundingClientRect().x))"
                        )
                        assert len(set(columns)) >= 2

                        containers = page.locator(".component-container")
                        assert (
                            await containers.nth(0).evaluate(
                                "element => getComputedStyle(element).borderLeftColor"
                            )
                            == "rgb(0, 0, 0)"
                        )
                        assert (
                            await containers.nth(1).evaluate(
                                "element => getComputedStyle(element).borderLeftColor"
                            )
                            == "rgb(18, 52, 86)"
                        )
                        assert await page.locator(".section-accessory button").count() == 1
                        separator_styles = await page.locator(".component-separator").evaluate_all(
                            """items => items.map(item => [
                                getComputedStyle(item).marginTop,
                                getComputedStyle(item).marginBottom,
                                getComputedStyle(item).borderTopColor,
                            ])"""
                        )
                        assert all(style[0] == style[1] for style in separator_styles)
                        assert float(separator_styles[1][0][:-2]) > float(separator_styles[0][0][:-2])
                        assert separator_styles[0][2] != "rgba(0, 0, 0, 0)"
                        assert separator_styles[1][2] == "rgba(0, 0, 0, 0)"

                        cover = page.locator(".component-gallery .spoiler-cover")
                        assert await cover.count() == 1
                        await cover.focus()
                        await page.keyboard.press("Enter")
                        assert await cover.count() == 0

                        await page.locator("#viewport-width").fill("320")
                        await page.locator("#viewport-width").dispatch_event("change")
                        await page.wait_for_function(
                            "(id) => window.simcordPreview?.ready === true && window.simcordPreview.targetId === id",
                            arg=str(message.id),
                        )
                        assert await page.locator(".spoiler-content.is-revealed").count() == 1
                        assert await gallery.evaluate(
                            "element => element.scrollWidth <= element.clientWidth + 1"
                        )
                        narrow_columns = await gallery.locator(".gallery-item").evaluate_all(
                            "items => items.map(item => Math.round(item.getBoundingClientRect().x))"
                        )
                        assert len(set(narrow_columns)) == 1
                    else:
                        assert (
                            await page.locator("#focused-content")
                            .get_by_text(f"Gallery with {count} item(s)")
                            .count()
                            == 1
                        )
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_video_seek_audio_pause_and_capture_time(env, channel, alice):
    from playwright.async_api import async_playwright

    fixtures = Path(__file__).parents[1] / "fixtures" / "preview"
    media_sources = {
        "still.png": png_bytes(),
        "video.mp4": (fixtures / "video.mp4").read_bytes(),
        "voice.ogg": (fixtures / "voice.ogg").read_bytes(),
    }
    message = await alice.send(
        channel,
        "local media",
        attachments=list(media_sources.items()),
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                external_requests = []
                page.on(
                    "request",
                    lambda request: (
                        external_requests.append(request.url)
                        if not request.url.startswith((preview._origin, "blob:"))
                        else None
                    ),
                )
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                video = page.locator("video.media-player-native")
                audio = page.locator("audio.media-player-native")
                assert await video.count() == await audio.count() == 1
                await page.get_by_role("button", name="Play video.mp4").click()
                await page.wait_for_function(
                    "() => !document.querySelector('video.media-player-native').paused"
                )
                await page.locator(".video-player .media-player-seek").evaluate(
                    "input => { input.value = '0.5'; input.dispatchEvent(new Event('input', { bubbles: true })); }"
                )
                await page.wait_for_function(
                    "() => document.querySelector('video.media-player-native').currentTime >= 0.4"
                )
                await page.get_by_role("button", name="Play voice.ogg").click()
                await page.wait_for_function(
                    "() => !document.querySelector('audio.media-player-native').paused"
                )
                await page.get_by_role("button", name="Pause voice.ogg").click()
                assert await audio.evaluate("element => element.paused")
                for filename, original in media_sources.items():
                    async with page.expect_download() as download_info:
                        await page.get_by_role("button", name=f"Download {filename}").click()
                    downloaded = await download_info.value
                    assert await asyncio.to_thread(Path(await downloaded.path()).read_bytes) == original
                assert external_requests == []
            finally:
                await browser.close()

        first = await preview.screenshot(media_time=0)
        second = await preview.screenshot(media_time=0.5)
        assert first.png != second.png
        assert first.media_metadata["captureTimes"] != second.media_metadata["captureTimes"]


@pytest.mark.asyncio
async def test_screenshot_captures_animated_custom_emoji_at_media_time(env, channel, alice):
    pytest.importorskip("playwright")
    emoji = env.guild.create_emoji("dancer", animated=True)
    original = gif_bytes()
    message = await alice.send(channel, f"<a:dancer:{emoji.id}>")
    url = f"{CDN_BASE}/emojis/{emoji.id}.gif"
    async with env.preview(channel, viewers=[alice], assets={url: ("dancer.gif", original)}) as preview:
        await preview.show(message)
        first = await preview.screenshot(media_time=0.1)
        second = await preview.screenshot(media_time=0.1)
        assert len(first.media_metadata["captureTimes"]) == 1
        asset_id = next(iter(first.media_metadata["captureTimes"]))
        assert first.complete is True and second.complete is True
        assert first.media_metadata["captureTimes"][asset_id] == 0.1
        assert first.media_metadata["assets"][asset_id]["effectiveMediaTime"] == 0.1
        assert first.png is not None and first.png == second.png


@pytest.mark.asyncio
async def test_screenshot_rejects_oversized_animated_emoji(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    emoji = env.guild.create_emoji("oversized", animated=True)
    gif = gif_bytes()
    original = gif + b"\0" * (10 * 1024 * 1024 + 1 - len(gif))
    message = await alice.send(channel, f"<a:oversized:{emoji.id}>")
    url = f"{CDN_BASE}/emojis/{emoji.id}.gif"
    async with env.preview(channel, viewers=[alice], assets={url: ("oversized.gif", original)}) as preview:
        await preview.show(message)
        capture = await preview.screenshot(tmp_path / "oversized.png", allow_incomplete=True, media_time=0.1)
        assert capture.complete is False
        assert capture.diagnostics


@pytest.mark.parametrize("in_point", [0, 10])
@pytest.mark.asyncio
async def test_browser_lottie_sticker_renders_offline_frames(env, channel, alice, monkeypatch, in_point):
    pytest.importorskip("playwright")
    from playwright.async_api import Page

    from simcord.backend.cdn import sticker_url

    sticker = env.guild.create_sticker("moving square", format_type=3)
    guild_sticker = await env.bot.get_guild(env.guild.id).fetch_sticker(sticker.id)
    message = await env.bot.get_channel(channel.id).send("sticker", stickers=[guild_sticker])
    composition = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "preview" / "moving-square.json").read_bytes()
    )
    composition["ip"] += in_point
    composition["op"] += in_point
    for layer in composition["layers"]:
        layer["ip"] += in_point
        layer["op"] += in_point
        for keyframe in layer["ks"]["p"]["k"]:
            keyframe["t"] += in_point
    source = json.dumps(composition).encode()
    original = Page.screenshot

    async def verify_frame(page, *args, **kwargs):
        media_time = await page.evaluate("() => window.simcordPreview.profile.mediaTime")
        expected_x, empty_x = (16, 48) if media_time == 0 else (48, 16)
        for x, expected in ((expected_x, [255, 0, 0, 255]), (empty_x, [0, 0, 0, 0])):
            pixel = await page.locator(".media-lottie canvas").evaluate(
                """(canvas, x) => {
                  const scale = Math.min(canvas.width, canvas.height) / 64;
                  const left = (canvas.width - scale * 64) / 2;
                  const top = (canvas.height - scale * 64) / 2;
                  return [...canvas.getContext('2d').getImageData(
                    Math.round(left + x * scale), Math.round(top + 32 * scale), 1, 1).data];
                }""",
                x,
            )
            assert pixel == expected
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", verify_frame)
    url = sticker_url(sticker.id, 3)
    async with env.preview(channel, viewers=[alice], assets={url: ("moving-square.json", source)}) as preview:
        await preview.show(message)
        first = await preview.screenshot(media_time=0)
        second = await preview.screenshot(media_time=1)
        assert first.complete and second.complete
        assert first.png != second.png
        assert set(first.media_metadata["captureTimes"].values()) == {0}
        assert set(second.media_metadata["captureTimes"].values()) == {1}


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
                await page.wait_for_function("() => !window.simcordPreview.pendingAction")
                prior_request = await page.evaluate("() => window.simcordPreview.lastAction?.requestId")
                await page.get_by_role("button", name="Ping").click()
                await page.wait_for_function(
                    "(prior) => window.simcordPreview.lastAction?.requestId !== prior "
                    "&& window.simcordPreview.lastAction?.dispatched && window.simcordPreview.pendingAction === null",
                    arg=prior_request,
                )
                prior_request = await page.evaluate("() => window.simcordPreview.lastAction.requestId")
                await page.get_by_role("button", name="Ping").click()
                await page.wait_for_function(
                    "(prior) => window.simcordPreview.lastAction?.requestId !== prior "
                    "&& window.simcordPreview.lastAction?.dispatched && window.simcordPreview.pendingAction === null",
                    arg=prior_request,
                )
                assert (
                    sum(message.content == "pong" for message in env.backend.messages[channel.id].values())
                    == 2
                )
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_select_navigation_retains_combobox_focus(env, channel, alice):
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
                trigger = page.locator(".select-trigger[role=combobox]")
                await trigger.focus()
                await trigger.press("ArrowDown")
                await page.wait_for_function(
                    "() => document.activeElement?.getAttribute('role') === 'combobox' && document.activeElement?.getAttribute('aria-expanded') === 'true'"
                )
                first = await trigger.get_attribute("aria-activedescendant")
                await trigger.press("ArrowDown")
                await page.wait_for_function(
                    """(first) => document.activeElement?.getAttribute("role") === "combobox"
                    && document.activeElement.getAttribute("aria-activedescendant") !== first""",
                    arg=first,
                )
                assert await page.locator(".select-trigger[aria-expanded=true]:focus").count() == 1
                assert (await page.evaluate("() => window.simcordPreview")).get("pendingAction") is None
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_browser_multi_select_pointer_keyboard_commit_and_escape(env, channel, alice):
    from playwright.async_api import async_playwright

    choices = discord.ui.Select(
        custom_id="multi-pick",
        min_values=1,
        max_values=2,
        options=[discord.SelectOption(label=name, value=name) for name in ("red", "blue", "green")],
    )
    received = []

    async def on_select(interaction):
        values = list(interaction.data["values"])
        received.append(values)
        await interaction.response.defer()

    choices.callback = on_select
    view = discord.ui.View(timeout=None)
    view.add_item(choices)
    message = await env.bot.get_channel(channel.id).send("Choose colors", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 390, "height": 360})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                trigger = page.locator(".select-trigger")
                await trigger.click()
                options = page.locator(".select-option")
                await options.filter(has_text="red").click()
                await options.filter(has_text="blue").click()
                assert received == []
                await trigger.click()
                await page.wait_for_function(
                    "() => window.simcordPreview?.lastAction?.settlement === 'settled' && !window.simcordPreview?.pendingAction"
                )
                assert received == [["red", "blue"]]

                prior_request = await page.evaluate("() => window.simcordPreview.lastAction.requestId")
                await trigger.focus()
                await trigger.press("ArrowDown")
                await trigger.press("Home")
                await trigger.press(" ")
                await trigger.press("Enter")
                await page.wait_for_function(
                    "(prior) => window.simcordPreview.lastAction?.requestId !== prior && !window.simcordPreview.pendingAction",
                    arg=prior_request,
                )
                assert received == [["red", "blue"], ["blue"]]

                await trigger.press("ArrowDown")
                await options.filter(has_text="green").click()
                await trigger.press("Escape")
                assert received == [["red", "blue"], ["blue"]]
                assert await trigger.get_attribute("aria-expanded") == "false"
                await trigger.press("ArrowDown")
                assert await trigger.get_attribute("aria-expanded") == "true"
                await trigger.press("Escape")

                await page.set_viewport_size({"width": 390, "height": 130})
                await trigger.click()
                assert await page.locator(".select-list").evaluate(
                    "element => element.matches(':popover-open')"
                )
                trigger_box = await trigger.bounding_box()
                popup_box = await page.locator(".select-list").bounding_box()
                assert trigger_box is not None and popup_box is not None
                assert popup_box["y"] >= 0
                assert popup_box["y"] + popup_box["height"] <= 130
            finally:
                await browser.close()


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
                assert await page.locator("#messages-toggle:focus").count() == 1
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


@pytest.mark.asyncio
async def test_browser_premium_buttons_use_supplied_metadata_and_never_dispatch(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    class ButtonView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.callback_count = 0

            async def count_callback(interaction):
                self.callback_count += 1
                await interaction.response.defer()

            action = discord.ui.Button(label="Action", custom_id="premium-action")
            action.callback = count_callback
            disabled = discord.ui.Button(label="Disabled", custom_id="disabled-action", disabled=True)
            disabled.callback = count_callback
            self.add_item(action)
            self.add_item(disabled)
            self.add_item(
                discord.ui.Button(
                    label="Disabled link",
                    style=discord.ButtonStyle.link,
                    url="https://example.test",
                    disabled=True,
                )
            )
            self.add_item(discord.ui.Button(sku_id=101))
            self.add_item(discord.ui.Button(sku_id=202))

    view = ButtonView()
    message = await env.bot.get_channel(channel.id).send(view=view)
    icon_url = "https://example.test/premium.png"
    presentations = {
        "101": {
            "name": "Provided plan",
            "price_text": "Provided price",
            "locale": "en-US",
            "icon_url": icon_url,
        }
    }
    async with env.preview(
        channel,
        viewers=[alice],
        assets={icon_url: ("premium.png", png_bytes())},
        sku_presentations=presentations,
    ) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => document.querySelector('.premium-button-icon')?.src.startsWith('blob:')"
                )
                assert await page.locator(".premium-button-name").inner_text() == "Provided plan"
                assert await page.locator(".premium-button-price").inner_text() == "Provided price"
                assert await page.evaluate(
                    "() => window.simcordPreview.diagnostics.some(item => "
                    "item.code === 'premium-sku-metadata-missing' && item.complete === false)"
                )
                link = page.locator("a.link-button.is-disabled")
                assert await link.get_attribute("href") is None
                original_url = page.url
                await link.click(force=True)
                await page.locator(".message-surface .component-row button:disabled").evaluate_all(
                    "(items) => items.forEach(item => item.click())"
                )
                await page.get_by_role("button", name="Provided plan Provided price").click()
                await page.get_by_role("button", name="Premium item details unavailable").click()
                await page.locator(".premium-button").last.focus()
                await page.keyboard.press("Enter")
                await page.wait_for_function(
                    "() => document.querySelector('#action-status').textContent.includes('No purchase was started')"
                )
                assert page.url == original_url
                assert view.callback_count == 0
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_button_focus_and_responsive_row_wrapping(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    static_emoji = env.guild.create_emoji("buttonmark")
    animated_emoji = env.guild.create_emoji("buttonmotion", animated=True)
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="A deliberately long action label", custom_id="wrap-0"))
    view.add_item(discord.ui.Button(emoji="🔥", custom_id="wrap-1"))
    view.add_item(discord.ui.Button(label="Unicode 👋", emoji="👋", custom_id="wrap-2"))
    view.add_item(
        discord.ui.Button(
            label="Static custom emoji",
            emoji=discord.PartialEmoji(name=static_emoji.name, id=static_emoji.id),
            custom_id="wrap-3",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Animated custom emoji",
            emoji=discord.PartialEmoji(name=animated_emoji.name, id=animated_emoji.id, animated=True),
            custom_id="wrap-4",
        )
    )
    assets = {
        f"{CDN_BASE}/emojis/{static_emoji.id}.png": ("buttonmark.png", png_bytes()),
        f"{CDN_BASE}/emojis/{animated_emoji.id}.gif": ("buttonmotion.gif", gif_bytes()),
    }
    message = await env.bot.get_channel(channel.id).send(view=view)
    async with env.preview(channel, viewers=[alice], width=320, assets=assets) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 360, "height": 720})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => { const images = [...document.querySelectorAll('.button-emoji img.custom-emoji')]; "
                    "return images.length === 2 && images.every(image => image.complete && image.naturalWidth > 0); }"
                )
                assert await page.locator(".component-button.is-icon-only .button-emoji").count() == 1
                await page.locator(".component-button.is-icon-only").first.evaluate("""button => {
                  const focusable = [...document.querySelectorAll(
                    'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]'
                  )].filter(element => element.checkVisibility());
                  focusable[focusable.indexOf(button) - 1]?.focus();
                }""")
                await page.keyboard.press("Tab")
                await page.wait_for_function(
                    "() => document.querySelector('.component-button:focus-visible') !== null"
                )
                outline = await page.locator(".component-button:focus-visible").evaluate(
                    "(button) => getComputedStyle(button).outlineWidth"
                )
                assert outline != "0px"
                rows = (
                    await page.locator(".component-row")
                    .first.locator(":scope > .component-button")
                    .evaluate_all(
                        "(buttons) => buttons.map(button => Math.round(button.getBoundingClientRect().top))"
                    )
                )
                assert len(set(rows)) > 1
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_responsive_message_column_reflows_long_bidi_text(env, channel, alice):
    from playwright.async_api import async_playwright

    text = "unbroken" * 26 + " العربية עברית mixed direction"
    message = await env.bot.get_channel(channel.id).send(text)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1360, "height": 1000})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.locator("#capture-open").click()
                await page.locator("#display-mode").select_option("fixed")
                await page.locator(".custom-dimensions > summary").click()
                await page.wait_for_function(
                    "() => !window.simcordPreview.pendingAction && window.simcordPreview.presentation.display === 'fixed'"
                )
                for width in (320, 360, 420, 640, 960, 1280):
                    for height in (700, 900):
                        await page.set_viewport_size({"width": width + 80, "height": height + 100})
                        await page.locator("#viewport-width").fill(str(width))
                        await page.locator("#viewport-width").dispatch_event("change")
                        await page.locator("#viewport-height").fill(str(height))
                        await page.locator("#viewport-height").dispatch_event("change")
                        await page.wait_for_function(
                            "([w, h]) => window.simcordPreview?.ready && "
                            "window.simcordPreview.profile.width === w && window.simcordPreview.profile.height === h",
                            arg=[width, height],
                        )
                        geometry = await page.evaluate(
                            """() => {
                              const app = document.querySelector('#preview-app').getBoundingClientRect();
                              const surface = document.querySelector('#focused-content').getBoundingClientRect();
                              const content = document.querySelector('.message-content').getBoundingClientRect();
                              return { app: app.width, surface: surface.width, text: content.width,
                                textRight: content.right, surfaceRight: surface.right,
                                overflow: document.documentElement.scrollWidth > innerWidth };
                            }"""
                        )
                        assert not geometry["overflow"], (width, height, geometry)
                        assert geometry["textRight"] <= geometry["surfaceRight"] + 1
                        assert geometry["text"] < geometry["surface"] <= geometry["app"] <= width
                await page.evaluate("() => { document.body.style.zoom = '200%'; }")
                assert await page.locator(".message-content").evaluate(
                    "element => element.scrollWidth <= element.clientWidth"
                )
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_capture_overrides_crop_visible_content_without_mutating_pages(env, channel, alice):
    pytest.importorskip("playwright")
    message = await env.bot.get_channel(channel.id).send("Reachable line\n" * 100)
    async with env.preview(channel, viewers=[alice], width=640, height=700) as preview:
        await preview.show(message)
        human = preview._open_page()
        before = (human.layout, human.display, human.width, human.height, human.target_id)
        capture = await preview.screenshot(
            target=message.id, viewport=(320, 240), layout="message", mode="surface"
        )
        assert capture.geometry["scope"] == "visible"
        assert capture.geometry["contentExtent"]["height"] > capture.output_height
        assert capture.geometry["overflow"]["vertical"] is True
        assert capture.output_width <= 320 and capture.output_height <= 240
        assert (human.layout, human.display, human.width, human.height, human.target_id) == before
        assert (preview.width, preview.height, preview._python.layout) == (640, 700, "message")
        viewport = await preview.screenshot(
            target=message.id, viewport=(100, 100), layout="channel", mode="viewport"
        )
        assert (viewport.output_width, viewport.output_height) == (100, 100)
        assert viewport.geometry["scope"] == "visible"
        assert viewport.png.startswith(b"\x89PNG")
        for invalid in ((True, 240), (320, 0), (32769, 240), (10000, 10000), [320, 240]):
            with pytest.raises(simcord.SetupError):
                await preview.screenshot(viewport=invalid)
        with pytest.raises(simcord.SetupError, match="layout"):
            await preview.screenshot(layout="document")


@pytest.mark.asyncio
async def test_browser_search_recovers_access_and_queues_close(env, channel, alice):
    from playwright.async_api import async_playwright

    source = await env.bot.get_channel(channel.id).send("Original focus")
    needle = await env.bot.get_channel(channel.id).send("Needle 👩🏽‍💻 ||private-spoiler||")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(source)
        async with async_playwright() as api:
            browser = await api.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 800, "height": 600})
                await page.goto(preview.url)
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                )
                await page.locator("#message-search").fill("Needle")
                await page.get_by_role("button", name="Search", exact=True).click()
                await page.wait_for_function(
                    "() => window.simcordPreview.navigation.query === 'needle' && !window.simcordPreview.pendingAction"
                )
                assert await page.evaluate("() => window.simcordPreview.targetId") == str(source.id)
                assert "private-spoiler" not in await page.locator("#message-picker").inner_text()
                await page.locator(f"#message-picker button[data-message-id='{needle.id}']").click()
                await page.wait_for_function(
                    "(id) => window.simcordPreview.targetId === id && !window.simcordPreview.pendingAction",
                    arg=str(needle.id),
                )

                member = env.bot.get_guild(env.guild.id).get_member(alice.id)
                await env.bot.get_channel(channel.id).set_permissions(member, view_channel=False)
                await env.settle()
                await page.wait_for_function("() => !window.simcordPreview.authorized")
                denied = await page.evaluate("() => window.simcordPreview")
                assert denied["lastAction"] is None and denied["activeControlKey"] is None
                assert denied["visibleMessageIds"] == [] and denied["selectDrafts"] == {}
                await env.bot.get_channel(channel.id).set_permissions(member, view_channel=True)
                await env.settle()
                await page.get_by_role("button", name="Refresh preview", exact=True).click()
                await page.wait_for_function(
                    "() => window.simcordPreview.authorized && window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                )
                await page.locator("#capture-open").click()
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                )

                started, release = asyncio.Event(), asyncio.Event()

                async def hold_presentation(route):
                    if route.request.post_data_json.get("kind") == "configure_presentation":
                        started.set()
                        await release.wait()
                    await route.continue_()

                await page.route("**/api/action", hold_presentation)
                await page.locator("#display-mode").select_option("fixed")
                await asyncio.wait_for(started.wait(), timeout=10)
                await page.locator(".session-menu > summary").click()
                await page.get_by_role("button", name="End preview session", exact=True).click()
                release.set()
                await asyncio.wait_for(preview.wait_closed(), timeout=10)
                await page.wait_for_function(
                    "() => !window.simcordPreview.authorized && !window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                )
                assert await page.locator("#focused-content").inner_text() == ""
            finally:
                await browser.close()


@pytest.mark.parametrize("surface", ["timezone", "animated"])
@pytest.mark.asyncio
async def test_modal_text_uses_profile_timezone_and_frozen_emoji(env, channel, alice, surface):
    pytest.importorskip("playwright")
    from PIL import Image
    from playwright.async_api import async_playwright

    emoji = env.guild.create_emoji("modal_dancer", animated=True)
    gif = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(
        gif,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), "blue")],
        duration=60_000,
        loop=0,
    )
    view = discord.ui.View(timeout=None)
    button = discord.ui.Button(label="Open media modal", custom_id="media-modal")

    async def open_modal(interaction):
        modal = discord.ui.Modal(title="Profile-aware modal")
        modal.add_item(discord.ui.TextDisplay(f"<t:1767225600:T> <a:modal_dancer:{emoji.id}>"))
        await interaction.response.send_modal(modal)

    button.callback = open_modal
    view.add_item(button)
    message = await env.bot.get_channel(channel.id).send(view=view)
    async with env.preview(
        channel,
        viewers=[alice],
        timezone="Pacific/Honolulu",
        assets={f"{CDN_BASE}/emojis/{emoji.id}.gif": ("modal.gif", gif.getvalue())},
    ) as preview:
        await preview.show(message)
        if surface == "timezone":
            async with async_playwright() as api:
                browser = await api.chromium.launch()
                try:
                    page = await browser.new_page()
                    await page.goto(preview.url)
                    await page.wait_for_function("() => window.simcordPreview?.ready === true")
                    await page.get_by_role("button", name="Open media modal", exact=True).click()
                    dialog = page.get_by_role("dialog", name="Profile-aware modal", exact=True)
                    await dialog.wait_for(state="visible")
                    assert await dialog.get_by_text("2:00:00 PM", exact=True).count() == 1
                finally:
                    await browser.close()
        else:
            opened = await alice.click(message)
            capture = await preview.screenshot(target=opened, media_time=60.1)
            assert capture.complete and capture.png is not None
            with Image.open(io.BytesIO(capture.png)).convert("RGB") as image:
                assert any(color == (0, 0, 255) for _, color in image.getcolors(image.width * image.height))


@pytest.mark.asyncio
async def test_channel_capture_focuses_old_target_without_moving_python_window(
    env, channel, alice, monkeypatch
):
    pytest.importorskip("playwright")
    from playwright.async_api import Page

    native = env.bot.get_channel(channel.id)
    oldest = await native.send("oldest requested capture")
    for index in range(54):
        await native.send(f"later message {index}")
    original = Page.screenshot

    async def verify_focus(page, *args, **kwargs):
        status = await page.evaluate("() => window.simcordPreview")
        assert str(oldest.id) in status["projectedMessageIds"]
        assert str(oldest.id) in status["visibleMessageIds"]
        assert (
            "oldest requested capture"
            in await page.locator(f"article[data-message-id='{oldest.id}']").inner_text()
        )
        return await original(page, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", verify_focus)
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        before = await preview.snapshot()
        for mode in ("surface", "viewport"):
            capture = await preview.screenshot(target=oldest, mode=mode)
            assert capture.complete and capture.ready
            assert capture.target_id == str(oldest.id)
            assert capture.png.startswith(b"\x89PNG")
        after = await preview.snapshot()
        assert after["targetId"] == before["targetId"] == str(channel.last_message.id)
        assert after["timeline"] == before["timeline"]


@pytest.mark.parametrize("mutation", ["delete_message", "remove_attachment", "remove_unretained_attachment"])
@pytest.mark.asyncio
async def test_channel_capture_revalidates_non_target_sources_before_install(
    tmp_path, env, channel, alice, monkeypatch, mutation
):
    pytest.importorskip("playwright")
    from playwright.async_api import Page

    native = env.bot.get_channel(channel.id)
    unretained = mutation == "remove_unretained_attachment"
    if unretained:
        monkeypatch.setattr(simcord.Preview, "_MAX_MEDIA_BYTES", 1)
    body = b"private attachment excerpt" if unretained else png_bytes()
    filename = "source.txt" if unretained else "source.png"
    source = await native.send("visible source", file=discord.File(io.BytesIO(body), filename))
    target = await native.send("capture target")
    original = Page.screenshot
    mutated = False

    async def revoke_source(page, *args, **kwargs):
        nonlocal mutated
        status = await page.evaluate("() => window.simcordPreview")
        assert str(source.id) in status["visibleMessageIds"]
        if unretained:
            assert "private attachment excerpt" in await page.locator("#channel-timeline").inner_text()
        png = await original(page, *args, **kwargs)
        if mutation == "delete_message":
            await source.delete()
        else:
            await source.edit(attachments=[])
        mutated = True
        return png

    destination = tmp_path / "capture.png"
    destination.write_bytes(b"previous capture")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        if unretained:
            baseline = await preview.screenshot(target=target, mode="viewport", allow_incomplete=True)
            assert baseline.ready and not baseline.complete and baseline.png.startswith(b"\x89PNG")
        monkeypatch.setattr(Page, "screenshot", revoke_source)
        with pytest.raises(simcord.SetupError):
            await preview.screenshot(destination, target=target, mode="viewport", allow_incomplete=True)
        assert destination.read_bytes() == b"previous capture"
        assert mutated


@pytest.mark.parametrize(
    ("kind", "mutation"),
    [("reply", "delete"), ("reply", "revoke"), ("system", "revoke"), ("context", "delete")],
)
@pytest.mark.asyncio
async def test_capture_revalidates_nested_message_sources_before_install(
    tmp_path, env, channel, alice, monkeypatch, kind, mutation
):
    from playwright.async_api import Page

    source_channel = env.guild.create_text_channel("reference") if mutation == "revoke" else channel
    source = await env.bot.get_channel(source_channel.id).send("private referenced excerpt")
    native = env.bot.get_channel(channel.id)
    if source_channel.id == channel.id:
        for index in range(51):
            await native.send(f"window filler {index}")
    if kind == "reply":
        target = await native.send("reply target", reference=source)
    elif kind == "system":
        from simcord.enums import MessageType

        target = env.guild.create_system_message(
            channel, MessageType.PINS_ADD, author=alice, referenced_message=source
        )
    else:

        @env.bot.tree.context_menu(name="Inspect reference")
        async def inspect_reference(interaction: discord.Interaction, message: discord.Message):
            await interaction.response.send_message("context target")

        await env.bot.tree.sync()
        target = (await alice.context_menu(channel, "Inspect reference", source)).response
    original = Page.screenshot
    mutated = False

    async def revoke_source(page, *args, **kwargs):
        nonlocal mutated
        status = await page.evaluate("() => window.simcordPreview")
        assert str(source.id) not in status["projectedMessageIds"]
        if kind == "reply":
            assert "private referenced excerpt" in await page.locator("#channel-timeline").inner_text()
        else:
            assert await page.locator(f"article a[href$='/{source.id}']").count() == 1
        png = await original(page, *args, **kwargs)
        if mutation == "delete":
            await source.delete()
        else:
            await env.bot.get_channel(source_channel.id).set_permissions(
                env.bot.get_guild(env.guild.id).get_member(alice.id), view_channel=False
            )
        mutated = True
        return png

    monkeypatch.setattr(Page, "screenshot", revoke_source)
    destination = tmp_path / "nested.png"
    destination.write_bytes(b"previous capture")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        with pytest.raises(simcord.SetupError):
            await preview.screenshot(destination, target=target, mode="viewport", allow_incomplete=True)
        assert mutated
        assert destination.read_bytes() == b"previous capture"
