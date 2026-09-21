"""Regression coverage for the preview protocol fixes."""

import asyncio
import io
import threading

import pytest

pytest.importorskip("PIL")

import discord
from aiohttp import ClientSession
from PIL import Image
from preview_helpers import action_body, control_key, gif_bytes, png_bytes, preview_headers, target_message

import simcord
from simcord.components import walk_components
from simcord.preview import _media
from simcord.preview._snapshot import _project_emoji


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
            preview._asset(bob_page.id, image["asset_id"])
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
            preview._asset("python", image["asset_id"])

        # The owning message itself still serves its attachment to alice.
        await preview.show(ephemeral.response)
        own = target_message(preview._page_payload(preview._python))["attachments"][0]
        assert own["available"] is True
        assert preview._asset("python", own["asset_id"])[1] == png_bytes()


@pytest.mark.asyncio
async def test_asset_unavailable_after_owner_deleted(env, channel, alice):
    """Deletion invalidates an asset immediately and after refresh."""
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(png_bytes()), filename="pic.png")
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        asset_id = target_message(preview._page_payload(preview._python))["attachments"][0]["asset_id"]
        assert preview._asset("python", asset_id)[1] == png_bytes()
        await message.delete()
        with pytest.raises(simcord.SetupError, match="unavailable"):
            preview._asset("python", asset_id)
        await preview.refresh()
        with pytest.raises(simcord.SetupError, match="unavailable"):
            preview._asset("python", asset_id)
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
        assert gap["diagnostics"][0]["severity"] == "error"

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
async def test_display_normalizes_animation_and_download_serves_original(env, channel, alice):
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
        assert response.headers["Content-Type"] == "image/png"
        assert "inline" in response.headers["Content-Disposition"]
        body = await response.read()
        assert body != original
        with Image.open(io.BytesIO(body)) as image:
            assert image.format == "PNG"
            assert getattr(image, "n_frames", 1) == 1
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
        assert {"type": "KeyError", "message": "'guild vanished'"} in first["diagnostics"]
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
async def test_media_release_during_decode_does_not_cache_orphan(monkeypatch):
    worker = _media.MediaWorker()
    started = threading.Event()
    release = threading.Event()
    inspect = _media._inspect

    def blocked_inspect(blob):
        started.set()
        assert release.wait(1)
        return inspect(blob)

    monkeypatch.setattr(_media, "_inspect", blocked_inspect)
    task = asyncio.create_task(worker.validate("orphan", png_bytes()))
    assert await asyncio.to_thread(started.wait, 1)
    worker.release("orphan")
    release.set()
    await task
    await asyncio.sleep(0)
    assert "orphan" not in worker._cache
    await worker.close()


@pytest.mark.asyncio
async def test_custom_emoji_projection_keeps_only_opaque_asset_reference(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview:
        projected = _project_emoji(
            preview._python,
            {"id": "123", "name": "party", "url": "https://secret.example/emoji.png"},
        )
        assert projected["asset_id"].startswith("a_")
        assert projected["custom"] is True
        assert "url" not in projected
