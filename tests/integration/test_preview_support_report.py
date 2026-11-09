import asyncio
import json
from importlib import resources
from pathlib import Path

import discord
import jsonschema
import pytest


async def _support_report_world(env):
    guild_name = "private-report-guild-7f31"
    channel_name = "private-report-channel-7f31"
    viewer_name = "private-report-viewer-7f31"
    guild = env.create_guild(guild_name)
    report_channel = guild.create_text_channel(channel_name)
    viewer = guild.add_member(env.create_user(viewer_name))
    await env.settle()

    query_secret = "private-query-token-7f31"
    message_secret = "private-message-body-7f31"
    path_secret = "/private/case-files/7f31"
    url_secret = "private-url-token-7f31"
    cookie_secret = "private-cookie-session-7f31"
    csrf_secret = "private-cookie-csrf-7f31"
    private_url = f"https://files.example.test{path_secret}?access_token={url_secret}"
    message_content = (
        f"{query_secret} {message_secret} {private_url} Cookie: session={cookie_secret}; csrf={csrf_secret}"
    )
    composer_draft = "private-composer-draft-7f31"
    modal_title = "private-modal-title-7f31"
    modal_label = "private-modal-label-7f31"
    modal_placeholder = "private-modal-placeholder-7f31"
    modal_control_id = "private-modal-control-7f31"
    modal_draft = "private-modal-draft-7f31"
    button_label = "Open private form 7f31"
    button_control_id = "private-open-control-7f31"
    raw_exception = "private-callback-exception-7f31"
    callback_invocations = []

    class SensitiveModal(discord.ui.Modal, title=modal_title):
        private_value = discord.ui.TextInput(
            label=modal_label,
            custom_id=modal_control_id,
            placeholder=modal_placeholder,
        )

        async def on_submit(self, interaction):
            callback_invocations.append(interaction.id)
            raise RuntimeError(raw_exception)

    async def open_modal(interaction):
        await interaction.response.send_modal(SensitiveModal())

    view = discord.ui.View()
    button = discord.ui.Button(label=button_label, custom_id=button_control_id)
    button.callback = open_modal
    view.add_item(button)
    message = await env.bot.get_channel(report_channel.id).send(content=message_content, view=view)
    private = {
        "guild_name": guild_name,
        "channel_name": channel_name,
        "viewer_name": viewer_name,
        "query_secret": query_secret,
        "message_secret": message_secret,
        "path_secret": path_secret,
        "url_secret": url_secret,
        "cookie_secret": cookie_secret,
        "csrf_secret": csrf_secret,
        "private_url": private_url,
        "message_content": message_content,
        "composer_draft": composer_draft,
        "modal_title": modal_title,
        "modal_label": modal_label,
        "modal_placeholder": modal_placeholder,
        "modal_control_id": modal_control_id,
        "modal_draft": modal_draft,
        "button_label": button_label,
        "button_control_id": button_control_id,
        "raw_exception": raw_exception,
    }
    return guild, report_channel, viewer, message, callback_invocations, private


