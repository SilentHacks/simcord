import asyncio
import io

import discord
import pytest
from preview_helpers import png_bytes

from simcord.preview._capture import ManagedCapture

# Measure renderer-local coordinates, not workbench position or scroll offsets.
_LAYOUT = """() => {
  const app = document.querySelector('#preview-app');
  const origin = app.getBoundingClientRect();
  const selectors = ['.message-content', '.embed-card', '.embed-field'];
  return Object.fromEntries(selectors.map(selector => [selector,
    [...app.querySelectorAll(selector)].map(element => {
      const box = element.getBoundingClientRect();
      return [box.x - origin.x, box.y - origin.y, box.width, box.height];
    })]));
}"""


@pytest.mark.parametrize("width", [420, 640, 960])
@pytest.mark.asyncio
async def test_logical_profile_layout_is_independent_of_host_and_capture(
    env, channel, alice, monkeypatch, width
):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    embed = discord.Embed(title="Logical viewport", description="Stable renderer content")
    for name in ("First", "Second", "Third"):
        embed.add_field(name=name, value="Inline field", inline=True)
    message = await env.bot.get_channel(channel.id).send(
        "A logical message column",
        embed=embed,
        file=discord.File(io.BytesIO(png_bytes()), filename="profile.png"),
    )
    captured_layouts = []
    inspect_fonts = ManagedCapture._platform_fonts

    async def observe_capture(page):
        captured_layouts.append(await page.evaluate(_LAYOUT))
        return await inspect_fonts(page)

    monkeypatch.setattr(ManagedCapture, "_platform_fonts", staticmethod(observe_capture))
    async with env.preview(channel, viewers=[alice], width=width, height=700) as preview:
        await preview.show(message)
        async with async_playwright() as api:
            browser = await api.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                await page.locator("#capture-open").click()
                await page.locator("#display-mode").select_option("fixed")
                await page.wait_for_function(
                    "width => window.simcordPreview.ready && !window.simcordPreview.pendingAction "
                    "&& window.simcordPreview.presentation.display === 'fixed' "
                    "&& window.simcordPreview.profile.width === width",
                    arg=width,
                )
                await page.locator("#inspector-close").click()
                wide = await page.evaluate(_LAYOUT)
                wide_toolbar = await page.locator("#toolbar").evaluate("el => el.clientHeight")
                await page.get_by_role("button", name="Open profile.png in image preview").click()
                assert await page.locator(".media-lightbox-avatar").is_visible() == (width > 480)
                await page.get_by_role("button", name="Close image preview").click()
                await page.set_viewport_size({"width": 390, "height": 800})
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction "
                    "&& window.simcordPreview.presentation.host.width === 390"
                )
                narrow = await page.evaluate(_LAYOUT)
                assert narrow == wide
                assert await page.locator("#toolbar").evaluate("el => el.clientHeight") > wide_toolbar
                assert (
                    await page.locator("#messages-sidebar").evaluate("el => getComputedStyle(el).position")
                    == "fixed"
                )
                fields = narrow[".embed-field"]
                assert len(fields) == 3
                if width <= 480:
                    assert fields[0][1] < fields[1][1] < fields[2][1]
                else:
                    assert fields[0][1] == fields[1][1] == fields[2][1]
                capture = await preview.screenshot(mode="viewport", viewport=(width, 700))
                assert (capture.output_width, capture.output_height) == (width, 700)
                assert capture.geometry["logicalViewport"]["width"] == width
                assert captured_layouts == [wide]
            finally:
                await browser.close()


@pytest.mark.parametrize("intent", ["refresh", "focus", "viewer", "configure_presentation", "close"])
@pytest.mark.asyncio
async def test_page_intents_queue_until_current_action_is_admitted(env, channel, alice, intent):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    bob = env.guild.add_member(env.create_user("bob"))
    first = await env.bot.get_channel(channel.id).send("First queued target")
    second = await env.bot.get_channel(channel.id).send("Second queued target")
    async with env.preview(channel, viewers=[alice, bob], width=640, height=700) as preview:
        await preview.show(first)
        async with async_playwright() as api:
            browser = await api.chromium.launch()
            started, release = asyncio.Event(), asyncio.Event()
            submissions = []
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                await page.goto(preview.url)
                await page.wait_for_function(
                    "() => window.simcordPreview?.ready && !window.simcordPreview.pendingAction"
                )
                await page.locator("#capture-open").click()

                async def hold_first_presentation(route):
                    body = route.request.post_data_json
                    submissions.append(body)
                    if len(submissions) == 1:
                        started.set()
                        await release.wait()
                    await route.continue_()

                await page.route("**/api/action", hold_first_presentation)
                await page.locator("#display-mode").select_option("fixed")
                await asyncio.wait_for(started.wait(), timeout=10)
                first_sequence = submissions[0]["sequence"]
                if intent == "refresh":
                    await page.locator("#refresh").click()
                elif intent == "focus":
                    await page.locator(f"#message-picker button[data-message-id='{second.id}']").click()
                elif intent == "viewer":
                    await page.locator("#viewer-picker").select_option(str(bob.id))
                elif intent == "configure_presentation":
                    await page.locator("#layout-mode").select_option("channel")
                else:
                    await page.locator(".session-menu > summary").click()
                    await page.locator("#close").click()
                # Queuing never dispatches a second request concurrently or consumes a sequence.
                assert len(submissions) == 1
                assert submissions[0]["kind"] == "configure_presentation"
                release.set()
                if intent == "close":
                    await asyncio.wait_for(preview.wait_closed(), timeout=10)
                    await page.wait_for_function(
                        "() => !window.simcordPreview.authorized && !window.simcordPreview.pendingAction"
                    )
                else:
                    await page.wait_for_function(
                        "sequence => window.simcordPreview.ready && !window.simcordPreview.pendingAction "
                        "&& window.simcordPreview.lastAction?.sequence >= sequence",
                        arg=first_sequence + 1,
                    )
                    status = await page.evaluate("() => window.simcordPreview")
                    assert status["lastAction"]["rejected"] is False
                    if intent == "focus":
                        assert status["targetId"] == str(second.id)
                    elif intent == "viewer":
                        assert status["viewerId"] == str(bob.id)
                    elif intent == "configure_presentation":
                        assert status["presentation"]["layout"] == "channel"
                assert submissions[1]["kind"] == intent
                assert submissions[1]["sequence"] == first_sequence + 1
            finally:
                release.set()
                await browser.close()
