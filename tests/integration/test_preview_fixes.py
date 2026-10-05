"""Regression coverage for the preview protocol fixes."""

import asyncio
import io
import json
from importlib import resources
from pathlib import Path

import pytest

pytest.importorskip("PIL")

import discord
import jsonschema
from aiohttp import ClientSession
from PIL import Image
from preview_helpers import action_body, control_key, gif_bytes, png_bytes, preview_headers, target_message

import simcord
from simcord.components import walk_components

_SCHEMA = json.loads(resources.files("simcord.preview").joinpath("protocol.schema.json").read_text())
_BROWSER_STATUS_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/browserStatus", "$defs": _SCHEMA["$defs"]}
)


class _ReleasableView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @discord.ui.button(label="Wait", custom_id="wait")
    async def wait(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.started.set()
        await self.release.wait()
        await interaction.response.defer()


class _CountingView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    @discord.ui.button(label="Count", custom_id="browser-count")
    async def count(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.calls += 1
        await interaction.response.defer()


class _CountingChannelSelect(discord.ui.ChannelSelect):
    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.calls += 1
        await interaction.response.defer()


class _CountingChannelSelectView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.add_item(_CountingChannelSelect(custom_id="channels", min_values=1, max_values=25))


@pytest.mark.asyncio
async def test_ephemeral_attachment_not_servable_via_foreign_embed(env, channel, alice):
    """CDN bytes only resolve for the attachment's owning message."""
    bob = env.guild.add_member(env.create_user("bob"))
    ephemeral = await alice.context_menu(channel, "Report Member", bob)
    stored = env.backend.get_message(channel.id, ephemeral.response.id)
    attachment = env.backend.cdn.store_attachment(
        ephemeral.response.id, channel.id, "secret.png", png_bytes(), None
    )
    stored.attachments.append(attachment)
    embed = discord.Embed().set_image(url=attachment["url"])
    foreign = await env.bot.get_channel(channel.id).send(embed=embed)

    async with env.preview(channel, viewers=[alice, bob]) as preview:
        bob_page = preview._open_page(bob.id, target_id=foreign.id)
        image = target_message(preview._page_payload(bob_page))["embeds"][0]["image"]
        assert image["asset_id"]
        assert image["available"] is False
        with pytest.raises(simcord.SetupError, match="unavailable"):
            await preview._prepare_asset(bob_page.id, image["asset_id"])
        async with ClientSession() as client:
            response = await client.get(
                preview._origin + f"/api/assets/{image['asset_id']}",
                headers=preview_headers(preview, bob_page.id),
            )
            assert response.status == 404

        # Even the authorized viewer cannot resolve the foreign embed's URL to
        # the ephemeral attachment: ownership, not visibility, gates CDN bytes.
        await preview.show(foreign)
        image = target_message(preview._page_payload(preview._python))["embeds"][0]["image"]
        assert image["available"] is False
        with pytest.raises(simcord.SetupError, match="unavailable"):
            await preview._prepare_asset("python", image["asset_id"])

        # The owning message itself still serves its attachment to alice.
        await preview.show(ephemeral.response)
        own = target_message(preview._page_payload(preview._python))["attachments"][0]
        assert own["available"] is True
        assert (await preview._prepare_asset("python", own["asset_id"], download=True))[1] == png_bytes()


@pytest.mark.asyncio
async def test_asset_unavailable_after_owner_deleted(env, channel, alice):
    """Deletion invalidates an asset immediately and after refresh."""
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(png_bytes()), filename="pic.png")
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        asset_id = target_message(preview._page_payload(preview._python))["attachments"][0]["asset_id"]
        assert (await preview._prepare_asset("python", asset_id, download=True))[1] == png_bytes()
        await message.delete()
        with pytest.raises(simcord.SetupError, match="unavailable"):
            await preview._prepare_asset("python", asset_id)
        await preview.refresh()
        with pytest.raises(simcord.SetupError, match="unavailable"):
            await preview._prepare_asset("python", asset_id)
        assert asset_id not in preview._page_payload(preview._python)["assets"]


@pytest.mark.asyncio
async def test_revoked_access_snapshot_omits_modal_and_candidates(env, channel, alice):
    assign = await alice.slash(channel, "assign")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(assign.response)
        page = preview._python
        payload = preview._page_payload(page)
        assert payload["candidates"][control_key(payload, "who")]

        cached = env.bot.get_channel(channel.id)
        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await cached.set_permissions(member, view_channel=False)
        await preview.refresh()
        denied = preview._page_payload(page)
        assert denied["status"] == "access_denied"
        assert denied["modal"] is None
        assert denied["candidates"] == {}
        assert denied["messageIndex"] == []
        assert denied["messages"] == {}
        assert denied["targetId"] is None
        assert denied["assets"] == {}

        await cached.set_permissions(member, view_channel=True)
        feedback = await alice.slash(channel, "feedback")
        await preview.show(feedback)
        assert preview._page_payload(page)["modal"] is not None
        await cached.set_permissions(member, view_channel=False)
        await preview.refresh()
        denied = preview._page_payload(page)
        assert denied["status"] == "access_denied"
        assert denied["modal"] is None
        assert denied["candidates"] == {}


@pytest.mark.asyncio
async def test_rejected_actions_keep_sequence_and_report_expected(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python

        gap = await preview._action("python", action_body(page, "refresh", 5, request_id="gap"))
        assert gap["rejected"] is True
        assert gap["settlement"] == "rejected"
        assert gap["dispatch"] == "not_dispatched"
        assert gap["dispatched"] is False
        assert gap["sequence"] == 5
        assert gap["expectedSequence"] == 0
        assert gap["diagnostics"][0]["code"] == "sequence-gap"

        payload = preview._page_payload(page)
        ping_key = control_key(payload, "persistent:ping")
        stale = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="stale",
                control_key=ping_key,
                published_revision=page.revision + 1,
            ),
        )
        assert stale["rejected"] is True
        assert stale["diagnostics"][0]["code"] == "stale-revision"
        assert stale["expectedSequence"] == 0

        # Rejections consumed nothing: sequence 1 is still the next admission.
        settled = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="click",
                control_key=ping_key,
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"
        assert settled["dispatched"] is True
        assert settled["expectedSequence"] == 1


