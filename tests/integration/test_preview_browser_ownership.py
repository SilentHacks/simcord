"""Controlled-delivery checks for browser-owned reads and retained surfaces."""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

import discord
import pytest
from discord import app_commands
from preview_helpers import png_bytes

from simcord.preview._diagnostics import make_diagnostic


@asynccontextmanager
async def _browser(preview, *, clock=False):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1100, "height": 800})
            if clock:
                await page.clock.install()
            await page.goto(preview.url)
            await page.wait_for_function("() => window.simcordPreview?.ready")
            yield page
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_browser_state_owner_coalesces_cancels_and_accepts_security_updates(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview:
        async with _browser(preview) as page:
            result = await page.evaluate("""async () => {
              const {createSession} = await import('/session.js');
              const reads = [], installed = [];
              const session = createSession({
                read: signal => new Promise(resolve => reads.push({signal, resolve})),
                install: async value => {
                  if (!session.accept(value)) return false;
                  installed.push(value.secret); return true;
                }, tick() {}, failure(error) { throw error; }, interval: 60000,
              });
              const snapshot = (generation, revision, secret) => ({context:{id:'one', generation},
                botGeneration:1, publishedRevision:revision, secret});
              const old = session.readState();
              const coalesced = old === session.readState();
              session.invalidate();
              const current = session.readState();
              reads[1].resolve(snapshot(2, 3, 'current')); await current;
              reads[0].resolve(snapshot(1, 2, 'private')); await old;
              const redacted = session.readState();
              reads[2].resolve(snapshot(2, 3, null)); await redacted;
              const rolledBack = session.accept(snapshot(1, 4, 'private'));
              session.resume(); session.resume();
              const oneChain = reads.length === 4;
              session.pause(); session.resume();
              reads[3].resolve(snapshot(2, 3, 'obsolete lifetime'));
              await Promise.resolve();
              reads[4].resolve(snapshot(2, 3, null));
              await Promise.resolve(); await Promise.resolve();
              session.pause();
              session.beginAction();
              const admitted = session.readState();
              reads[5].resolve(snapshot(2, 4, 'admitted')); await admitted;
              session.endAction();
              session.beginAction(true);
              const oldContext = session.readState();
              reads[6].resolve(snapshot(2, 4, 'private'));
              const oldContextBlocked = !await oldContext;
              session.endAction();
              const uncertainContext = session.readState();
              reads[7].resolve(snapshot(2, 4, 'private'));
              const uncertainContextBlocked = !await uncertainContext;
              session.beginAction(true); session.endAction(true);
              const rejectedContext = session.readState();
              reads[8].resolve(snapshot(2, 4, 'rejection recovery')); await rejectedContext;
              session.stop();
              return {coalesced, aborted: reads[0].signal.aborted, installed, rolledBack, oneChain,
                oldContextBlocked, uncertainContextBlocked};
            }""")
            assert result == {
                "coalesced": True,
                "aborted": True,
                "installed": ["current", None, None, "admitted", "rejection recovery"],
                "rolledBack": False,
                "oneChain": True,
                "oldContextBlocked": True,
                "uncertainContextBlocked": True,
            }


@pytest.mark.asyncio
async def test_browser_revocation_tears_down_modal_without_new_revision(env, channel, alice):
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        await preview.show(feedback)
        async with _browser(preview) as page:
            await page.locator(".modal-dialog").wait_for()
            await page.locator(".modal-dialog input, .modal-dialog textarea").first.fill("private draft")
            revision = await page.evaluate("window.simcordPreview.publishedRevision")
            member = env.bot.get_guild(env.guild.id).get_member(alice.id)
            await env.bot.get_channel(channel.id).set_permissions(member, view_channel=False)
            await page.wait_for_function("() => window.simcordPreview.authorized === false")
            assert await page.locator(".modal-dialog").count() == 0
            assert "private draft" not in await page.locator("body").inner_text()
            assert await page.evaluate("window.simcordPreview.publishedRevision") == revision
            assert await page.evaluate("window.simcordPreview.visibleMessageIds") == []
            assert not await page.locator("#channel-layout").evaluate("element => element.inert")
            await page.evaluate(
                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
            )
            assert not await page.evaluate("window.simcordPreview.ready")


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [True, False])
async def test_browser_lost_autocomplete_reads_result_without_callback_replay(env, channel, alice, available):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _browser(preview) as page:
            posts = []
            result_reads = []

            async def lose_result(route):
                body = route.request.post_data_json
                if body.get("kind") != "autocomplete_command":
                    await route.continue_()
                    return
                posts.append(body)
                await route.fetch()
                await route.abort("failed")

            async def result_read(route):
                result_reads.append(route.request.url)
                if available:
                    await route.continue_()
                else:
                    await route.fulfill(status=404, json={})

            await page.route("**/api/action", lose_result)
            await page.route("**/api/action-result?*", result_read)
            await page.wait_for_function(
                "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
            )
            composer = page.locator("#channel-composer-input")
            await composer.fill("/tag")
            await composer.press("Enter")
            tag = page.locator('.command-option-input[data-option="name"]')
            await tag.fill("py")
            expected = "answered" if available else "failed"
            await page.wait_for_function(
                "expected => window.simcordPreview.commandPicker.autocomplete.state === expected",
                arg=expected,
            )
            assert len(posts) == len(result_reads) == 1
            assert await tag.input_value() == "py"
            assert await page.evaluate("window.simcordPreview.transport.uncertainRequestId") is None
            if available:
                assert await page.locator(".command-suggestion").count() == 2
            else:
                assert await page.locator(".command-empty").inner_text() == "Loading options failed"


