import io
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
                for message, count in messages:
                    await page.locator("#message-picker").select_option(str(message.id))
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
                        assert "alt-only-10-0" not in await page.locator("#message-surface").inner_text()
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
                            await page.locator("#message-surface")
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
    message = await alice.send(
        channel,
        "local media",
        attachments=[
            ("video.mp4", (fixtures / "video.mp4").read_bytes()),
            ("voice.ogg", (fixtures / "voice.ogg").read_bytes()),
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
            finally:
                await browser.close()

        first = await preview.screenshot(media_time=0)
        second = await preview.screenshot(media_time=0.5)
        assert first.png != second.png
        assert first.media_metadata["captureTimes"] != second.media_metadata["captureTimes"]


@pytest.mark.asyncio
async def test_browser_lottie_sticker_renders_offline_frames(env, channel, alice):
    from playwright.async_api import async_playwright

    from simcord.backend.cdn import sticker_url

    sticker = env.guild.create_sticker("moving square", format_type=3)
    guild_sticker = await env.bot.get_guild(env.guild.id).fetch_sticker(sticker.id)
    message = await env.bot.get_channel(channel.id).send("sticker", stickers=[guild_sticker])
    source = (Path(__file__).parents[1] / "fixtures" / "preview" / "moving-square.json").read_bytes()
    url = sticker_url(sticker.id, 3)
    async with env.preview(channel, viewers=[alice], assets={url: ("moving-square.json", source)}) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.locator(".media-lottie canvas").wait_for()
                assert await page.locator(".media-lottie canvas").evaluate(
                    "canvas => [...canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data].some((value, index) => index % 4 === 3 && value > 0)"
                )
                assert not await page.locator(".media-unavailable").count()
            finally:
                await browser.close()

        first = await preview.screenshot(media_time=0)
        second = await preview.screenshot(media_time=1)
        assert first.png != second.png
        assert first.media_metadata["captureTimes"] != second.media_metadata["captureTimes"]


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

                await trigger.focus()
                await trigger.press("ArrowDown")
                await trigger.press("Home")
                await trigger.press(" ")
                await trigger.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview?.lastAction?.sequence === 2 && !window.simcordPreview?.pendingAction"
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
                assert await page.evaluate("() => window.simcordPreview.lastAction") is None
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
                await page.locator("#close").focus()
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
