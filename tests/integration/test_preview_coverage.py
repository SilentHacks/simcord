import asyncio
import io

import pytest

pytest.importorskip("PIL")

import discord
from aiohttp import ClientSession, FormData
from PIL import Image
from preview_helpers import action_body, control_key, png_bytes, preview_headers, target_message

import simcord
from simcord.preview import _media
from simcord.preview._media import MediaError, MediaWorker


@pytest.mark.asyncio
async def test_preview_public_asset_and_lifecycle_contracts(env, channel, alice):
    url = "https://cdn.example.test/note.txt"
    embed = discord.Embed(title="asset")
    embed.set_image(url=url)
    message = await env.bot.get_channel(channel.id).send(embed=embed)
    preview = env.preview(
        channel,
        viewers=[alice],
        assets={url: ("note.txt", b"hello")},
    )
    with pytest.raises(simcord.SetupError, match="not entered"):
        _ = preview.url
    async with preview:
        headers = preview_headers(preview, "python")
        async with ClientSession() as client:
            async with client.get(preview._origin + "/api/state", headers=headers) as response:
                assert response.status == 200
                state = await response.json()
        target = target_message(state)
        assert target["id"] == str(message.id)
        asset_id = target["embeds"][0]["image"]["asset_id"]
        content_type, body, filename = await preview._prepare_asset("python", asset_id)
        assert (content_type, body, filename) == ("text/plain", b"hello", "note.txt")
        assert await preview._prepare_asset("python", asset_id) == ("text/plain", b"hello", "note.txt")

    worker = MediaWorker()
    with pytest.raises(MediaError, match="valid PNG"):
        await worker.validate("broken", b"not-an-image")
    with pytest.raises(MediaError, match="valid PNG"):
        await worker.validate("broken", b"not-an-image")
    await worker.close()


@pytest.mark.asyncio
async def test_preview_public_exports_and_media_cache(monkeypatch):
    assert simcord.Preview.__name__ == "Preview"
    assert simcord.PreviewCapture.__name__ == "PreviewCapture"

    worker = MediaWorker()
    output = io.BytesIO()
    Image.new("RGBA", (2, 2)).save(output, format="PNG")
    valid = await worker.validate("valid", output.getvalue())
    assert await worker.validate("valid", b"ignored") is valid
    monkeypatch.setattr(_media, "MAX_PIXELS", 1)
    with pytest.raises(MediaError, match="megapixels"):
        await worker.validate("pixels", output.getvalue())
    await worker.close()


@pytest.mark.asyncio
async def test_preview_public_media_session_budgets(monkeypatch, env, channel, alice):
    output = io.BytesIO()
    Image.new("RGBA", (2, 2)).save(output, format="PNG")
    body = output.getvalue()
    url = "https://cdn.example.test/budget.png"
    message = await env.bot.get_channel(channel.id).send(embed=discord.Embed().set_image(url=url))

    async with env.preview(channel, viewers=[alice], assets={url: ("budget.png", body)}) as preview:
        await preview.show(message)
        asset = target_message(preview._page_payload(preview._python))["embeds"][0]["image"]["asset_id"]
        preview._retained_media_bytes = preview._MAX_MEDIA_BYTES
        with pytest.raises(simcord.SetupError, match="budget exceeded after normalization"):
            await preview._prepare_asset("python", asset)

    monkeypatch.setattr(simcord.Preview, "_MAX_MEDIA_BYTES", 1)
    async with env.preview(channel, viewers=[alice], assets={url: ("budget.png", body)}) as preview:
        await preview.show(message)
        image = target_message(preview._page_payload(preview._python))["embeds"][0]["image"]
        assert image["available"] is False
        assert preview._python.assets[image["asset_id"]].diagnostic == "session media budget exceeded"


