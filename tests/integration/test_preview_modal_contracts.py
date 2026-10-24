import discord
import pytest
from preview_helpers import action_body, control_key


async def _profile_form_message(env, channel, received):
    class Form(discord.ui.Modal, title="Profile form"):
        name = discord.ui.Label(
            text="Name",
            description="Enter at least two characters.",
            component=discord.ui.TextInput(custom_id="name", min_length=2, max_length=30),
        )
        comment = discord.ui.Label(
            text="Comment",
            description="Optional notes.",
            component=discord.ui.TextInput(
                custom_id="comment",
                style=discord.TextStyle.paragraph,
                required=False,
                default="",
            ),
        )
        choice = discord.ui.Label(
            text="Choice",
            description="Choose an option.",
            component=discord.ui.Select(
                custom_id="choice",
                options=[
                    discord.SelectOption(label="One", value="one", default=True),
                    discord.SelectOption(label="Two", value="two"),
                ],
            ),
        )
        consent = discord.ui.Label(
            text="Consent",
            component=discord.ui.Checkbox(custom_id="consent", default=True),
        )
        files = discord.ui.Label(
            text="Files",
            description="Up to three files.",
            component=discord.ui.FileUpload(custom_id="files", required=False, min_values=0, max_values=3),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            uploads = []
            for file in self.files.component.values:
                uploads.append((file.filename, await file.read()))
            received.append(
                (
                    self.name.component.value,
                    self.comment.component.value,
                    self.choice.component.values,
                    self.consent.component.value,
                    uploads,
                )
            )
            await interaction.response.send_message("submitted")

    class FormView(discord.ui.View):
        @discord.ui.button(label="Open form", custom_id="open-form")
        async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.send_modal(Form())

    return await env.bot.get_channel(channel.id).send(content="forms", view=FormView())


@pytest.mark.asyncio
@pytest.mark.parametrize("height", [360, 700], ids=["short-modal-scroll", "tall-modal-scroll"])
async def test_modal_scroll_focus_trap_and_escape_restore_opener(env, channel, alice, height):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    received = []
    message = await _profile_form_message(env, channel, received)
    async with env.preview(channel, viewers=[alice], width=640, height=height, display="fixed") as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 800, "height": height + 100})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("button", name="Open form").click()
                await page.wait_for_selector(".modal-dialog")
                dialog_element = page.locator(".modal-dialog")
                assert await dialog_element.get_attribute("role") == "dialog"
                assert await dialog_element.get_attribute("aria-modal") == "true"
                dialog = await page.locator(".modal-dialog").bounding_box()
                assert dialog is not None and dialog["height"] <= min(height - 32, 640)
                body = page.locator(".modal-body")
                metrics = await body.evaluate(
                    "(element) => ({scrollHeight: element.scrollHeight, clientHeight: element.clientHeight})"
                )
                assert metrics["scrollHeight"] > metrics["clientHeight"]

                await body.evaluate("(element) => { element.scrollTop = element.scrollHeight; }")
                bottom = await body.evaluate("(element) => element.scrollTop")
                assert bottom > 0
                trigger = page.locator(".select-trigger")
                await trigger.click()
                assert await trigger.get_attribute("aria-expanded") == "true"
                before_escape = await body.evaluate("(element) => element.scrollTop")
                await trigger.press("Escape")
                assert await page.locator(".modal-dialog").count() == 1
                after_escape = await page.locator(".modal-body").evaluate("(element) => element.scrollTop")
                assert abs(after_escape - before_escape) <= 1
                await page.locator(".modal-body").evaluate("(element) => { element.scrollTop = 0; }")
                close = page.locator(".modal-close")
                submit = page.get_by_role("button", name="Submit")
                await close.focus()
                await page.keyboard.press("Shift+Tab")
                assert await submit.evaluate("(element) => document.activeElement === element")
                await page.keyboard.press("Tab")
                assert await close.evaluate("(element) => document.activeElement === element")
                await trigger.click()
                assert await trigger.get_attribute("aria-expanded") == "true"
                await page.get_by_role("button", name="Cancel").click()
                await page.wait_for_selector(".modal-dialog", state="detached")
                opener = page.get_by_role("button", name="Open form")
                assert await opener.evaluate("(element) => document.activeElement === element")
                await opener.click()
                await page.wait_for_selector(".modal-dialog")
                await page.locator(".modal-close").focus()
                await page.keyboard.press("Escape")
                await page.wait_for_selector(".modal-dialog", state="detached")
                assert await opener.evaluate("(element) => document.activeElement === element")
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_modal_validation_accessibility_and_default_submission(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    received = []
    message = await _profile_form_message(env, channel, received)
    async with env.preview(channel, viewers=[alice], width=640, height=700, display="fixed") as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 800, "height": 700 + 100})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("button", name="Open form").click()
                await page.wait_for_selector(".modal-dialog")
                await page.get_by_role("button", name="Submit").click()
                error = page.get_by_role("alert")
                await error.wait_for()
                name = page.locator('input[name="name"]')
                assert await name.get_attribute("aria-invalid") == "true"
                described_by = (await name.get_attribute("aria-describedby") or "").split()
                assert await error.get_attribute("id") in described_by
                assert await name.evaluate("(element) => document.activeElement === element")
                await name.fill("A")
                await page.get_by_role("button", name="Submit").click()
                error = page.get_by_role("alert")
                await error.wait_for()
                await name.fill("Ada")
                assert await page.get_by_role("alert").count() == 0
                await page.locator(".modal-choice input[type=checkbox]").uncheck()
                await page.get_by_role("button", name="Submit").click()
                await page.wait_for_function(
                    "() => !document.querySelector('.modal-dialog') "
                    "&& !window.simcordPreview?.pendingAction "
                    "&& window.simcordPreview?.lastAction?.settlement === 'settled'"
                )
                assert received == [("Ada", "", ["one"], False, [])]
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_modal_upload_limits_recover_to_real_file_submission(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    received = []
    message = await _profile_form_message(env, channel, received)
    async with env.preview(channel, viewers=[alice], width=640, height=700, display="fixed") as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 800, "height": 700 + 100})
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("button", name="Open form").click()
                await page.wait_for_selector(".modal-dialog")
                name = page.locator('input[name="name"]')
                await name.fill("Ada")
                await page.locator("input.upload-input").set_input_files(
                    {
                        "name": "oversized.bin",
                        "mimeType": "application/octet-stream",
                        "buffer": b"x" * (10 * 1024 * 1024 + 1),
                    }
                )
                await page.get_by_role("button", name="Submit").click()
                error = page.get_by_role("alert")
                await error.wait_for()
                assert "10 MiB" in await error.inner_text()
                file_input = page.locator("input.upload-input")
                assert await file_input.get_attribute("aria-invalid") == "true"
                file_error_ids = (await file_input.get_attribute("aria-describedby") or "").split()
                assert await error.get_attribute("id") in file_error_ids
                assert await file_input.evaluate("(element) => document.activeElement === element")
                assert await page.locator(".modal-body").evaluate("(element) => element.scrollTop") > 0
                assert received == []
                await page.get_by_role("button", name="Remove oversized.bin").click()
                await page.locator("input.upload-input").set_input_files(
                    [
                        {
                            "name": f"part-{index}.bin",
                            "mimeType": "application/octet-stream",
                            "buffer": bytes([index]) * (9 * 1024 * 1024),
                        }
                        for index in range(1, 4)
                    ]
                )
                await page.get_by_role("button", name="Submit").click()
                error = page.get_by_role("alert")
                await error.wait_for()
                assert "25 MiB" in await error.inner_text()
                assert received == []
                for index in range(1, 4):
                    await page.get_by_role("button", name=f"Remove part-{index}.bin").click()
                await page.locator("input.upload-input").set_input_files(
                    {"name": "real.txt", "mimeType": "text/plain", "buffer": b"browser bytes"}
                )
                await page.get_by_role("button", name="Submit").click()
                await page.wait_for_function(
                    "() => !document.querySelector('.modal-dialog') "
                    "&& !window.simcordPreview?.pendingAction "
                    "&& window.simcordPreview?.lastAction?.settlement === 'settled'"
                )
                assert received == [("Ada", "", ["one"], True, [("real.txt", b"browser bytes")])]
            finally:
                await browser.close()


