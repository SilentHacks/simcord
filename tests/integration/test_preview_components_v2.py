import io

import discord
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count", [1, 3, 10], ids=["single", "three-ratios", "ten-with-duplicate-and-spoiler"]
)
async def test_gallery_intrinsic_geometry_and_reveal_survive_narrow_resize(env, channel, alice, count):
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
    files = [discord.File(io.BytesIO(content), filename=filename) for filename, content in uploads.items()]
    message = await env.bot.get_channel(channel.id).send(view=view, files=files)
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
                    assert "alt-only-10-0" not in await page.locator("#focused-content").inner_text()
                    assert await images.first.get_attribute("alt") == "alt-only-10-0"
                    assert await images.nth(0).get_attribute("src") == await images.nth(9).get_attribute(
                        "src"
                    )
                    columns = await gallery.locator(".gallery-item").evaluate_all(
                        "items => items.map(item => Math.round(item.getBoundingClientRect().x))"
                    )
                    assert len(set(columns)) >= 2
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
                    assert await gallery.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
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
async def test_v2_layout_excludes_legacy_attachments_and_preserves_container_styles(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright
    from preview_helpers import png_bytes

    view = discord.ui.LayoutView()
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://gallery.png")))
    uploads = {"gallery.png": png_bytes()}
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
    files = [discord.File(io.BytesIO(content), filename=filename) for filename, content in uploads.items()]
    await env.bot.get_channel(channel.id).send(view=view, files=files)
    async with env.preview(channel, viewers=[alice]) as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert await page.locator(".message-content, .embed-card, .message-attachments").count() == 0
                assert await page.get_by_text("unreferenced-v2.txt").count() == 0
                containers = page.locator(".component-container")
                assert (
                    await containers.nth(0).evaluate("element => getComputedStyle(element).borderLeftColor")
                    == "rgb(0, 0, 0)"
                )
                assert (
                    await containers.nth(1).evaluate("element => getComputedStyle(element).borderLeftColor")
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
            finally:
                await browser.close()