@pytest.mark.asyncio
async def test_browser_busy_query_uses_poll_cadence_without_revision_gate(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _browser(preview) as page:
            requests = []
            revision = await page.evaluate("window.simcordPreview.publishedRevision")

            async def reject_once(route):
                body = route.request.post_data_json
                if body.get("kind") != "autocomplete_command":
                    await route.continue_()
                    return
                requests.append(body)
                if len(requests) == 1:
                    await route.fulfill(
                        json={
                            "requestId": body["request_id"],
                            "sequence": body["sequence"],
                            "expectedSequence": body["sequence"] - 1,
                            "revision": revision,
                            "rejected": True,
                            "settlement": "rejected",
                            "dispatched": False,
                            "dispatch": "not_dispatched",
                            "acknowledgement": "not_applicable",
                            "presentation": "current",
                            "target": None,
                            "command": None,
                            "uncertain": False,
                            "correlation": "c_ownershipbusy1",
                            "outcomes": [],
                            "diagnostics": [make_diagnostic("busy")],
                        }
                    )
                else:
                    await route.continue_()

            await page.route("**/api/action", reject_once)
            await page.wait_for_function(
                "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
            )
            composer = page.locator("#channel-composer-input")
            await composer.fill("/tag")
            await composer.press("Enter")
            await page.locator('.command-option-input[data-option="name"]').fill("py")
            await page.wait_for_function(
                "() => window.simcordPreview.commandPicker.autocomplete.state === 'answered'"
            )
            assert len(requests) == 2
            assert requests[0]["sequence"] == requests[1]["sequence"]
            assert requests[0]["published_revision"] == requests[1]["published_revision"]
            assert await page.locator(".command-suggestion").count() == 2


@pytest.mark.asyncio
async def test_browser_catalog_converges_after_manifest_changes_during_fetch(env, channel, alice):
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _browser(preview) as page:
            started, release = asyncio.Event(), asyncio.Event()
            calls = 0

            async def held_catalog(route):
                nonlocal calls
                calls += 1
                response = await route.fetch()
                if calls == 1:
                    started.set()
                    await release.wait()
                await route.fulfill(response=response)

            await page.route("**/api/commands", held_catalog)

            async def first(interaction: discord.Interaction) -> None:
                await interaction.response.send_message("first")

            env.bot.tree.add_command(
                app_commands.Command(name="ownership-first", description="First", callback=first)
            )
            await env.bot.tree.sync()
            await preview.refresh()
            await asyncio.wait_for(started.wait(), 5)
            first_revision = await page.evaluate("window.simcordPreview.publishedRevision")

            async def second(interaction: discord.Interaction) -> None:
                await interaction.response.send_message("second")

            env.bot.tree.add_command(
                app_commands.Command(name="ownership-second", description="Second", callback=second)
            )
            await env.bot.tree.sync()
            await preview.refresh()
            await page.wait_for_function(
                "revision => window.simcordPreview.publishedRevision > revision", arg=first_revision
            )
            release.set()
            await page.wait_for_function(
                "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
            )
            await page.locator("#channel-composer-input").fill("/ownership-second")
            await page.get_by_role("option").filter(has_text="ownership-second").wait_for()
            assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 410])