@pytest.mark.asyncio
async def test_invalid_modal_submit_does_not_consume_sequence(env, channel, alice):
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        invalid = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="bad-modal",
                modal_handle=page.modal_handle,
                values={"bogus": "x"},
                published_revision=page.revision,
            ),
        )
        assert invalid["rejected"] is True
        assert invalid["diagnostics"][0]["code"] == "validation-failed"
        assert invalid["expectedSequence"] == 0
        # The modal is still open and sequence 1 remains available.
        assert preview._page_payload(page)["modal"] is not None
        settled = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="good-modal",
                modal_handle=page.modal_handle,
                values={"name": "Ada"},
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"
        assert channel.last_message.content == "Thanks Ada"


@pytest.mark.asyncio
async def test_close_cancellation_does_not_strand_server(env, channel, alice):
    preview = env.preview(channel, viewers=[alice])
    await preview.__aenter__()
    task = asyncio.create_task(preview.close())
    # Yield twice so close() reaches its shielded cleanup await.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert preview._cleanup_task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await preview.wait_closed()
    assert preview._closed_event.is_set()
    assert preview._server.port is None
    assert env._preview is None


@pytest.mark.asyncio
async def test_page_lease_expiry_frees_slots(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        opened = [preview._open_page(alice.id) for _ in range(preview._MAX_PAGES)]
        with pytest.raises(simcord.SetupError, match="page limit"):
            preview._open_page(alice.id)
        opened[0].last_activity -= preview._PAGE_LEASE_SECONDS + 1
        freed = preview._open_page(alice.id)
        assert opened[0].id not in preview._pages
        assert freed.id in preview._pages


@pytest.mark.asyncio
async def test_shared_blob_counts_once_against_media_budget(env, channel, alice):
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(png_bytes()), filename="pic.png")
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        retained = preview._retained_media_bytes
        assert retained == len(png_bytes())
        second = preview._open_page(alice.id, target_id=message.id)
        assert preview._retained_media_bytes == retained
        assert len(preview._blobs) == 1
        assert next(iter(preview._blobs.values())).refs == 2
        preview._close_page(second.id)
        assert preview._retained_media_bytes == retained
    assert preview._retained_media_bytes == 0


