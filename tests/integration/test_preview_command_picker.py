from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

_CASES = json.loads((Path(__file__).parents[1] / "fixtures/commands/option_cases.json").read_text())


def _start_playwright():
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    return async_playwright()


@pytest.mark.asyncio
async def test_browser_command_picker_runs_group_and_autocomplete_and_captures_states(
    env, channel, alice, monkeypatch
):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _start_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1000, "height": 760})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )

                composer = page.locator("#channel-composer-input")
                await composer.fill("/")
                await page.wait_for_function("() => window.simcordPreview.commandPicker.state === 'browsing'")
                await composer.press("ArrowDown")
                assert (await page.locator("#live-status").inner_text()).startswith("/")
                await composer.fill("/config s")
                await page.wait_for_function("() => window.simcordPreview.commandPicker.state === 'browsing'")
                assert await page.locator("#live-status").inner_text() == "1 commands"
                await page.screenshot(path="/tmp/simcord-picker-browsing.png")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                assert (await page.evaluate("() => window.simcordPreview.commandPicker.draft"))[
                    "invocation"
                ] == "config set"
                assert "/config set" in await page.locator("#live-status").inner_text()
                await page.screenshot(path="/tmp/simcord-picker-composing.png")

                await page.locator('.command-option-input[data-option="key"]').fill("theme")
                await page.locator('.command-option-input[data-option="value"]').fill("dark")
                state = await page.evaluate("() => window.simcordPreview.commandPicker")
                assert state["draft"]["submittable"] is True
                await page.screenshot(path="/tmp/simcord-picker-filled.png")
                await page.locator('.command-option-input[data-option="value"]').press("Enter")
                await page.wait_for_function("() => window.simcordPreview.commandPicker.draft === null")
                await page.locator("#channel-timeline").get_by_text("theme=dark", exact=True).wait_for()
                receipt = await page.evaluate(
                    "() => window.simcordPreview.activity.find(item => item.command?.invocation === 'config set')"
                )
                assert receipt["command"]["invocation"] == "config set"
                assert any(item["kind"] == "response" for item in receipt["outcomes"])

                await composer.fill("/tag")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                tag = page.locator('.command-option-input[data-option="name"]')
                await tag.fill("py")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.autocomplete.state === 'answered'"
                )
                await page.locator(".command-suggestion").first.wait_for(timeout=5000)
                await page.screenshot(path="/tmp/simcord-picker-autocomplete.png")
                assert await page.locator(".command-popup-heading").inner_text() == "OPTIONS MATCHING PY"
                await tag.press("Escape")
                await tag.press("Enter")
                await page.wait_for_function("() => window.simcordPreview.commandPicker.draft === null")
                await page.wait_for_function(
                    "() => document.querySelector('#channel-timeline')?.innerText.includes('Tag: py')"
                )

                await composer.fill("/picker_options")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                await page.locator('.command-option-input[data-option="text"]').fill("hello")
                await page.locator('.command-option-input[data-option="count"]').fill("2")
                await page.locator('.command-option-input[data-option="ratio"]').fill("0.5")
                assert (
                    await page.locator('.command-option-input[data-option="text"]').input_value() == "hello"
                )
                assert await page.locator('.command-option-input[data-option="count"]').input_value() == "2"
                assert await page.locator('.command-option-input[data-option="ratio"]').input_value() == "0.5"
                enabled = page.locator('.command-option-input[data-option="enabled"]')
                await enabled.focus()
                await page.screenshot(path="/tmp/simcord-picker-boolean.png")
                assert await page.locator(".command-popup").inner_text()
                assert "True" in await page.locator(".command-popup").inner_text()
                await page.locator(".command-suggestion").filter(has_text="True").click()
                user = page.locator('.command-option-input[data-option="user"]')
                await user.fill("ali")
                await page.wait_for_function(
                    "() => document.querySelector('.command-popup-heading')?.textContent === 'MEMBERS'"
                )
                await page.locator(".command-suggestion").first.wait_for(timeout=5000)
                await page.screenshot(path="/tmp/simcord-picker-members.png")
                assert await page.locator(".command-suggestion").count() >= 1
                await page.locator(".command-suggestion").first.click()
                await page.locator(".command-mention").wait_for()
                assert await page.locator(".command-mention").inner_text() == "@alice"
                await page.screenshot(path="/tmp/simcord-picker-mention.png")
                await page.get_by_role("button", name="Exit command mode").click()
                assert await composer.input_value() == "/picker_options"

                await composer.fill("/all-optional")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                assert await page.locator(".command-option-input").count() == 0
                assert await page.locator(".command-ghost").inner_text() == "+2 options"
                assert await page.locator(".command-popup-heading").inner_text() == "OPTIONS"
                assert await page.locator(".command-suggestion-ellipsis").count() == 2
                await page.screenshot(path="/tmp/simcord-picker-all-optional.png")
                await page.screenshot(path="/tmp/simcord-picker-options.png")
                await page.locator(".command-suggestion").filter(has_text="user").click()
                user_option = page.locator('.command-option-input[data-option="user"]')
                await user_option.wait_for()
                await user_option.fill("ali")
                await page.wait_for_function(
                    "() => document.querySelector('.command-popup-heading')?.textContent === 'MEMBERS' "
                    "&& document.querySelectorAll('.command-suggestion').length > 0"
                )
                assert await page.locator(".command-ghost").inner_text() == "+1 more"
                await page.screenshot(path="/tmp/simcord-picker-all-optional-added.png")
                await page.screenshot(path="/tmp/simcord-picker-members.png")
                await page.get_by_role("button", name="Remove user option").click()
                assert await page.locator('.command-option-input[data-option="user"]').count() == 0
                await page.get_by_role("button", name="Exit command mode").click()

                await composer.fill("/tag")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                tag = page.locator('.command-option-input[data-option="name"]')
                from simcord.preview import _actions

                original_autocomplete = _actions._autocomplete_result
                fake_interaction = env.backend.new_interaction(4, channel.id, alice.id, env.guild.id)

                async def unanswered(actor, command_channel, invocation, focused, value, options):
                    if value == "silent":
                        return SimpleNamespace(autocomplete_choices=None, _interaction=fake_interaction)
                    return await original_autocomplete(
                        actor, command_channel, invocation, focused, value, options
                    )

                monkeypatch.setattr(_actions, "_autocomplete_result", unanswered)
                await tag.fill("silent")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.autocomplete.state === 'failed'"
                )
                assert await page.locator(".command-empty").inner_text() == "Loading options failed"
                await tag.fill("empty")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.autocomplete.state === 'answered' "
                    "&& window.simcordPreview.commandPicker.autocomplete.choiceCount === 0"
                )
                assert await page.locator(".command-empty").inner_text() == "No options match"
                await page.get_by_role("button", name="Exit command mode").click()

                await composer.fill("/feedback")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                assert await page.locator(".command-option-input").count() == 0
                await page.screenshot(path="/tmp/simcord-picker-no-options.png")
                await page.locator(".command-chip").press("Enter")
                await page.get_by_role("dialog").wait_for()
                assert (await page.evaluate("() => window.simcordPreview.lastAction"))["command"][
                    "invocation"
                ] == "feedback"
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_validation_upload_viewer_privacy_and_narrow_composer(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    async with env.preview(channel, viewers=[alice, bob], layout="channel") as preview:
        async with _start_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1000, "height": 760})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )
                composer = page.locator("#channel-composer-input")

                await composer.fill("/option-check")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                limit = page.locator('.command-option-input[data-option="limit"]')
                label = page.locator('.command-option-input[data-option="label"]')
                await limit.press("Enter")
                assert await page.locator("#live-status").inner_text() == "2 required options missing"
                assert await limit.evaluate("element => element === document.activeElement")
                await limit.fill("0")
                await label.fill("x")
                await page.wait_for_function(
                    "() => document.querySelector('.command-pill[data-option=limit] input')?.getAttribute('aria-invalid') === 'true'"
                )
                await label.press("Enter")
                assert await limit.get_attribute("aria-invalid") == "true"
                describedby = await limit.get_attribute("aria-describedby")
                assert "command-context-description" in describedby
                assert "command-error-limit" in describedby
                assert (
                    await page.evaluate("() => window.simcordPreview.commandPicker.draft.submittable")
                    is False
                )
                assert await limit.evaluate("element => element === document.activeElement")
                await limit.fill("2")
                await label.fill("okay")
                await page.get_by_role("button", name="Send").click()
                await page.wait_for_function("() => window.simcordPreview.commandPicker.draft === null")
                await page.locator("#channel-timeline").get_by_text("okay:2", exact=True).wait_for()

                await composer.fill("/upload")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                await page.locator(".command-file-input").set_input_files(
                    {"name": "payload.txt", "mimeType": "text/plain", "buffer": b"hello"}
                )
                await page.screenshot(path="/tmp/simcord-picker-attachment.png")
                assert (
                    await page.evaluate("() => window.simcordPreview.commandPicker.draft.submittable") is True
                )
                await page.get_by_role("button", name="Send").click()
                await page.wait_for_function("() => window.simcordPreview.commandPicker.draft === null")
                await (
                    page.locator("#channel-timeline").get_by_text("payload.txt:hello", exact=True).wait_for()
                )

                await page.set_viewport_size({"width": 360, "height": 640})
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction "
                    "&& window.simcordPreview.geometry.host.width === 360"
                )
                await composer.fill("/picker_options")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                geometry = await page.locator(".command-row").evaluate(
                    "element => ({height: element.clientHeight, maxHeight: 160, width: element.clientWidth, scrollWidth: element.scrollWidth, tops: [...element.querySelectorAll('.command-pill')].map(item => item.offsetTop)})"
                )
                assert geometry["height"] <= 160
                assert geometry["scrollWidth"] <= geometry["width"]
                assert len(set(geometry["tops"])) > 1
                await page.locator(".command-chip").evaluate(
                    "element => { const row = element.closest('.command-row'); row.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true})); element.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, isComposing: true, keyCode: 229})); row.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true})); }"
                )
                assert await page.evaluate(
                    "() => window.simcordPreview.commandPicker.draft !== null && !window.simcordPreview.pendingAction"
                )
                await page.locator(".command-context-close").click()

                await composer.fill("/dm_greeting")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                await page.locator(".command-chip").press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.activity.some(item => item.command?.invocation === 'dm_greeting')"
                )
                await (
                    page.locator("#channel-timeline").get_by_text("Hello from the bot", exact=True).wait_for()
                )
                ephemeral_receipt = await page.evaluate(
                    "() => window.simcordPreview.activity.find(item => item.command?.invocation === 'dm_greeting')"
                )
                ephemeral_id = ephemeral_receipt["outcomes"][0]["messageId"]
                assert await page.locator(f'.channel-message[data-message-id="{ephemeral_id}"]').count() == 1
                await page.locator("#viewer-picker").select_option(label="bob")
                await page.wait_for_function(
                    f"() => window.simcordPreview.viewerId === '{bob.id}' && window.simcordPreview.ready"
                )
                assert "Hello from the bot" not in await page.locator("#channel-timeline").inner_text()
                assert await page.locator(f'.channel-message[data-message-id="{ephemeral_id}"]').count() == 0
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_autocomplete_busy_keeps_only_latest_query(env, channel, alice):
    await alice.slash(channel, "picker-busy-panel")

    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _start_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )

                composer = page.locator("#channel-composer-input")
                await composer.fill("/tag")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                await (
                    page.locator(".channel-message")
                    .filter(has_text="Busy control")
                    .get_by_role("button", name="Wait")
                    .click()
                )
                await page.wait_for_function("() => window.simcordPreview.pendingAction?.kind === 'click'")
                tag = page.locator('.command-option-input[data-option="name"]')
                await tag.fill("p")
                await tag.fill("py")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.autocomplete.state === 'loading'"
                )
                assert not await page.locator("#diagnostics").get_by_text("busy", exact=False).count()
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.autocomplete.state === 'answered' "
                    "&& window.simcordPreview.commandPicker.autocomplete.choiceCount === 2"
                )
                assert await page.locator(".command-suggestion").count() == 2
                assert not await page.locator("#diagnostics").get_by_text("busy", exact=False).count()
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_catalog_resync_preserves_rebuilds_and_invalidates_drafts(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    tree = env.bot.tree

    async def original(interaction: discord.Interaction, label: str, retired: str | None = None) -> None:
        await interaction.response.send_message(f"{label}:{retired}")

    def original_command():
        return app_commands.Command(
            name="picker-resync", description="Command catalog re-sync journey", callback=original
        )

    tree.add_command(original_command())
    await tree.sync()
    async with env.preview(channel, viewers=[alice, bob], layout="channel") as preview:
        async with _start_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )
                await page.wait_for_function(
                    "async () => { const status = window.simcordPreview; const response = await fetch('/api/commands', {headers: {'X-Simcord-Capability': location.hash.slice(1), 'X-Simcord-Context': status.contextId}}); const catalog = await response.json(); return catalog.entries.some(entry => entry.invocation === 'picker-resync'); }"
                )
                composer = page.locator("#channel-composer-input")
                await composer.fill("/picker-resync")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                await page.locator('.command-option-input[data-option="label"]').fill("keep")
                await page.locator(".command-ghost").click()
                await page.locator(".command-suggestion").filter(has_text="retired").click()
                retired = page.locator('.command-option-input[data-option="retired"]')
                await retired.fill("discard-me")
                original_state = await page.evaluate("() => window.simcordPreview.commandPicker.draft")
                original_fingerprint = original_state["schemaFingerprint"]
                original_revision = await page.evaluate("() => window.simcordPreview.publishedRevision")

                await tree.sync()
                await preview.refresh()
                await page.wait_for_function(
                    f"() => window.simcordPreview.publishedRevision > {original_revision}"
                )
                unchanged = await page.evaluate("() => window.simcordPreview.commandPicker.draft")
                assert unchanged["schemaFingerprint"] == original_fingerprint
                assert (
                    next(option for option in unchanged["options"] if option["name"] == "retired")["display"]
                    == "discard-me"
                )

                async def revised(
                    interaction: discord.Interaction,
                    label: app_commands.Range[str, 1, 10],
                    added: str | None = None,
                ) -> None:
                    await interaction.response.send_message(f"{label}:{added}")

                old_manifest = await page.evaluate(
                    "() => window.simcordPreview.commandPicker.catalog.fingerprint"
                )
                tree.remove_command("picker-resync")
                tree.add_command(
                    app_commands.Command(
                        name="picker-resync", description="Command catalog re-sync journey", callback=revised
                    )
                )
                await tree.sync()
                await preview.refresh()
                await page.wait_for_function(
                    f"() => window.simcordPreview.commandPicker.catalog.fingerprint !== '{old_manifest}' "
                    "&& window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )
                rebuilt = await page.evaluate("() => window.simcordPreview.commandPicker.draft")
                assert rebuilt["available"] is True
                assert rebuilt["droppedOptions"] == ["retired"]
                assert (
                    next(option for option in rebuilt["options"] if option["name"] == "label")["display"]
                    == "keep"
                )
                assert "retired" in await page.locator("#live-status").inner_text()

                tree.remove_command("picker-resync")
                await tree.sync()
                await preview.refresh()
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.draft?.available === false "
                    "&& window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )
                assert await page.get_by_role("alert").get_by_text("no longer available").count() == 1
                await page.get_by_role("button", name="Exit command mode").click()
                assert await composer.input_value() == "/picker-resync"

                await composer.fill("/tag")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                await page.locator('.command-option-input[data-option="name"]').fill("p")
                await page.locator("#viewer-picker").select_option(label="bob")
                await page.wait_for_function(
                    f"() => window.simcordPreview.viewerId === '{bob.id}' "
                    "&& window.simcordPreview.commandPicker.draft === null"
                )
                assert await composer.input_value() == ""
            finally:
                tree.remove_command("picker-resync")
                await tree.sync()
                await browser.close()


@pytest.mark.asyncio
async def test_browser_dm_user_command_suggests_and_runs(env, alice):
    await alice.send_dm("Open a bot DM for the picker")
    dm = alice.user.dm_channel
    async with env.preview(dm, viewers=[alice.user], layout="channel") as preview:
        async with _start_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )
                composer = page.locator("#channel-composer-input")
                await composer.fill("/dm-member")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                user = page.locator('.command-option-input[data-option="user"]')
                await user.fill("alice")
                await page.wait_for_function(
                    "() => document.querySelector('.command-popup-heading')?.textContent === 'MEMBERS'"
                )
                await page.locator(".command-suggestion").first.wait_for(timeout=5000)
                await page.locator(".command-suggestion").first.click()
                await page.locator(".command-mention").wait_for()
                await page.locator(".command-chip").press("Enter")
                await page.wait_for_function("() => window.simcordPreview.commandPicker.draft === null")
                await page.locator("#channel-timeline").get_by_text("Hello alice", exact=True).wait_for()
                assert (
                    await page.evaluate(
                        "() => window.simcordPreview.activity.find(item => item.command?.invocation === 'dm-member')"
                    )
                )["command"]["invocation"] == "dm-member"
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_oversized_attachments_are_rejected_without_run(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _start_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
                )
                run_requests = []

                def record_run_request(request):
                    if request.url.endswith("/api/action") and request.method == "POST":
                        body = request.post_data or ""
                        if '"kind":"run_command"' in body or '"kind": "run_command"' in body:
                            run_requests.append(body)

                page.on("request", record_run_request)
                composer = page.locator("#channel-composer-input")
                await composer.fill("/upload")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                oversized = {
                    "name": "large.txt",
                    "mimeType": "text/plain",
                    "buffer": b"x" * (10 * 1024 * 1024 + 1),
                }
                await page.locator(".command-file-input").set_input_files(oversized)
                file_input = page.locator(".command-file-input")
                assert await file_input.get_attribute("aria-invalid") == "true"
                assert "10 MiB" in await page.locator(".command-error").inner_text()
                await page.get_by_role("button", name="Send").click()
                assert "10 MiB" in await page.locator(".command-error").inner_text()
                assert run_requests == []
                assert await page.evaluate("() => window.simcordPreview.pendingAction") is None
                await page.get_by_role("button", name="Exit command mode").click()

                await composer.fill("/upload-bundle")
                await composer.press("Enter")
                await page.wait_for_function(
                    "() => window.simcordPreview.commandPicker.state === 'composing'"
                )
                await page.evaluate(
                    """() => {
                      const sizes = [9, 9, 9].map(value => value * 1024 * 1024);
                      for (let index = 0; index < sizes.length; index += 1) {
                        const input = document.querySelectorAll('.command-file-input')[index];
                        const file = new File([new Uint8Array(sizes[index])], `part-${index}.txt`, {type: 'text/plain'});
                        const transfer = new DataTransfer();
                        transfer.items.add(file);
                        Object.defineProperty(input, 'files', {configurable: true, value: transfer.files});
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                      }
                    }"""
                )
                await page.get_by_role("button", name="Send").click()
                await page.locator('.command-pill[data-option="third"] .command-error').wait_for()
                assert (
                    "25 MiB"
                    in await page.locator('.command-pill[data-option="third"] .command-error').inner_text()
                )
                assert run_requests == []
                assert await page.evaluate("() => window.simcordPreview.pendingAction") is None
                assert await page.evaluate("() => window.simcordPreview.commandPicker.draft !== null")
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_command_option_validator_matches_shared_cases(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _start_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                results = await page.evaluate(
                    """async (cases) => {
                      const { validateOptionInput } = await import('/commands.js');
                      return cases.map(({option, raw}) => {
                        const translated = {
                          ...option,
                          minLength: option.min_length,
                          maxLength: option.max_length,
                          minValue: option.min_value,
                          maxValue: option.max_value,
                        };
                        const result = validateOptionInput(translated, raw);
                        return result.code ? {code: result.code} : {value: result.value};
                      });
                    }""",
                    _CASES,
                )
                expected = [case["expect"] for case in _CASES]
                assert results == expected
            finally:
                await browser.close()