async def test_browser_terminal_read_and_bfcache_lifetime(env, channel, alice, status):
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        async with _browser(preview) as page:
            deleted = []
            reads = []
            page.on(
                "request", lambda request: deleted.append(request.url) if request.method == "DELETE" else None
            )
            await page.evaluate("""() => {
              dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}));
              dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
              dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
            }""")

            async def expire(route):
                reads.append(route.request.url)
                await route.fulfill(status=status, json={})

            await page.route("**/api/state", expire)
            await page.wait_for_function("() => window.simcordPreview.authorized === false")
            assert not deleted
            assert len(reads) == 1
            assert await page.locator(".modal-dialog, .channel-message").count() == 0
            assert await page.evaluate("window.simcordPreview.commandPicker.draft") is None
            assert await page.evaluate("window.simcordPreview.renderState.modalHandle") is None
            assert not await page.evaluate("window.simcordPreview.complete")
            await page.locator("#inspector-toggle").click()
            await page.locator("#diagnostics-tab").click()
            for severity in ("error", "warning", "info", "all"):
                await page.locator("#diagnostic-filter").select_option(severity)
                assert not await page.evaluate("window.simcordPreview.complete")
                assert not await page.evaluate("window.simcordPreview.ready")
                assert await page.locator(".modal-dialog, .channel-message").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