@pytest.mark.asyncio
async def test_native_audio_state_survives_local_select_redraw_and_source_replacement(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    original = await asyncio.to_thread((Path(__file__).parents[1] / "fixtures/preview/voice.ogg").read_bytes)

    view = discord.ui.View()
    view.add_item(
        discord.ui.Select(
            custom_id="draft",
            min_values=1,
            max_values=2,
            options=[
                discord.SelectOption(label="One", value="one", default=True),
                discord.SelectOption(label="Two", value="two"),
            ],
        )
    )
    message = await env.bot.get_channel(channel.id).send(
        "audio state",
        file=discord.File(io.BytesIO(original), filename="tone.ogg"),
        view=view,
    )
    stored = env.backend.get_message(channel.id, message.id)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function(
                    "() => { const audio = document.querySelector('audio.media-player-native');"
                    " return audio?.readyState >= 2 && audio.duration > 0.5; }"
                )
                before = await page.evaluate("""async () => {
                  const audio = document.querySelector("audio.media-player-native");
                  const seeked = new Promise(resolve => audio.addEventListener("seeked", resolve, { once: true }));
                  audio.currentTime = 0.4;
                  await seeked;
                  audio.pause();
                  audio.volume = 0.35;
                  audio.playbackRate = 1.5;
                  audio.loop = true;
                  window.__retainedAudio = audio;
                  return audio.currentTime;
                }""")

                await page.locator(".select-trigger").first.click()
                await page.get_by_role("option", name="Two", exact=True).click()
                await page.wait_for_function(
                    "() => document.querySelector('.select-value')?.textContent.includes('Two')"
                )
                after = await page.evaluate("""() => {
                  const audio = document.querySelector("audio.media-player-native");
                  return {
                    same: audio === window.__retainedAudio,
                    currentTime: audio?.currentTime,
                    paused: audio?.paused,
                    volume: audio?.volume,
                    playbackRate: audio?.playbackRate,
                  };
                }""")
                assert after["same"] is True
                assert abs(after["currentTime"] - before) < 0.05
                assert after["paused"] is True
                assert after["volume"] == pytest.approx(0.35)
                assert after["playbackRate"] == pytest.approx(1.5)

                await page.wait_for_function("() => window.simcordPreview.ready")
                await page.keyboard.press("Escape")
                await page.get_by_role("button", name="Play tone.ogg", exact=True).click()
                await page.wait_for_function(
                    "() => !document.querySelector('audio.media-player-native').paused"
                )
                await page.locator(".select-trigger").first.click()
                await page.get_by_role("option", name="One", exact=True).click()
                playing = await page.locator("audio.media-player-native").evaluate(
                    "(audio) => ({paused: audio.paused, loop: audio.loop})"
                )
                assert playing["paused"] is False
                assert playing["loop"] is True

                old_source = await page.locator("audio.media-player-native").get_attribute("src")
                stored.attachments[:] = [
                    env.backend.cdn.store_attachment(
                        message.id, channel.id, "replacement.ogg", original, None
                    )
                ]
                await preview.refresh()
                await page.wait_for_function(
                    "(oldSource) => { const audio = document.querySelector('audio.media-player-native');"
                    " return audio?.src && audio.src !== oldSource && audio.readyState >= 2 && audio.duration > 0.5; }",
                    arg=old_source,
                )
                replaced = await page.evaluate(
                    "() => document.querySelector('audio.media-player-native') !== window.__retainedAudio"
                )
                assert replaced is True
                await page.wait_for_function("() => window.simcordPreview.ready")
                restart_revision = await page.evaluate("() => window.simcordPreview.publishedRevision")
                restart_source = await page.locator("audio.media-player-native").get_attribute("src")
                from fixtures.sample_bot import create_bot

                await env.restart_bot(create_bot())
                await preview.refresh()
                await page.wait_for_function(
                    "(revision) => window.simcordPreview.publishedRevision > revision"
                    " && window.simcordPreview.ready && !window.simcordPreview.pendingAction",
                    arg=restart_revision,
                )
                restarted = await page.locator("audio.media-player-native").evaluate(
                    "(audio) => ({source: audio.getAttribute('src'),"
                    " readyState: audio.readyState, duration: audio.duration})"
                )
                assert isinstance(restarted["source"], str), restarted
                assert restarted["source"].startswith("blob:")
                assert restarted["source"] != restart_source
                assert restarted["readyState"] >= 2 and restarted["duration"] > 0.5
                await page.keyboard.press("Escape")
                await page.get_by_role("button", name="Play replacement.ogg", exact=True).click()
                await page.wait_for_function(
                    "() => !document.querySelector('audio.media-player-native').paused"
                )
                assert (await page.evaluate("() => window.simcordPreview"))["complete"] is True

                stored.attachments.clear()
                await preview.refresh()
                await page.wait_for_function("() => !document.querySelector('audio.media-player-native')")
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_display_preserves_animation_and_download_serves_original(env, channel, alice):
    original = gif_bytes()
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(original), filename="anim.gif")
    )
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        await preview.show(message)
        asset_id = target_message(preview._page_payload(preview._python))["attachments"][0]["asset_id"]
        response = await client.get(
            preview._origin + f"/api/assets/{asset_id}", headers=preview_headers(preview, "python")
        )
        assert response.status == 200
        assert response.headers["Content-Type"] == "image/gif"
        assert "inline" in response.headers["Content-Disposition"]
        body = await response.read()
        assert body == original
        with Image.open(io.BytesIO(body)) as image:
            assert image.format == "GIF"
            assert image.n_frames == 2
        response = await client.get(
            preview._origin + f"/api/assets/{asset_id}?download=1",
            headers=preview_headers(preview, "python"),
        )
        assert response.status == 200
        assert "attachment" in response.headers["Content-Disposition"]
        assert await response.read() == original


