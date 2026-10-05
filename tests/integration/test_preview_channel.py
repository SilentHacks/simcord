from __future__ import annotations

import asyncio
import json
from importlib import resources
from urllib.parse import urlsplit

import discord
import jsonschema
import pytest
from preview_helpers import action_body

import simcord


@pytest.mark.asyncio
async def test_dm_and_guild_channel_recipients_validate_against_schema(env, channel, alice):
    await alice.send_dm("hello")
    dm = alice.user.dm_channel
    schema = json.loads(resources.files("simcord.preview").joinpath("protocol.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)

    async with env.preview(dm, viewers=[alice.user], layout="channel") as preview:
        dm_snapshot = await preview.snapshot()
        recipient = dm_snapshot["channel"]["recipient"]
        assert recipient["id"] == str(env.backend.bot_user.id)
        assert recipient["name"]
        validator.validate(dm_snapshot)

    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        guild_snapshot = await preview.snapshot()
        assert guild_snapshot["channel"]["recipient"] is None
        validator.validate(guild_snapshot)


@pytest.mark.asyncio
async def test_dm_composer_label_and_hash_change_load_second_preview(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    await alice.send_dm("hello")
    dm = alice.user.dm_channel
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            async with env.preview(dm, viewers=[alice.user], layout="channel") as first:
                first_url = first.url
                port = urlsplit(first_url).port
                await page.goto(first_url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                first_context_id = await page.evaluate("() => window.simcordPreview.contextId")
                placeholder = await page.locator("#channel-composer-input").get_attribute("placeholder")
                assert placeholder is not None and placeholder.startswith("Message @")

            async with env.preview(channel, viewers=[alice], layout="channel", port=port) as second:
                second_url = second.url
                assert (
                    urlsplit(first_url)._replace(fragment="").geturl()
                    == urlsplit(second_url)._replace(fragment="").geturl()
                )
                await page.evaluate(
                    "(fragment) => { window.location.hash = fragment; }", urlsplit(second_url).fragment
                )
                await page.wait_for_function(
                    "(previous) => window.simcordPreview?.ready && window.simcordPreview.contextId !== previous",
                    arg=first_context_id,
                )
                second_context_id = await page.evaluate("() => window.simcordPreview.contextId")
                assert second_context_id in second._pages
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_channel_layout_authorizes_window_and_reanchors_targets(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    bot_channel = env.bot.get_channel(channel.id)
    compact_first = await bot_channel.send("grouped first")
    compact_next = await bot_channel.send("grouped next")
    boundary_before = await bot_channel.send("before midnight")
    boundary_after = await bot_channel.send("after midnight")
    edited = await bot_channel.send("before edit")
    await edited.edit(content="edited message")
    starter = await bot_channel.send("thread starter")
    await starter.create_thread(name="discussion")
    history = [await bot_channel.send(f"history {index}") for index in range(55)]
    ephemeral = await bob.context_menu(channel, "Report Member", alice)

    stored_first = env.backend.get_message(channel.id, compact_first.id)
    stored_next = env.backend.get_message(channel.id, compact_next.id)
    stored_first.timestamp = stored_next.timestamp = "2030-01-01T12:00:00+00:00"
    env.backend.get_message(channel.id, boundary_before.id).timestamp = "2030-01-01T23:59:00+00:00"
    env.backend.get_message(channel.id, boundary_after.id).timestamp = "2030-01-02T00:01:00+00:00"

    with pytest.raises(simcord.SetupError, match="layout"):
        env.preview(channel, viewers=[alice], layout="server")

    async with env.preview(channel, viewers=[alice, bob], layout="channel") as preview:
        snapshot = await preview.snapshot()
        assert snapshot["layout"] == "channel"
        assert len(snapshot["timeline"]) == 50
        assert snapshot["history"]["hasBefore"] is True
        assert snapshot["history"]["hasAfter"] is False
        assert str(ephemeral.response.id) not in {message["id"] for message in snapshot["messageIndex"]}
        assert str(ephemeral.response.id) not in snapshot["messages"]

        page = preview._python
        result = await preview._action(
            "python",
            action_body(page, "history", 1, direction="older"),
        )
        assert result["settlement"] == "settled", result
        older = preview._page_payload(page)
        assert len(older["timeline"]) <= 50
        assert older["history"]["hasBefore"] is False
        assert older["history"]["hasAfter"] is True
        assert str(ephemeral.response.id) not in older["messages"]
        assert older["messages"][str(compact_next.id)]["compact"] is True
        assert older["messages"][str(edited.id)]["edited_timestamp"] is not None
        assert older["messages"][str(starter.id)]["thread"]["name"] == "discussion"

        await preview.show(history[20])
        focused = await preview.snapshot()
        assert focused["targetId"] == str(history[20].id)
        assert focused["timeline"][24] == str(history[20].id)
        assert focused["timeline"][-1] == str(history[45].id)
        assert focused["history"]["hasBefore"] is True
        assert focused["history"]["hasAfter"] is True

        bob_page = preview._open_page(bob.id, target_id=ephemeral.response.id)
        bob_snapshot = preview._page_payload(bob_page)
        ephemeral_projection = bob_snapshot["messages"][str(ephemeral.response.id)]
        assert ephemeral_projection["ephemeral"] is True
        assert ephemeral_projection["interaction_header"]["kind"] == "context_menu_command"
        assert ephemeral_projection["interaction_header"]["user"]["id"] == str(bob.id)


@pytest.mark.asyncio
async def test_channel_controls_dispatch_to_their_own_message(env, channel, alice):
    from playwright.async_api import async_playwright

    first = (await alice.slash(channel, "panel")).response.message
    second = (await alice.slash(channel, "panel")).response.message
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        await preview.show(second)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                first_panel = page.locator(".channel-message").filter(has_text="Panel").first
                assert await first_panel.get_attribute("data-message-id") == str(first.id)
                await first_panel.get_by_role("button", name="Ping").click()
                await page.wait_for_function(
                    "() => window.simcordPreview?.lastAction?.settlement === 'settled'"
                    " && !window.simcordPreview.pendingAction"
                )
                assert (await page.evaluate("() => window.simcordPreview.lastAction"))["dispatched"]
                assert channel.last_message.content == "pong"
                release = asyncio.Event()

                async def pause_action(route):
                    await release.wait()
                    await route.continue_()

                await page.route("**/api/action", pause_action)
                await page.get_by_role("textbox", name="Message").fill("private draft")
                await page.get_by_role("button", name="Send").click()
                await page.wait_for_function("() => Boolean(window.simcordPreview?.pendingAction)")
                pending = await page.evaluate("() => window.simcordPreview.pendingAction")
                assert "private draft" not in str(pending)
                assert await first_panel.get_by_role("button", name="Ping").is_disabled()
                await first_panel.get_by_role("button", name="Ping").focus()
                assert await first_panel.get_by_role("button", name="Ping").evaluate(
                    "button => button === document.activeElement"
                )
                release.set()
                await page.wait_for_function(
                    "() => !window.simcordPreview?.pendingAction && !window.simcordPreview?.awaitingRevision"
                )
                assert await first_panel.get_by_role("button", name="Ping").is_enabled()
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_preview_workbench_navigation_panels_and_modal_isolation(env, channel, alice):
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

                await page.set_viewport_size({"width": 1200, "height": 900})
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                first_row = page.locator(f"#message-picker button[data-message-id='{form_message.id}']")
                await first_row.click()
                await page.wait_for_function(
                    "(id) => window.simcordPreview?.targetId === id && !window.simcordPreview.pendingAction",
                    arg=str(form_message.id),
                )
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
async def test_channel_select_draft_cancel_and_apply_dispatch_real_callback(env, channel, alice):
    from playwright.async_api import async_playwright

    class Choices(discord.ui.View):
        @discord.ui.select(
            custom_id="choices",
            options=[discord.SelectOption(label="First"), discord.SelectOption(label="Second")],
            min_values=0,
            max_values=2,
        )
        async def choose(self, interaction, select):
            await interaction.response.send_message(f"Selected: {', '.join(select.values)}")

    message = await env.bot.get_channel(channel.id).send("Choose a destination", view=Choices())
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                trigger = page.get_by_role("combobox", name="Select one or more options")
                await trigger.click()
                await page.get_by_role("option", name="First", exact=True).click()
                await page.locator(".select-cancel").click()
                assert await trigger.get_attribute("aria-expanded") == "false"
                assert await page.evaluate(
                    "() => Object.values(window.simcordPreview.selectStates).every(item => item.values.length === 0)"
                )
                assert channel.last_message.id == message.id
                await trigger.click()
                await page.get_by_role("option", name="Second", exact=True).click()
                await page.locator(".select-apply").click()
                await (
                    page.locator("#channel-message-list")
                    .get_by_text("Selected: Second", exact=True)
                    .wait_for()
                )
                assert channel.last_message.content == "Selected: Second"
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_composer_enter_preserves_multiline_and_ime_input(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    await env.bot.get_channel(channel.id).send("Start")
    async with (
        env.preview(channel, viewers=[alice], layout="channel") as preview,
        async_playwright() as playwright,
    ):
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(preview.url)
            await page.wait_for_function("() => window.simcordPreview?.ready")
            composer = page.get_by_role("textbox", name="Message")
            await composer.fill("first")
            await composer.press("Shift+Enter")
            await page.keyboard.type("second")
            expected = "first\nsecond"
            assert await composer.input_value() == expected
            before = channel.last_message.id
            await composer.dispatch_event("keydown", {"key": "Enter", "isComposing": True, "keyCode": 229})
            assert channel.last_message.id == before
            assert await composer.input_value() == expected
            await composer.press("Enter")
            await page.wait_for_function(
                "() => window.simcordPreview?.lastAction?.dispatch === 'dispatched' && !window.simcordPreview.pendingAction"
            )
            assert channel.last_message.content == expected
            assert channel.last_message.author.id == alice.id
            assert await composer.input_value() == ""
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_channel_composer_sends_replies_and_preserves_live_dom_state(env, channel, alice):
    from playwright.async_api import async_playwright

    bot_channel = env.bot.get_channel(channel.id)
    for index in range(16):
        content = f"history {index}\nsecond line\nthird line"
        if index == 15:
            content += " ||reveal me||"
        await bot_channel.send(content)

    received: list[int] = []

    async def reply_to_preview(message):
        if message.author.id == alice.id and message.content == "from-preview":
            received.append(message.id)
            await bot_channel.send("bot answer", reference=message)

    env.bot.add_listener(reply_to_preview, "on_message")

    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert await page.locator("#channel-layout").is_visible()
                assert await page.locator("#diagnostics").evaluate(
                    "element => !document.getElementById('preview-app').contains(element)"
                )

                spoiler = page.locator(".markdown-spoiler").last
                await spoiler.click()
                timeline = page.locator("#channel-timeline")
                before = await timeline.evaluate(
                    "element => { element.scrollTop = 100; return element.scrollTop; }"
                )
                composer = page.get_by_role("textbox", name="Message")
                await composer.fill("preserve draft")
                await composer.evaluate("element => { element.setSelectionRange(3, 3); element.focus(); }")
                revision = await page.evaluate("() => window.simcordPreview.publishedRevision")

                await bot_channel.send("outside current window")
                await preview.refresh()
                await page.wait_for_function(
                    "revision => window.simcordPreview?.publishedRevision > revision",
                    arg=revision,
                )
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                state = await page.evaluate(
                    """() => ({
                      draft: document.getElementById('channel-composer-input').value,
                      start: document.getElementById('channel-composer-input').selectionStart,
                      scrollTop: document.getElementById('channel-timeline').scrollTop,
                      revealed: document.querySelector('.markdown-spoiler')?.classList.contains('is-revealed'),
                    })"""
                )
                assert state["draft"] == "preserve draft"
                assert state["start"] == 3
                assert abs(state["scrollTop"] - before) <= 2
                await composer.fill("retain after denial")
                member = env.bot.get_guild(env.guild.id).get_member(alice.id)
                await bot_channel.set_permissions(member, send_messages=False)
                await preview.refresh()
                await page.wait_for_function("() => document.getElementById('channel-composer').hidden")
                assert await page.locator("#channel-composer-input").input_value() == "retain after denial"
                denied_revision = await page.evaluate("() => window.simcordPreview?.publishedRevision")
                await bot_channel.set_permissions(member, send_messages=True)
                await preview.refresh()
                await page.wait_for_function(
                    "revision => window.simcordPreview?.publishedRevision > revision && window.simcordPreview?.ready",
                    arg=denied_revision,
                )
                assert await composer.input_value() == "retain after denial"
                assert state["revealed"] is True

                prior_sequence = await page.evaluate("() => window.simcordPreview?.lastAction?.sequence")
                await composer.fill("from-preview")
                await page.get_by_role("button", name="Send").click()
                await page.wait_for_function(
                    "sequence => window.simcordPreview?.lastAction?.sequence > sequence && !window.simcordPreview?.pendingAction",
                    arg=prior_sequence,
                )
                assert await composer.input_value() == "", await page.evaluate("() => window.simcordPreview")
                await page.locator("#channel-message-list").get_by_text("bot answer", exact=True).wait_for()
                assert received
                bot_message = page.locator(".channel-message").filter(has_text="bot answer").last
                await bot_message.get_by_role("button", name="Reply").click()
                assert await page.locator("#reply-context").is_visible()
                await composer.fill("follow-up")
                await page.get_by_role("button", name="Send").click()
                follow_up = page.locator(".channel-message").filter(has_text="follow-up").last
                await follow_up.locator(".message-reply").wait_for()
                assert "bot answer" in await follow_up.locator(".message-reply").inner_text()
            finally:
                await browser.close()
        finally:
            await playwright.stop()


@pytest.mark.asyncio
async def test_dropdown_scroll_reaches_last_option(env, channel, alice):
    from playwright.async_api import async_playwright

    class Choices(discord.ui.View):
        @discord.ui.select(options=[discord.SelectOption(label=f"Choice {index}") for index in range(25)])
        async def choose(self, interaction, select):
            await interaction.response.send_message(select.values[0])

    message = await env.bot.get_channel(channel.id).send("Long choices", view=Choices())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                await page.locator(".select-trigger").click()
                popup = page.locator(".select-list")
                bottom = await popup.evaluate(
                    "element => { element.scrollTop = element.scrollHeight; return element.scrollTop; }"
                )
                await page.evaluate(
                    "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                )
                assert await popup.evaluate("element => element.scrollTop") == bottom
                await page.get_by_role("option", name="Choice 24", exact=True).click()
                await page.wait_for_function(
                    "() => !window.simcordPreview.pendingAction && window.simcordPreview.lastAction?.settlement === 'settled'"
                )
                assert channel.last_message.content == "Choice 24"
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_search_target_stays_visible_and_keyboard_row_keeps_focus(env, channel, alice):
    from playwright.async_api import async_playwright

    bot_channel = env.bot.get_channel(channel.id)
    target = await bot_channel.send("Unique old target")
    for index in range(55):
        await bot_channel.send(f"History {index}\n" + "A tall history line\n" * 8)
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                await page.locator("#message-search").fill("Unique old target")
                await page.locator("#message-search").press("Enter")
                row = page.locator(f".message-row[data-message-id='{target.id}']")
                await row.wait_for()
                await page.wait_for_function("() => !window.simcordPreview.pendingAction")
                await row.focus()
                await row.press("Enter")
                await page.wait_for_function(
                    "id => window.simcordPreview.targetId === id && window.simcordPreview.ready && !window.simcordPreview.pendingAction",
                    arg=str(target.id),
                )
                assert (
                    await page.evaluate("() => document.activeElement.dataset.controlKey")
                    == f"message:{target.id}"
                )
                bounds = await page.locator(f".channel-message[data-message-id='{target.id}']").evaluate(
                    """element => {
                        const target = element.getBoundingClientRect();
                        const timeline = document.getElementById('channel-timeline').getBoundingClientRect();
                        return {top: target.top, bottom: target.bottom, visibleTop: timeline.top, visibleBottom: timeline.bottom};
                    }"""
                )
                assert bounds["visibleTop"] <= bounds["top"] < bounds["visibleBottom"]
                assert bounds["bottom"] <= bounds["visibleBottom"] + 1
            finally:
                await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_draft", ["", "Keep this next draft"])
async def test_lost_send_receipt_clears_only_confirmed_sent_draft(env, channel, alice, newer_draft):
    from playwright.async_api import async_playwright

    await env.bot.get_channel(channel.id).send("Conversation")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                submissions = []
                delivered = asyncio.Event()
                release = asyncio.Event()

                async def lose_send_receipt(route):
                    if route.request.post_data_json.get("kind") == "send_message":
                        submissions.append(route.request.post_data_json["request_id"])
                        await route.fetch()
                        delivered.set()
                        await release.wait()
                        await route.abort("failed")
                    else:
                        await route.continue_()

                await page.route("**/api/action", lose_send_receipt)
                composer = page.get_by_role("textbox", name="Message")
                await composer.fill("Send exactly once")
                await page.get_by_role("button", name="Send").click()
                await delivered.wait()
                await (
                    page.get_by_label("Preview viewport")
                    .get_by_text("Send exactly once", exact=True)
                    .wait_for()
                )
                if newer_draft:
                    await composer.fill(newer_draft)
                release.set()
                await page.wait_for_function(
                    "id => window.simcordPreview?.lastAction?.requestId === id && window.simcordPreview.lastAction.settlement === 'settled' && !window.simcordPreview.pendingAction",
                    arg=submissions[0],
                )
                assert channel.last_message.content == "Send exactly once"
                assert await composer.input_value() == newer_draft
                assert len(submissions) == 1
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_channel_focus_preserves_message_geometry_and_group_spacing(env, alice):
    from playwright.async_api import async_playwright

    channel = env.guild.create_text_channel("general", topic="Long channel context. " * 40)
    first = await alice.send(channel, "Wrapping stays stable when selecting this message. " * 12)
    await alice.send(channel, "A continuation by the same author.")
    await env.bot.get_channel(channel.id).send("A new author group.")
    await env.bot.get_channel(channel.id).send("🙂")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                geometry = """() => [...document.querySelectorAll('.channel-message')].map(element => {
                    const bounds = node => {
                        if (!node) return null;
                        const {x, y, width, height} = node.getBoundingClientRect();
                        return {x, y, width, height};
                    };
                    return {
                        message: bounds(element),
                        content: bounds(element.querySelector('.message-content')),
                        avatar: bounds(element.querySelector('.message-avatar')),
                    };
                })"""
                before = await page.evaluate(geometry)
                continuation_gap = before[1]["message"]["y"] - (
                    before[0]["message"]["y"] + before[0]["message"]["height"]
                )
                author_gap = before[2]["message"]["y"] - (
                    before[1]["message"]["y"] + before[1]["message"]["height"]
                )
                assert author_gap > continuation_gap
                assert len({item["content"]["x"] for item in before}) == 1
                assert await page.locator(".channel-heading h1").evaluate(
                    "heading => heading.scrollWidth <= heading.clientWidth"
                )
                await page.locator(f".message-row[data-message-id='{first.id}']").click()
                await page.wait_for_function(
                    "id => window.simcordPreview.targetId === id && window.simcordPreview.ready",
                    arg=str(first.id),
                )
                assert await page.evaluate(geometry) == before
            finally:
                await browser.close()
