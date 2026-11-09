import discord
import pytest


@pytest.mark.asyncio
async def test_workbench_navigation_panels_and_logical_viewport(env, channel, alice):
    from playwright.async_api import async_playwright

    form_message = await env.bot.get_channel(channel.id).send("First navigation target")
    second = await env.bot.get_channel(channel.id).send("Second navigation target")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(form_message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1200, "height": 900})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                navigation = page.get_by_role("navigation", name="Authorized messages")
                first_row = navigation.locator(f"button[data-message-id='{form_message.id}']")
                second_row = navigation.locator(f"button[data-message-id='{second.id}']")
                assert await first_row.get_attribute("aria-current") == "page"
                await second_row.click()
                await page.wait_for_function(
                    "(id) => window.simcordPreview?.targetId === id && !window.simcordPreview.pendingAction",
                    arg=str(second.id),
                )
                assert await second_row.get_attribute("aria-current") == "page"
                assert (
                    await page.locator("#focused-content")
                    .get_by_text("Second navigation target", exact=True)
                    .is_visible()
                )

                await page.locator("#inspector-toggle").click()
                panel = page.locator("#inspector-panel")
                assert await panel.is_visible()
                assert await page.locator("#inspector-toggle").get_attribute("aria-expanded") == "true"
                assert await page.locator("#activity-panel").is_visible()
                await page.locator('[data-inspector-tab="diagnostics"]').click()
                assert await page.locator("#diagnostics-panel").is_visible()
                assert await page.locator("#activity-panel").is_hidden()
                assert await page.locator("#diagnostics-tab").get_attribute("aria-selected") == "true"
                await page.keyboard.press("ArrowRight")
                assert await page.locator("#capture-tab").get_attribute("aria-selected") == "true"
                await page.locator("#capture-open").click()
                assert await page.locator("#capture-panel").is_visible()
                assert await page.locator("#display-mode").is_visible()
                await page.locator("#display-mode").select_option("fixed")
                await page.locator("#viewport-preset").select_option("960x720")
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                    " && window.simcordPreview.presentation.display === 'fixed'"
                )
                assert await page.locator("#preview-stage").evaluate("""stage => {
                  const app = document.getElementById('preview-app');
                  stage.scrollLeft = 0;
                  const leftReachable = app.getBoundingClientRect().left >= stage.getBoundingClientRect().left;
                  stage.scrollLeft = stage.scrollWidth;
                  const rightReachable = app.getBoundingClientRect().right <= stage.getBoundingClientRect().right + 1;
                  return stage.clientWidth < app.clientWidth && leftReachable && rightReachable;
                }""")
                await page.locator(".custom-dimensions > summary").click()
                height_field = page.locator("#viewport-height")
                await height_field.fill("730")
                revision = await page.evaluate("() => window.simcordPreview.publishedRevision")
                await preview.refresh()
                await page.wait_for_function(
                    "revision => window.simcordPreview.ready && window.simcordPreview.publishedRevision > revision",
                    arg=revision,
                )
                assert await height_field.input_value() == "730"
                await height_field.dispatch_event("change")
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && window.simcordPreview.profile.height === 730"
                )
                await page.locator("#display-mode").select_option("responsive")
                await page.locator("#inspector-close").click()

                await page.set_viewport_size({"width": 500, "height": 760})
                await page.locator("#messages-toggle").click()
                assert await navigation.is_visible()
                assert await page.locator("#messages-toggle").get_attribute("aria-expanded") == "true"
                await page.keyboard.press("Escape")
                assert await page.locator("#messages-toggle").get_attribute("aria-expanded") == "false"
                assert await page.locator("#messages-toggle").evaluate(
                    "element => document.activeElement === element"
                )
                await page.locator("#capture-open").click()
                assert await page.locator("#capture-panel").is_visible()
                assert await page.locator("#preview-stage").evaluate("element => element.inert")
                await page.locator("#viewport-preset").select_option("640x700")
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                    " && window.simcordPreview.presentation.exactProfile.width === 640"
                )
                assert await page.locator("#preview-stage").evaluate("element => element.inert")
                await page.keyboard.press("Escape")

                await page.set_viewport_size({"width": 390, "height": 844})
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                    " && window.simcordPreview.presentation.host.width === 390"
                )
                assert await page.locator("#focused-content .message-avatar").evaluate("""avatar => {
                  const bounds = avatar.getBoundingClientRect();
                  const viewport = document.getElementById('preview-app').getBoundingClientRect();
                  return bounds.left >= viewport.left && bounds.right <= viewport.right;
                }""")
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_workbench_modal_isolates_chrome_and_restores_select_focus(env, channel, alice):
    from playwright.async_api import async_playwright

    class ChoiceForm(discord.ui.Modal, title="Choose options"):
        choices = discord.ui.Label(
            text="Choices",
            component=discord.ui.Select(
                custom_id="choices",
                options=[
                    discord.SelectOption(label="First", value="first"),
                    discord.SelectOption(label="Second", value="second"),
                ],
                min_values=0,
                max_values=2,
            ),
        )

    class FormView(discord.ui.View):
        @discord.ui.button(label="Open form", custom_id="open-form")
        async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.send_modal(ChoiceForm())

    form_message = await env.bot.get_channel(channel.id).send("First navigation target", view=FormView())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(form_message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1200, "height": 900})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                panel = page.locator("#inspector-panel")
                await page.get_by_role("button", name="Open form").click()
                await page.locator(".modal-dialog").wait_for()
                assert await page.locator("#toolbar").evaluate("element => element.inert")
                assert await page.locator("#messages-sidebar").evaluate("element => element.inert")
                assert await panel.evaluate("element => element.inert")
                assert await page.locator(".modal-dialog").evaluate("element => !element.closest('[inert]')")

                await page.locator(".select-trigger").click()
                helper = page.locator(".select-draft-actions")
                await helper.wait_for(state="visible")
                await page.get_by_role("option", name="Second", exact=True).click()
                await helper.locator(".select-cancel").focus()
                await page.keyboard.press("Enter")
                assert await page.evaluate(
                    "() => Object.values(window.simcordPreview.selectStates).every(item => item.values.length === 0)"
                )
                assert await page.locator(".modal-dialog").count() == 1
                assert await page.locator(".select-trigger").evaluate(
                    "element => document.activeElement === element"
                )
                await page.locator(".select-trigger").click()
                await page.get_by_role("option", name="Second", exact=True).click()
                await page.keyboard.press("Escape")
                assert await page.locator(".modal-dialog").count() == 1
                assert await page.evaluate("() => window.simcordPreview.renderState.openPopupKey === null")
                assert await page.locator(".select-trigger").evaluate(
                    "element => document.activeElement === element"
                )
                await page.locator(".select-trigger").click()
                await page.get_by_role("option", name="First", exact=True).click()
                await page.locator(".select-apply").focus()
                await page.keyboard.press("Enter")
                assert await page.locator(".select-trigger").evaluate(
                    "element => document.activeElement === element"
                )
                await page.keyboard.press("Escape")
                await page.locator(".modal-dialog").wait_for(state="detached")
                assert await page.locator("#toolbar").evaluate("element => !element.inert")
                assert await page.locator("#messages-sidebar").evaluate("element => !element.inert")
                assert await page.get_by_role("button", name="Open form").evaluate(
                    "element => document.activeElement === element"
                )
            finally:
                await browser.close()