@pytest.mark.asyncio
async def test_normalized_blob_charged_once_per_blob(env, channel, alice):
    """The shared normalized copy is charged once, not once per asset record."""
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(png_bytes()), filename="pic.png")
    )
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        await preview.show(message)
        second = preview._open_page(alice.id, target_id=message.id)
        for page in (preview._python, second):
            asset_id = target_message(preview._page_payload(page))["attachments"][0]["asset_id"]
            response = await client.get(
                preview._origin + f"/api/assets/{asset_id}", headers=preview_headers(preview, page.id)
            )
            assert response.status == 200
            await response.read()
        blob = next(iter(preview._blobs.values()))
        assert blob.normalized_refs == 2
        expected = len(png_bytes()) + blob.normalized_size
        assert preview._retained_media_bytes == expected
        preview._close_page(second.id)
        assert preview._retained_media_bytes == expected
    assert preview._retained_media_bytes == 0


@pytest.mark.asyncio
async def test_disabled_select_rejected_before_dispatch(env, channel, alice):
    """A select disabled after the snapshot rejects at admission, sequence intact."""
    panel = await alice.slash(channel, "color")
    stored = env.backend.get_message(channel.id, panel.response.id)
    select = next(
        component for component in walk_components(stored.components) if component.get("custom_id") == "color"
    )
    select["disabled"] = True
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(panel.response)
        page = preview._python
        color_key = control_key(preview._page_payload(page), "color")
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="disabled-select",
                control_key=color_key,
                values=["red"],
                published_revision=page.revision,
            ),
        )
        assert rejected["rejected"] is True
        assert rejected["dispatched"] is False
        assert rejected["expectedSequence"] == 0

        select["disabled"] = False
        settled = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="enabled-select",
                control_key=color_key,
                values=["red"],
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"
        assert channel.last_message.content == "Picked red"


@pytest.mark.asyncio
async def test_click_on_select_rejected_before_dispatch(env, channel, alice):
    """kind=click against a select's custom_id is a pre-admission rejection."""
    panel = await alice.slash(channel, "color")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(panel.response)
        page = preview._python
        color_key = control_key(preview._page_payload(page), "color")
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="click-select",
                control_key=color_key,
                published_revision=page.revision,
            ),
        )
        assert rejected["rejected"] is True
        assert rejected["dispatched"] is False
        assert rejected["expectedSequence"] == 0


@pytest.mark.asyncio
async def test_consumed_modal_rejected_on_second_page(env, channel, alice):
    """A modal consumed on one page rejects, not fails, when resubmitted elsewhere."""
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        second = preview._open_page(alice.id)
        assert second.modal_handle is not None
        settled = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="submit",
                modal_handle=page.modal_handle,
                values={"name": "Ada"},
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"

        resubmit = await preview._action(
            second.id,
            action_body(
                second,
                "modal_submit",
                1,
                request_id="resubmit",
                modal_handle=second.modal_handle,
                values={"name": "Bob"},
                published_revision=second.revision,
            ),
        )
        assert resubmit["rejected"] is True
        assert resubmit["dispatched"] is False
        assert resubmit["expectedSequence"] == 0
        assert channel.last_message.content == "Thanks Ada"


@pytest.mark.asyncio
async def test_modal_missing_required_control_rejected(env, channel, alice):
    """Omitting a required modal control rejects at admission, modal stays open."""
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="missing-required",
                modal_handle=page.modal_handle,
                values={},
                published_revision=page.revision,
            ),
        )
        assert rejected["rejected"] is True
        assert rejected["dispatched"] is False
        assert rejected["expectedSequence"] == 0
        assert preview._page_payload(page)["modal"] is not None


@pytest.mark.asyncio
async def test_open_page_authorizes_target_against_the_requested_viewer(env, channel, alice):
    """target_id access is checked for the page's viewer, not viewers[0]."""
    bob = env.guild.add_member(env.create_user("bob"))
    # Ephemeral responses are visible only to their invoking user.
    ephemeral = await bob.context_menu(channel, "Report Member", alice)
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        page = preview._open_page(bob.id, target_id=ephemeral.response.id)
        assert page.target_id == ephemeral.response.id
        assert target_message(preview._page_payload(page))["id"] == str(ephemeral.response.id)
        # The reverse direction resolves against alice too: a message only bob
        # can see is not pinned for alice's page, which falls back to her own
        # initial (empty) target instead of leaking the ephemeral.
        other = preview._open_page(alice.id, target_id=ephemeral.response.id)
        assert other.target_id is None
        assert target_message(preview._page_payload(other)) is None


