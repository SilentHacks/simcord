from __future__ import annotations

import pytest
from preview_helpers import action_body

import simcord


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

        await preview.show(history[0])
        focused = await preview.snapshot()
        assert focused["targetId"] == str(history[0].id)
        assert str(history[0].id) in focused["timeline"]

        bob_page = preview._open_page(bob.id, target_id=ephemeral.response.id)
        bob_snapshot = preview._page_payload(bob_page)
        ephemeral_projection = bob_snapshot["messages"][str(ephemeral.response.id)]
        assert ephemeral_projection["ephemeral"] is True
        assert ephemeral_projection["interaction_header"]["kind"] == "context_menu_command"
        assert ephemeral_projection["interaction_header"]["user"]["id"] == str(bob.id)


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
                await page.get_by_text("bot answer", exact=True).wait_for()
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