async def test_browser_backend_diagnostic_navigates_to_affected_message(env, channel, alice, recovered):
    affected = await alice.send(channel, "Affected diagnostic message")
    initial = await alice.send(channel, "Initial diagnostic message")
    diagnostic = make_diagnostic(
        "message-type-unknown",
        state="recovered" if recovered else "current",
        subject={"messageId": str(affected.id), "controlKey": "private-diagnostic-control"},
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(initial)
        async with _browser(preview) as page:

            async def with_diagnostic(route):
                response = await route.fetch()
                payload = await response.json()
                payload["diagnostics"].append(
                    {
                        **diagnostic,
                        "message": "private-diagnostic-wording",
                        "remediation": "private-diagnostic-remediation",
                        "complete": True,
                    }
                )
                await route.fulfill(response=response, json=payload)

            await page.route("**/api/state", with_diagnostic)
            await preview.refresh()
            await page.wait_for_function(
                "id => window.simcordPreview.diagnostics.some(item => item.id === id)", arg=diagnostic["id"]
            )
            rendered = await page.evaluate(
                "id => window.simcordPreview.diagnostics.find(item => item.id === id)", diagnostic["id"]
            )
            assert rendered["complete"] is recovered
            await page.locator("#inspector-toggle").click()
            await page.locator("#diagnostics-tab").click()
            visible_diagnostics = await page.locator("body").inner_text()
            assert "private-diagnostic-wording" not in visible_diagnostics
            assert "private-diagnostic-remediation" not in visible_diagnostics
            await page.get_by_role("button", name="View affected message", exact=True).click(timeout=5000)
            await page.wait_for_function(
                "id => window.simcordPreview.targetId === id && window.simcordPreview.ready"
                " && !window.simcordPreview.pendingAction",
                arg=str(affected.id),
            )
            assert (
                await page.get_by_label("Focused message")
                .get_by_text("Affected diagnostic message", exact=True)
                .is_visible()
            )
            await page.locator("#capture-tab").click()
            async with page.expect_download() as download_info:
                await page.get_by_role("button", name="Download safe support report").click()
            download = await download_info.value
            download_path = await download.path()
            assert download_path is not None
            report_text = await asyncio.to_thread(Path(download_path).read_text)
            report = json.loads(report_text)
            assert any(item["code"] == diagnostic["code"] for item in report["diagnostics"])
            for private in (
                *diagnostic["subject"].values(),
                "private-diagnostic-wording",
                "private-diagnostic-remediation",
            ):
                assert private not in report_text


@pytest.mark.asyncio
async def test_browser_retained_player_is_disposed_on_projection_eviction(env, channel, alice):
    source = Path(__file__).parents[1] / "fixtures" / "preview" / "voice.ogg"
    message = await alice.send(channel, "Owned audio", attachments=[("voice.ogg", source.read_bytes())])
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        await preview.show(message)
        async with _browser(preview) as page:
            await page.wait_for_function("() => document.querySelector('audio')?.readyState >= 1")
            await page.evaluate("window.retainedAudio = document.querySelector('audio')")
            await page.get_by_role("button", name="Play voice.ogg").click()
            await page.wait_for_function("() => !window.retainedAudio.paused")
            await alice.delete(message)
            await preview.refresh()
            await page.wait_for_function("() => !window.retainedAudio.isConnected")
            assert await page.evaluate("window.retainedAudio.paused")
            assert await page.evaluate("window.retainedAudio.getAttribute('src')") is None
            await page.wait_for_function("() => window.simcordPreview.ready")


@pytest.mark.asyncio
async def test_browser_asset_revocation_preserves_unrelated_playback(env, channel, alice):
    image = await env.bot.get_channel(channel.id).send(
        "Revoked image", file=discord.File(io.BytesIO(png_bytes()), filename="private.png")
    )
    audio = Path(__file__).parents[1] / "fixtures" / "preview" / "voice.ogg"
    await alice.send(channel, "Retained audio", attachments=[("voice.ogg", audio.read_bytes())])
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with _browser(preview) as page:
            await page.wait_for_function("() => document.querySelector('audio')?.readyState >= 1")
            revision = await page.evaluate("window.simcordPreview.publishedRevision")
            await page.evaluate("""() => {
              window.unaffectedAudio = document.querySelector('audio');
              unaffectedAudio.loop = true;
            }""")
            await page.get_by_role("button", name="Play voice.ogg").click()
            await page.wait_for_function("() => !unaffectedAudio.paused")
            await image.edit(attachments=[])
            await page.wait_for_function("() => !document.querySelector('img[alt=\"private.png\"]')")
            assert await page.evaluate("window.simcordPreview.publishedRevision") == revision
            assert await page.evaluate(
                "() => unaffectedAudio === document.querySelector('audio') && !unaffectedAudio.paused"
            )


@pytest.mark.asyncio
async def test_browser_modal_links_exact_focus_and_nested_escape(env, channel, alice):
    modal = discord.ui.Modal(title="Ownership form")
    modal.add_item(discord.ui.TextDisplay("[Help](https://example.com/help)"))
    modal.add_item(
        discord.ui.Label(
            text="Checks",
            component=discord.ui.CheckboxGroup(
                custom_id="checks",
                required=False,
                options=[
                    discord.CheckboxGroupOption(label="First check", value="one"),
                    discord.CheckboxGroupOption(label="Second check", value="two"),
                ],
            ),
        )
    )
    modal.add_item(
        discord.ui.Label(
            text="Person",
            component=discord.ui.UserSelect(
                custom_id="person",
                required=False,
            ),
        )
    )
    view = discord.ui.View()
    button = discord.ui.Button(label="Open ownership form", custom_id="ownership-form")

    async def open_modal(interaction):
        await interaction.response.send_modal(modal)

    button.callback = open_modal
    view.add_item(button)
    message = await env.bot.get_channel(channel.id).send(view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with _browser(preview) as page:
            await page.get_by_role("button", name="Open ownership form").click()
            await page.get_by_role("dialog", name="Ownership form").wait_for()
            await page.get_by_role("button", name="Close modal", exact=True).focus()
            await page.keyboard.press("Tab")
            assert await page.get_by_role("link", name="Help").evaluate(
                "element => element === document.activeElement"
            )
            second = page.get_by_role("checkbox", name="Second check")
            await second.focus()
            context_id = await page.evaluate("window.simcordPreview.contextId")
            preview._pages[context_id].modal._interaction.modal["title"] = "Changed ownership form"
            await preview.refresh()
            await page.get_by_role("dialog", name="Changed ownership form").wait_for()
            assert await second.evaluate("element => element === document.activeElement")
            assert await page.evaluate("""() => window.simcordPreview.activeControlKey ===
              document.activeElement.closest('[data-control-key]').dataset.controlKey""")
            await page.locator(".modal-dialog .select-trigger").click()
            await page.wait_for_function("() => window.simcordPreview.renderState.openPopupKey !== null")
            await page.keyboard.press("Escape")
            await page.wait_for_function("() => window.simcordPreview.renderState.openPopupKey === null")
            assert await page.locator(".modal-dialog").count() == 1
            await page.keyboard.press("Escape")
            await page.wait_for_function("() => window.simcordPreview.renderState.modalHandle === null")
            assert await page.get_by_role("button", name="Open ownership form").evaluate(
                "element => element === document.activeElement"
            )


@pytest.mark.asyncio
async def test_browser_picker_avatar_decode_participates_in_readiness(env, channel, alice):
    message = await alice.send(channel, "Picker avatar bytes", attachments=[("avatar.png", png_bytes())])
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        await preview.show(message)
        async with _browser(preview) as page:
            await page.evaluate("""() => {
              const decode = HTMLImageElement.prototype.decode;
              window.pickerDecodes = [];
              HTMLImageElement.prototype.decode = function() {
                if (this.classList.contains('entity-avatar-img') && this.closest('.command-popup')) {
                  return new Promise(resolve => window.pickerDecodes.push(() => decode.call(this).then(resolve)));
                }
                return decode.call(this);
              };
            }""")
            context_id = await page.evaluate("window.simcordPreview.contextId")

            async def avatar_catalog(route):
                response = await route.fetch()
                catalog = await response.json()
                catalog["application"]["avatarAssetId"] = next(
                    key
                    for key, asset in preview._page_payload(preview._pages[context_id])["assets"].items()
                    if asset["filename"] == "avatar.png" and asset["available"]
                )
                await route.fulfill(response=response, json=catalog)

            await page.route("**/api/commands", avatar_catalog)

            async def command(interaction: discord.Interaction) -> None:
                await interaction.response.send_message("ready")

            env.bot.tree.add_command(
                app_commands.Command(name="avatar-ready", description="Ready", callback=command)
            )
            await env.bot.tree.sync()
            revision = await page.evaluate("window.simcordPreview.publishedRevision")
            await preview.refresh()
            await page.wait_for_function(
                "revision => window.simcordPreview.publishedRevision > revision", arg=revision
            )
            await page.wait_for_function(
                "() => window.simcordPreview.commandPicker.catalog.state === 'ready'"
            )
            await page.locator("#channel-composer-input").fill("/avatar-ready")
            await page.wait_for_function("() => window.pickerDecodes.length > 0")
            assert not await page.evaluate("window.simcordPreview.ready")
            assert await page.locator("#preview-app").get_attribute("aria-busy") == "true"
            await page.locator("#channel-composer-input").fill("No command picker")
            await page.wait_for_function("() => window.simcordPreview.ready")
            assert await page.locator("#preview-app").get_attribute("aria-busy") == "false"
            assert await page.locator(".command-popup").is_hidden()
            await page.locator("#channel-composer-input").fill("/avatar-ready")
            await page.wait_for_function("() => window.pickerDecodes.length > 1")
            assert not await page.evaluate("window.simcordPreview.ready")
            await page.evaluate("() => window.pickerDecodes.splice(0).forEach(release => release())")
            await page.wait_for_function("() => window.simcordPreview.ready")
            assert await page.evaluate("window.simcordPreview.calibration.status") == "uncalibrated"


@pytest.mark.asyncio
async def test_browser_delayed_old_viewer_read_cannot_restore_projection(env, channel, alice):
    bob = env.guild.add_member(env.create_user("ownership-bob"))
    await alice.send(channel, "Published ownership message")
    async with env.preview(channel, viewers=[alice, bob], layout="channel") as preview:
        async with _browser(preview) as page:
            from playwright.async_api import Error

            started, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
            held = False

            async def hold_old_viewer(route):
                nonlocal held
                if held:
                    await route.continue_()
                    return
                held = True
                response = await route.fetch()
                started.set()
                await release.wait()
                try:
                    await route.fulfill(response=response)
                except Error:
                    # An obsolete read may already have been canceled by its owner.
                    pass
                finally:
                    delivered.set()

            await page.route("**/api/state", hold_old_viewer)
            await asyncio.wait_for(started.wait(), 5)
            await page.locator("#viewer-picker").select_option(str(bob.id))
            await page.wait_for_function(
                "id => window.simcordPreview.viewerId === id && window.simcordPreview.ready", arg=str(bob.id)
            )
            generation = await page.evaluate("window.simcordPreview.contextGeneration")
            release.set()
            await asyncio.wait_for(delivered.wait(), 5)
            await page.wait_for_function("() => window.simcordPreview.transport.state !== 'error'")
            assert await page.evaluate("window.simcordPreview.viewerId") == str(bob.id)
            assert await page.evaluate("window.simcordPreview.contextGeneration") == generation


@pytest.mark.asyncio
async def test_browser_close_acknowledgement_survives_expired_context_reads(env, channel, alice):
    await env.bot.get_channel(channel.id).send("A closing surface")
    async with env.preview(channel, viewers=[alice]) as preview:
        async with _browser(preview, clock=True) as page:
            admitted, release = asyncio.Event(), asyncio.Event()
            submitted_id = None

            async def hold_close_receipt(route):
                nonlocal submitted_id
                body = route.request.post_data_json
                if body.get("kind") != "close":
                    await route.continue_()
                    return
                submitted_id = body["request_id"]
                response = await route.fetch()
                admitted.set()
                await release.wait()
                await route.fulfill(response=response)

            await page.route("**/api/action", hold_close_receipt)
            try:
                await page.locator(".session-menu > summary").click()
                await page.locator("#close").click()
                await asyncio.wait_for(admitted.wait(), 5)
                await page.evaluate("""() => {
                  const originalFetch = fetch.bind(window);
                  window.fetch = (input, init) => {
                    const url = typeof input === 'string' ? input : input.url;
                    return url.endsWith('/api/state')
                      ? Promise.resolve(new Response('{}', {status: 410}))
                      : originalFetch(input, init);
                  };
                }""")
                await page.clock.run_for(1000)
                assert await page.evaluate("simcordPreview.transport.uncertainRequestId") == submitted_id
                assert not await page.evaluate("simcordPreview.complete")
                release.set()
                await page.wait_for_function(
                    "() => !simcordPreview.authorized && !simcordPreview.pendingAction && simcordPreview.complete"
                )
                assert await page.evaluate("simcordPreview.complete")
                assert await page.evaluate("simcordPreview.visibleMessageIds") == []
                assert await page.evaluate("simcordPreview.transport.uncertainRequestId") is None
                assert not await page.evaluate("simcordPreview.ready")
                await page.locator("#inspector-toggle").click()
                await page.locator("#diagnostics-tab").click()
                for severity in ("warning", "all"):
                    await page.locator("#diagnostic-filter").select_option(severity)
                    assert await page.evaluate("simcordPreview.complete")
                    assert not await page.evaluate("simcordPreview.ready")
                    assert await page.evaluate("simcordPreview.visibleMessageIds") == []
            finally:
                release.set()
                await page.unroute_all(behavior="wait")