@pytest.mark.asyncio
async def test_second_enter_rejected_and_close_preserves_first_registration(env, channel, alice):
    """Two previews may be constructed; only the first registration may enter."""
    first = env.preview(channel, viewers=[alice])
    second = env.preview(channel, viewers=[alice])
    await first.__aenter__()
    try:
        with pytest.raises(simcord.SetupError, match="Only one active"):
            await second.__aenter__()
        # Closing the rejected preview must not orphan the live registration.
        await second.close()
        assert env._preview is first
        with pytest.raises(simcord.SetupError, match="Only one active"):
            env.preview(channel, viewers=[alice])
    finally:
        await first.close()
    assert env._preview is None


@pytest.mark.asyncio
async def test_failed_dispatch_settles_instead_of_wedging_replays(env, channel, alice, monkeypatch):
    """An unexpected mid-dispatch error records a failed, replayable result."""
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python
        body = action_body(page, "refresh", 1, request_id="boom")

        async def boom():
            raise KeyError("guild vanished")

        monkeypatch.setattr(env, "_settle_internal", boom)
        first = await preview._action("python", dict(body))
        assert first["rejected"] is False
        assert first["settlement"] == "failed"
        assert first["dispatched"] is False
        assert "guild vanished" not in repr(first)
        assert first["diagnostics"][0]["code"] == "action-callback-error"
        assert page.status == "stale"

        # The same sequence+request_id replays the stored result rather than
        # reporting "pending" forever.
        replayed = await preview._action("python", dict(body))
        assert replayed == first
        assert replayed["settlement"] == "failed"

        # Unexpected dispatch failures stay observable through env.errors;
        # consume the captured error so teardown does not re-raise it.
        assert isinstance(env.errors[-1], KeyError)
        with pytest.raises(ExceptionGroup):
            env.raise_errors()