@pytest.mark.asyncio
async def test_downloaded_support_report_is_share_safe_after_real_error_and_recovery(env):
    from playwright.async_api import async_playwright

    guild, report_channel, viewer, message, callback_invocations, private = await _support_report_world(env)
    async with env.preview(report_channel, viewers=[viewer], layout="channel") as preview:
        await preview.show(message)
        runtime_version = (await preview.snapshot())["runtimeVersion"]
        error_cursor = env.error_cursor
        browser_state = {
            "action_request_ids": [],
            "callback_request_ids": [],
            "auth_headers": set(),
            "interrupt_reads": False,
            "failed_read": False,
            "injected": False,
            "injected_values": [],
        }

        async def capture_action(route):
            body = route.request.post_data_json
            if isinstance(body, dict):
                request_id = body.get("request_id")
                if isinstance(request_id, str):
                    browser_state["action_request_ids"].append(request_id)
                if body.get("kind") == "modal_submit" and isinstance(request_id, str):
                    browser_state["callback_request_ids"].append(request_id)
                for header in ("x-simcord-capability", "x-simcord-context"):
                    value = route.request.headers.get(header)
                    if value:
                        browser_state["auth_headers"].add(value)
            await route.continue_()

        async def interrupt_one_read(route):
            if browser_state["interrupt_reads"] and not browser_state["failed_read"]:
                browser_state["failed_read"] = True
                await route.fulfill(status=503, content_type="application/json", body='{"error":"temporary"}')
                return
            if browser_state["failed_read"]:
                response = await route.fetch()
                payload = await response.json()
                assert response.ok
                assert isinstance(payload.get("diagnostics"), list)
                for code in ("__proto__", "constructor"):
                    injected = {
                        "id": f"private-diagnostic-id-{code}-7f31",
                        "code": code,
                        "category": "rendering",
                        "severity": "critical",
                        "state": "current",
                        "complete": False,
                        "message": f"private-diagnostic-message-{code}-7f31",
                        "detail": f"private-diagnostic-detail-{code}-7f31",
                        "remediation": f"private-diagnostic-remediation-{code}-7f31",
                    }
                    payload["diagnostics"].append(injected)
                    browser_state["injected_values"].extend(
                        value
                        for key, value in injected.items()
                        if key not in {"code", "category", "severity", "state", "complete"}
                    )
                browser_state["injected"] = True
                await route.fulfill(response=response, json=payload)
                return
            await route.continue_()

        async def wait_for_receipt(page, request_id, settlement):
            await page.wait_for_function(
                """({requestId, settlement}) => {
                  const status = window.simcordPreview;
                  return status?.activity?.some(receipt => receipt.requestId === requestId
                    && receipt.settlement === settlement)
                    && !status.pendingAction;
                }""",
                arg={"requestId": request_id, "settlement": settlement},
            )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.route("**/api/action", capture_action)
                await page.route("**/api/state", interrupt_one_read)

                search = page.locator("#message-search")
                await search.fill(private["query_secret"])
                async with page.expect_request("**/api/action") as search_request_info:
                    await page.locator("#message-search-form").get_by_role("button", name="Search").click()
                search_request = await search_request_info.value
                await wait_for_receipt(page, search_request.post_data_json["request_id"], "settled")
                await page.wait_for_function(
                    "(query) => window.simcordPreview?.navigation?.query === query",
                    arg=private["query_secret"],
                )

                composer = page.locator("#channel-composer-input")
                await composer.fill(private["composer_draft"])
                assert await composer.input_value() == private["composer_draft"]
                await page.get_by_role("button", name=private["button_label"]).wait_for(state="visible")
                async with page.expect_request("**/api/action") as open_request_info:
                    await page.get_by_role("button", name=private["button_label"]).click()
                open_request = await open_request_info.value
                await wait_for_receipt(page, open_request.post_data_json["request_id"], "settled")

                modal_input = page.get_by_label(private["modal_label"])
                await modal_input.fill(private["modal_draft"])
                assert await modal_input.input_value() == private["modal_draft"]
                async with page.expect_request("**/api/action") as callback_request_info:
                    await page.get_by_role("button", name="Submit", exact=True).click()
                callback_request = await callback_request_info.value
                callback_request_id = callback_request.post_data_json["request_id"]
                await wait_for_receipt(page, callback_request_id, "failed")
                assert browser_state["callback_request_ids"] == [callback_request_id]
                assert len(callback_invocations) == 1
                errors = env.errors[error_cursor:]
                assert any(
                    isinstance(error, RuntimeError) and str(error) == private["raw_exception"]
                    for error in errors
                )
                # Arm only after the callback's receipt and post-action read settle.
                # A read started while the POST is pending is invalidated by endAction;
                # consuming the injected failure there does not reach the consumer.
                browser_state["interrupt_reads"] = True

                await page.wait_for_function(
                    """() => {
                      const status = window.simcordPreview;
                      const codes = status?.diagnostics?.map((item) => item.code) || [];
                      return status?.transport?.state === "recovered"
                        && status.transport.failures === 1
                        && status.transport.recoveries === 1
                        && codes.includes("action-callback-error")
                        && codes.includes("__proto__")
                        && codes.includes("constructor");
                    }"""
                )

                cancel = page.get_by_role("button", name="Cancel", exact=True)
                if await cancel.is_visible():
                    await cancel.click()
                await page.locator("#capture-open").click()
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                    " && window.simcordPreview.presentation.host.width === document.getElementById('preview-stage').clientWidth"
                )
                async with page.expect_download() as download_info:
                    await page.get_by_role("button", name="Download safe support report").click()
                download = await download_info.value
                download_path = await download.path()
                assert download_path is not None
                report_text = await asyncio.to_thread(Path(download_path).read_text)
                report = json.loads(report_text)

                schema = json.loads(
                    resources.files("simcord.preview").joinpath("protocol.schema.json").read_text()
                )
                report_schema = {
                    "$schema": schema["$schema"],
                    "$defs": schema["$defs"],
                    "$ref": "#/$defs/supportReport",
                }
                jsonschema.Draft202012Validator(report_schema).validate(report)

                status = await page.evaluate("() => window.simcordPreview")
                assert report["runtimeVersion"] == runtime_version
                assert report["protocolVersion"] == 3
                assert report["rendererVersion"] == "3"
                assert report["schemaVersion"] == 1
                assert report["publicationRevision"] == status["publishedRevision"]
                assert report["display"] == status["presentation"]["display"]
                assert report["dimensions"] == {
                    "viewport": status["presentation"]["viewport"],
                    "host": status["presentation"]["host"],
                }

                diagnostics = report["diagnostics"]
                callback_diagnostic = next(
                    item for item in diagnostics if item["code"] == "action-callback-error"
                )
                assert callback_diagnostic["severity"] == "error"
                assert callback_diagnostic["state"] == "current"
                transport_diagnostics = [
                    item
                    for item in diagnostics
                    if item["code"] in {"state-refresh-unavailable", "state-poll-unavailable"}
                ]
                assert len(transport_diagnostics) == 1
                assert transport_diagnostics[0]["severity"] == "warning"
                assert transport_diagnostics[0]["state"] == "recovered"
                generic_diagnostics = [item for item in diagnostics if item["code"] == "internal-error"]
                assert len(generic_diagnostics) >= 2
                assert all(item["severity"] == "warning" for item in generic_diagnostics)

                capability = await page.evaluate("() => location.hash.slice(1)")
                context_id = status["contextId"]
                assert capability
                assert browser_state["injected"]
                assert browser_state["auth_headers"]
                assert callback_request_id in browser_state["action_request_ids"]
                assert browser_state["action_request_ids"].count(callback_request_id) == 1
                private_values = {
                    *private.values(),
                    capability,
                    *browser_state["auth_headers"],
                    *browser_state["injected_values"],
                }
                assert all(value not in report_text for value in private_values)
                private_ids = {
                    str(guild.id),
                    str(report_channel.id),
                    str(viewer.id),
                    str(message.id),
                    context_id,
                    *browser_state["action_request_ids"],
                }
                assert all(json.dumps(value) not in report_text for value in private_ids)
            finally:
                await browser.close()