class _UploadModal(discord.ui.Modal, title="Upload"):
    upload = discord.ui.Label(
        text="File",
        component=discord.ui.FileUpload(custom_id="upload", max_values=3),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("uploaded")


class _UploadView(discord.ui.View):
    @discord.ui.button(label="Upload", custom_id="open-upload")
    async def open_upload(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(_UploadModal())


@pytest.mark.asyncio
async def test_preview_modal_file_upload_validation_and_dispatch(env, channel, alice):
    message = await env.bot.get_channel(channel.id).send(content="upload", view=_UploadView())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        upload_key = control_key(preview._page_payload(page), "open-upload")
        opened = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="open-upload",
                control_key=upload_key,
                published_revision=page.revision,
            ),
        )
        assert opened["dispatched"] is True
        assert page.modal is not None
        modal = preview._page_payload(page)["modal"]["payload"]
        assert modal["application_identity"]["id"] == str(env.backend.bot_user.id)
        assert modal["application_name"] == modal["application_identity"]["name"]

        invalid = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="bad-upload",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"upload": "not-a-file-list"},
            ),
        )
        assert invalid["dispatched"] is False
        aggregate = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="aggregate-upload",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={
                    "upload": [
                        ["one.bin", b"a" * (9 * 1024 * 1024)],
                        ["two.bin", b"b" * (9 * 1024 * 1024)],
                        ["three.bin", b"c" * (9 * 1024 * 1024)],
                    ]
                },
            ),
        )
        assert aggregate["dispatched"] is False
        oversized = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="oversized-upload",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"upload": [["oversized.bin", b"x" * (10 * 1024 * 1024 + 1)]]},
            ),
        )
        assert oversized["dispatched"] is False

        submitted = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="good-upload",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"upload": [["report.txt", b"contents"]]},
            ),
        )
        assert submitted["dispatched"] is True
        assert channel.last_message.content == "uploaded"