@pytest.mark.asyncio
async def test_preview_generation_fields_reject_before_sequence_admission(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python
        missing = action_body(page, "refresh", 1)
        missing.pop("generation")
        rejected = await preview._action("python", missing)
        assert rejected["rejected"] is True
        assert rejected["diagnostics"][0]["code"] == "bad-envelope"
        assert rejected["expectedSequence"] == 0

        malformed = action_body(page, "refresh", 1, request_id="malformed", bot_generation=True)
        rejected = await preview._action("python", malformed)
        assert rejected["rejected"] is True
        assert rejected["diagnostics"][0]["code"] == "bad-envelope"
        assert rejected["expectedSequence"] == 0

        admitted = await preview._action("python", action_body(page, "refresh", 1, request_id="valid"))
        assert admitted["rejected"] is False
        assert admitted["sequence"] == 1


@pytest.mark.asyncio
async def test_preview_page_release_allows_repeated_reload_contexts(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        headers = preview_headers(preview)
        for _ in range(preview._MAX_PAGES + 4):
            response = await client.post(preview._origin + "/api/pages", headers=headers, json={})
            assert response.status == 200
            page = await response.json()
            context = page["context"]["id"]
            released = await client.delete(
                preview._origin + f"/api/pages/{context}",
                headers=preview_headers(preview, context),
            )
            assert released.status == 200
        assert len(preview._pages) == 1


@pytest.mark.asyncio
async def test_page_close_waits_for_active_action_before_releasing_assets(env, channel, alice):
    view = _ReleasableView()
    message = await env.bot.get_channel(channel.id).send(
        "wait",
        view=view,
        file=discord.File(io.BytesIO(png_bytes()), filename="owned.png"),
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._open_page()
        assert page.assets
        wait_key = control_key(preview._page_payload(page), "wait")
        task = asyncio.create_task(
            preview._action(
                page.id,
                action_body(
                    page,
                    "click",
                    1,
                    request_id="active-close",
                    control_key=wait_key,
                    published_revision=page.revision,
                ),
            )
        )
        await view.started.wait()
        preview._close_page(page.id)
        assert page.id in preview._pages
        view.release.set()
        assert (await task)["settlement"] == "settled"
        assert page.id not in preview._pages
        assert not page.assets
        assert {blob.refs for blob in preview._blobs.values()} == {1}


@pytest.mark.asyncio
async def test_browser_uncertain_receipt_recovers_dropped_action_response(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    view = _CountingView()
    message = await env.bot.get_channel(channel.id).send("count action", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.add_init_script("""(() => {
                  const realFetch = window.fetch.bind(window);
                  window.__droppedActionResponses = 0;
                  window.fetch = async (input, init = {}) => {
                    const url = typeof input === "string" ? input : input.url;
                    let body = null;
                    try { body = typeof init.body === "string" ? JSON.parse(init.body) : null; } catch {}
                    if (url.endsWith("/api/action") && body?.kind === "click") {
                      await realFetch(input, init);
                      window.__droppedActionResponses += 1;
                      throw new Error("response dropped after real action POST");
                    }
                    return realFetch(input, init);
                  };
                })()""")
                await page.goto(preview.url)
                await page.wait_for_function(
                    "() => window.simcordPreview?.ready && !window.simcordPreview.pendingAction"
                    " && window.simcordPreview.awaitingRevision === null"
                )
                await page.get_by_role("button", name="Count", exact=True).click()
                await page.wait_for_function("""() => {
                  const status = window.simcordPreview;
                  return window.__droppedActionResponses === 1
                    && status.transport.uncertainRequestId === null
                    && status.transport.history.some(item => item.code === "action-receipt-observed")
                    && status.diagnostics.some(item => item.code === "action-response-unavailable"
                      && item.state === "recovered" && item.complete === true);
                }""")
                status = await page.evaluate("() => window.simcordPreview")
                _BROWSER_STATUS_VALIDATOR.validate(status)
                assert view.calls == 1
                assert status["complete"] is True
                assert not any(
                    item["code"] in {"action-response-unavailable", "action-receipt-unavailable"}
                    and item["state"] != "recovered"
                    for item in status["diagnostics"]
                )
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_close_proceeds_after_unadmitted_uncertain_action(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    view = _CountingView()
    message = await env.bot.get_channel(channel.id).send("close uncertain action", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.add_init_script("""(() => {
                  const realFetch = window.fetch.bind(window);
                  window.__blockedClicks = 0;
                  window.fetch = async (input, init = {}) => {
                    const url = typeof input === "string" ? input : input.url;
                    let body = null;
                    try { body = typeof init.body === "string" ? JSON.parse(init.body) : null; } catch {}
                    if (url.endsWith("/api/action") && body?.kind === "click") {
                      window.__blockedClicks += 1;
                      throw new Error("click POST blocked before admission");
                    }
                    return realFetch(input, init);
                  };
                })()""")
                await page.goto(preview.url)
                await page.wait_for_function(
                    "() => window.simcordPreview?.ready && !window.simcordPreview.pendingAction"
                    " && window.simcordPreview.awaitingRevision === null"
                )
                await page.get_by_role("button", name="Count", exact=True).click()
                await page.wait_for_function("""() => window.__blockedClicks === 1
                  && window.simcordPreview.transport.uncertainRequestId !== null
                  && window.simcordPreview.pendingAction === null""")
                await page.locator(".session-menu > summary").click()
                await page.get_by_role("button", name="End preview session", exact=True).click()
                await page.wait_for_function("""() => {
                  const status = window.simcordPreview;
                  return status.authorized === false && status.complete === true
                    && status.transport.uncertainRequestId === null
                    && status.transport.history.some(item => item.code === "action-not-admitted-by-close")
                    && status.visibleMessageIds.length === 0 && Object.keys(status.selectDrafts).length === 0;
                }""")
                await asyncio.wait_for(preview._closed_event.wait(), timeout=5)
                assert view.calls == 0
                assert await page.locator("#focused-content *").count() == 0
                assert await page.locator("#channel-message-list *").count() == 0
                assert (
                    await page.locator(
                        "#message-picker button[data-message-id], #viewer-picker option"
                    ).count()
                    == 0
                )
                assert await page.evaluate(
                    "() => window.simcordPreview.targetId === null && window.simcordPreview.viewerId === null"
                )
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_reauthorizes_cached_channel_select_draft_after_refresh(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    private = env.guild.create_text_channel(
        "private-review-entity",
        overwrites={
            env.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            env.bot.user: discord.PermissionOverwrite(view_channel=True, manage_roles=True),
            env.bot.get_guild(env.guild.id).get_member(alice.id): discord.PermissionOverwrite(
                view_channel=True
            ),
        },
    )
    cached_private = env.bot.get_channel(private.id)
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    view = _CountingChannelSelectView()
    message = await env.bot.get_channel(channel.id).send("channel select", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready")
                trigger = page.locator(".select-trigger").first
                await trigger.click()
                await page.locator(".select-candidate-search").first.fill(private.name)
                option = page.locator(".select-option").filter(has_text=private.name).first
                await option.wait_for()
                await option.click()
                await page.wait_for_function(
                    """({id, name}) => {
                      const trigger = document.querySelector(".select-trigger");
                      const key = trigger?.dataset.controlKey;
                      return window.simcordPreview.selectDrafts[key]?.includes(id)
                        && trigger.textContent.includes(name);
                    }""",
                    arg={"id": str(private.id), "name": private.name},
                )
                key = await trigger.get_attribute("data-control-key")

                await cached_private.set_permissions(member, view_channel=False)
                await env.settle()
                await preview.refresh()
                await page.wait_for_function(
                    """({id, name, key}) => {
                      const status = window.simcordPreview;
                      const trigger = document.querySelector(".select-trigger");
                      const guidance = document.querySelector(".select-guidance")?.textContent || "";
                      return !status.selectDrafts[key]?.includes(id)
                        && !trigger?.textContent.includes(name)
                        && !trigger?.textContent.includes(id)
                        && guidance.includes("no longer available");
                    }""",
                    arg={"id": str(private.id), "name": private.name, "key": key},
                )
                assert view.calls == 0
                trigger_text = await trigger.text_content()
                assert private.name not in trigger_text
                assert str(private.id) not in trigger_text
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_rendering_diagnostics_follow_media_surface_replacement(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    image_url = "https://cdn.example.test/browser-repaired-image.png"
    good = await env.bot.get_channel(channel.id).send("ordinary target")
    bad = await env.bot.get_channel(channel.id).send(embed=discord.Embed().set_image(url=image_url))
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(bad)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function(
                    "(id) => window.simcordPreview?.ready"
                    " && window.simcordPreview.targetId === id && !window.simcordPreview.complete",
                    arg=str(bad.id),
                )
                assert await page.locator(".media-unavailable").count() == 1
                before_resize = await page.evaluate("() => window.simcordPreview.publishedRevision")
                await page.set_viewport_size({"width": 1180, "height": 820})
                await page.wait_for_function(
                    "(revision) => window.simcordPreview.publishedRevision > revision",
                    arg=before_resize,
                )
                await page.wait_for_function(
                    "() => window.simcordPreview.ready && !window.simcordPreview.pendingAction"
                    " && window.simcordPreview.awaitingRevision === null"
                )
                status = await page.evaluate("() => window.simcordPreview")
                assert status["complete"] is False
                assert any(
                    item["code"] == "media-unavailable" and item["state"] == "current"
                    for item in status["diagnostics"]
                )

                await page.locator(f"#message-picker button[data-message-id='{good.id}']").click()
                await page.wait_for_function(
                    "(id) => window.simcordPreview.targetId === id && window.simcordPreview.complete",
                    arg=str(good.id),
                )
                status = await page.evaluate("() => window.simcordPreview")
                assert not any(
                    item["code"] == "media-unavailable" and item["state"] == "current"
                    for item in status["diagnostics"]
                )
                assert await page.locator(".media-unavailable").count() == 0

            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_retained_avatar_and_image_hold_readiness_across_refresh(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    from simcord.backend.cdn import CDN_BASE

    user = env.create_user("slow-avatar", avatar="slow-hash")
    member = env.guild.add_member(user)
    bob = env.guild.add_member(env.create_user("other-viewer"))
    await member.send(channel, "retained media", attachments=[("image.png", png_bytes())])
    avatar_url = f"{CDN_BASE}/avatars/{user.id}/slow-hash.png"
    async with (
        env.preview(
            channel, viewers=[alice, bob], layout="channel", assets={avatar_url: ("avatar.png", png_bytes())}
        ) as preview,
        async_playwright() as playwright,
    ):
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.add_init_script("""(() => {
              const fetch = window.fetch.bind(window);
              const assets = [];
              window.__heldAssets = 0;
              window.__releaseAssets = () => assets.splice(0).forEach(resolve => resolve());
              window.fetch = async (input, init = {}) => {
                const url = typeof input === "string" ? input : input.url;
                if (url.startsWith("/api/assets/")) {
                  window.__heldAssets += 1;
                  await new Promise(resolve => assets.push(resolve));
                }
                if (url.endsWith("/api/action") && JSON.parse(init.body || "{}").kind === "refresh") {
                  await new Promise(resolve => { window.__releaseRefresh = resolve; });
                }
                return fetch(input, init);
              };
            })()""")
            await page.goto(preview.url)
            await page.wait_for_function("() => window.__heldAssets === 2")
            before = await page.evaluate("() => window.simcordPreview")
            _BROWSER_STATUS_VALIDATOR.validate(before)
            assert before["ready"] is False
            await page.evaluate(
                "() => { window.__retainedImages = [...document.querySelectorAll('article img')]; }"
            )
            await page.get_by_role("button", name="Refresh preview").click()
            await page.wait_for_function("() => window.simcordPreview.pendingAction !== null")
            _BROWSER_STATUS_VALIDATOR.validate(await page.evaluate("() => window.simcordPreview"))
            await page.evaluate("() => window.__releaseRefresh()")
            await page.wait_for_function(
                "(revision) => window.simcordPreview.pendingAction === null"
                " && window.simcordPreview.publishedRevision > revision",
                arg=before["publishedRevision"],
            )
            await page.evaluate(
                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
            )
            assert (await page.evaluate("() => window.simcordPreview"))["ready"] is False
            assert await page.locator("#preview-app").get_attribute("aria-busy") == "true"
            assert await page.evaluate("() => window.__retainedImages.every(image => image.isConnected)")
            await page.evaluate("() => window.__releaseAssets()")
            await page.wait_for_function("() => window.simcordPreview.ready")
            status = await page.evaluate("() => window.simcordPreview")
            _BROWSER_STATUS_VALIDATOR.validate(status)
            assert status["complete"] is True
            assert await page.locator("article img").evaluate_all(
                "images => images.length === 2 && images.every(image => image.complete && image.naturalWidth === 2)"
            )
            await page.locator("#viewer-picker").select_option(str(bob.id))
            await page.wait_for_function(
                "(id) => window.simcordPreview.viewerId === id && window.__heldAssets === 4",
                arg=str(bob.id),
            )
            assert (await page.evaluate("() => window.simcordPreview"))["ready"] is False
            assert await page.evaluate("() => window.__retainedImages.every(image => !image.isConnected)")
            await page.evaluate("() => window.__releaseAssets()")
            await page.wait_for_function("() => window.simcordPreview.ready")
            status = await page.evaluate("() => window.simcordPreview")
            _BROWSER_STATUS_VALIDATOR.validate(status)
            assert status["complete"] is True
            assert await page.locator("article img").evaluate_all(
                "images => images.length === 2 && images.every(image => image.complete && image.naturalWidth === 2)"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_browser_keeps_oldest_active_failure_after_diagnostic_history_overflow(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    native = env.bot.get_channel(channel.id)
    messages = [
        await native.send(
            embed=discord.Embed().set_image(url=f"https://assets.example.test/missing-{index}.png")
        )
        for index in range(21)
    ]
    async with (
        env.preview(channel, viewers=[alice], layout="channel") as preview,
        async_playwright() as playwright,
    ):
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(preview.url)
            await page.wait_for_function("() => window.simcordPreview?.ready")
            assert await page.locator(".media-unavailable").count() == 21
            status = await page.evaluate("() => window.simcordPreview")
            _BROWSER_STATUS_VALIDATOR.validate(status)
            assert status["complete"] is False
            for message in messages[1:]:
                await message.delete()
            await page.get_by_role("button", name="Refresh preview").click()
            await page.wait_for_function(
                "(id) => window.simcordPreview.ready && window.simcordPreview.projectedMessageIds.length === 1"
                " && window.simcordPreview.projectedMessageIds[0] === id",
                arg=str(messages[0].id),
            )
            status = await page.evaluate("() => window.simcordPreview")
            _BROWSER_STATUS_VALIDATOR.validate(status)
            assert await page.locator(".media-unavailable").count() == 1
            assert status["complete"] is False
            assert any(
                item["code"] == "media-unavailable" and item["complete"] is False
                for item in status["diagnostics"]
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_retained_premium_icon_failure_remains_incomplete(env, channel, alice):
    from playwright.async_api import async_playwright

    view = discord.ui.View()
    view.add_item(discord.ui.Button(sku_id=101))
    message = await env.bot.get_channel(channel.id).send(view=view)
    icon_url = "https://example.test/premium.png"
    async with (
        env.preview(
            channel,
            viewers=[alice],
            layout="channel",
            assets={icon_url: ("premium.png", png_bytes())},
            sku_presentations={
                "101": {"name": "Provided plan", "price_text": "$5", "locale": "en-US", "icon_url": icon_url}
            },
        ) as preview,
        async_playwright() as playwright,
    ):
        await preview.show(message)
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.add_init_script("""(() => {
              const decode = HTMLImageElement.prototype.decode;
              HTMLImageElement.prototype.decode = function() {
                if (!this.classList.contains("premium-button-icon")) return decode.call(this);
                window.__pendingIcon = this;
                return new Promise((resolve, reject) => {
                  window.__failIcon = () => reject(new Error("decode failed"));
                });
              };
            })()""")
            await page.goto(preview.url)
            await page.wait_for_function("() => window.__failIcon && !window.simcordPreview.ready")
            before = await page.evaluate("() => window.simcordPreview.publishedRevision")
            await page.get_by_role("button", name="Refresh preview").click()
            await page.wait_for_function(
                "(revision) => !window.simcordPreview.pendingAction"
                " && window.simcordPreview.publishedRevision > revision",
                arg=before,
            )
            assert await page.evaluate("() => window.__pendingIcon.isConnected")
            await page.evaluate("() => window.__failIcon()")
            await page.wait_for_function("() => window.simcordPreview.ready")
            status = await page.evaluate("() => window.simcordPreview")
            _BROWSER_STATUS_VALIDATOR.validate(status)
            assert status["complete"] is False
            assert any(
                item["code"] == "premium-sku-icon-unavailable" and item["state"] == "current"
                for item in status["diagnostics"]
            )
            assert await page.locator(".premium-button-icon").count() == 0
        finally:
            await browser.close()