@pytest.mark.asyncio
async def test_preview_public_focus_and_entity_select_errors(env, channel, alice):
    empty = env.guild.create_text_channel("empty")
    async with env.preview(empty, viewers=[alice]) as preview:
        headers = preview_headers(preview, "python")
        async with ClientSession() as client:
            async with client.get(preview._origin + "/api/state", headers=headers) as response:
                state = await response.json()
        result = await preview._action(
            state["context"]["id"],
            action_body(
                state["context"],
                "focus",
                1,
                bot_generation=state["botGeneration"],
                request_id="missing-focus",
                target_id=None,
            ),
        )
        assert result["dispatched"] is False

    view = discord.ui.View()
    view.add_item(discord.ui.UserSelect(custom_id="member"))
    view.add_item(discord.ui.ChannelSelect(custom_id="channel"))
    message = await env.bot.get_channel(channel.id).send(view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        headers = preview_headers(preview, "python")
        async with ClientSession() as client:
            async with client.get(preview._origin + "/api/state", headers=headers) as response:
                state = await response.json()
        member_key = control_key(state, "member")
        channel_key = control_key(state, "channel")
        invalid_user = await preview._action(
            "python",
            action_body(
                state["context"],
                "select",
                1,
                bot_generation=state["botGeneration"],
                request_id="bad-member-id",
                target_id=state["targetId"],
                control_key=member_key,
                values=["not-a-snowflake"],
                published_revision=state["publishedRevision"],
            ),
        )
        assert invalid_user["dispatched"] is False
        invalid_channel = await preview._action(
            "python",
            action_body(
                state["context"],
                "select",
                1,
                bot_generation=state["botGeneration"],
                request_id="bad-channel-id",
                target_id=state["targetId"],
                control_key=channel_key,
                values=["not-a-channel"],
                published_revision=state["publishedRevision"],
            ),
        )
        assert invalid_channel["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_public_multipart_unknown_part(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview:
        headers = preview_headers(preview, "python")
        form = FormData()
        form.add_field("payload", "{}")
        form.add_field("note", "ignored")
        async with ClientSession() as client:
            async with client.post(preview._origin + "/api/action", headers=headers, data=form) as response:
                assert response.status == 400


@pytest.mark.asyncio
async def test_preview_public_page_limit_and_http_size_limits(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        headers = preview_headers(preview, "python")
        for _ in range(preview._MAX_PAGES):
            preview._open_page()
        with pytest.raises(simcord.SetupError, match="page limit"):
            preview._open_page()

        response = await client.post(preview._origin + "/api/action", headers=headers, data=b"")
        assert response.status == 400
        response = await client.post(
            preview._origin + "/api/action",
            headers={"X-Simcord-Capability": "wrong", "X-Simcord-Context": "python"},
            json={},
        )
        assert response.status == 401

        form = FormData()
        form.add_field("payload", b"{" + b" " * (256 * 1024), filename="payload.json")
        response = await client.post(preview._origin + "/api/action", headers=headers, data=form)
        assert response.status == 413
        form = FormData()
        form.add_field("payload", b"not-json", filename="payload.json")
        response = await client.post(preview._origin + "/api/action", headers=headers, data=form)
        assert response.status == 400

        form = FormData()
        form.add_field("payload", "{}")
        for index in range(11):
            form.add_field(f"note-{index}", b"x", filename=f"{index}.txt")
        response = await client.post(preview._origin + "/api/action", headers=headers, data=form)
        assert response.status == 413


@pytest.mark.asyncio
async def test_preview_public_select_validation_boundaries(env, channel, alice):
    view = discord.ui.View()
    view.add_item(
        discord.ui.Select(
            custom_id="choice",
            options=[discord.SelectOption(label="one", value="one")],
            min_values=1,
            max_values=1,
        )
    )
    view.add_item(discord.ui.ChannelSelect(custom_id="place", channel_types=[discord.ChannelType.voice]))
    message = await env.bot.get_channel(channel.id).send(view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        payload = preview._page_payload(page)
        choice_key = control_key(payload, "choice")
        place_key = control_key(payload, "place")
        cases = (
            {"control_key": choice_key, "values": ["one", "one"]},
            {"control_key": "message:missing:component:0", "values": ["one"]},
            {"control_key": place_key, "values": [str(channel.id)]},
            {"control_key": place_key, "values": ["not-an-id"]},
        )
        for sequence, values in enumerate(cases, 1):
            result = await preview._action(
                "python",
                action_body(
                    page,
                    "select",
                    1,
                    request_id=f"select-{sequence}",
                    published_revision=page.revision,
                    **values,
                ),
            )
            assert result["dispatched"] is False
            assert result["settlement"] == "rejected"


@pytest.mark.asyncio
async def test_preview_publication_inheritance_and_asset_reauthorization(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    url = "https://cdn.example.test/private.txt"
    first = await env.bot.get_channel(channel.id).send("first", embed=discord.Embed().set_image(url=url))
    await env.bot.get_channel(channel.id).send("latest")
    async with env.preview(
        channel, viewers=[alice, bob], assets={url: ("private.txt", b"private")}
    ) as preview:
        await preview.show(first)
        browser_page = preview._open_page()
        assert browser_page.target_id == first.id

        await first.edit(content="edited")
        assert target_message(preview._page_payload(preview._python))["content"] == "first"
        await preview.refresh()
        snapshot = preview._page_payload(preview._python)
        target = target_message(snapshot)
        assert target["content"] == "edited"
        old_asset = target["embeds"][0]["image"]["asset_id"]

        switched = await preview._action(
            "python",
            action_body(
                preview._python,
                "viewer",
                1,
                request_id="switch-viewer",
                viewer_id=str(bob.id),
            ),
        )
        assert switched["settlement"] == "settled"
        with pytest.raises(simcord.SetupError, match="asset is unavailable"):
            preview._asset("python", old_asset)

    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        assert preview._open_page().modal is feedback


@pytest.mark.asyncio
async def test_preview_rejected_overlap_does_not_consume_action(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_operation():
            token = env._begin_operation("held")
            started.set()
            try:
                await release.wait()
            finally:
                env._end_operation(token)

        holder = asyncio.create_task(hold_operation())
        await started.wait()
        body = action_body(preview._python, "refresh", 1, request_id="refresh")
        with pytest.raises(simcord.SetupError, match="overlaps"):
            await preview._action("python", body)
        release.set()
        await holder
        assert (await preview._action("python", body))["settlement"] == "settled"

        page = preview._python
        closed = await asyncio.wait_for(
            preview._action("python", action_body(page, "close", 2, request_id="close")),
            1,
        )
        assert closed["settlement"] == "settled"
        await asyncio.wait_for(preview.wait_closed(), 1)


@pytest.mark.asyncio
async def test_preview_media_worker_release_forgets_decoded_entries():
    worker = MediaWorker()
    body = png_bytes()
    await worker.validate("owned", body)
    assert "owned" in worker._cache
    worker.release("owned")
    assert "owned" not in worker._cache
    await worker.close()
