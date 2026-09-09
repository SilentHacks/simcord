import io

import discord
import pytest
from aiohttp import ClientSession, FormData
from PIL import Image

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
        headers = {
            "X-Simcord-Capability": preview.capability,
            "X-Simcord-Context": "python",
        }
        async with ClientSession() as client:
            async with client.get(preview.origin + "/api/state", headers=headers) as response:
                assert response.status == 200
                state = await response.json()
        assert state["selected"]["id"] == str(message.id)
        asset_id = state["selected"]["embeds"][0]["image"]["asset_id"]
        content_type, body, filename = await preview.prepare_asset("python", asset_id)
        assert (content_type, body, filename) == ("text/plain", b"hello", "note.txt")
        assert await preview.prepare_asset("python", asset_id) == ("text/plain", b"hello", "note.txt")

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
        asset = preview.page_payload(preview._python)["selected"]["embeds"][0]["image"]["asset_id"]
        preview._retained_media_bytes = preview._MAX_MEDIA_BYTES
        with pytest.raises(simcord.SetupError, match="budget exceeded after normalization"):
            await preview.prepare_asset("python", asset)

    monkeypatch.setattr(simcord.Preview, "_MAX_MEDIA_BYTES", 1)
    async with env.preview(channel, viewers=[alice], assets={url: ("budget.png", body)}) as preview:
        await preview.show(message)
        image = preview.page_payload(preview._python)["selected"]["embeds"][0]["image"]
        assert image["available"] is False
        assert preview._python.assets[image["asset_id"]]["diagnostic"] == "session media budget exceeded"


@pytest.mark.asyncio
async def test_preview_public_focus_and_entity_select_errors(env, channel, alice):
    empty = env.guild.create_text_channel("empty")
    async with env.preview(empty, viewers=[alice]) as preview:
        headers = {
            "X-Simcord-Capability": preview.capability,
            "X-Simcord-Context": "python",
        }
        async with ClientSession() as client:
            async with client.get(preview.origin + "/api/state", headers=headers) as response:
                state = await response.json()
        result = await preview.action(
            state["context"]["id"],
            {
                "sequence": 1,
                "request_id": "missing-focus",
                "generation": state["context"]["generation"],
                "bot_generation": state["botGeneration"],
                "kind": "focus",
                "target_id": None,
            },
        )
        assert result["dispatched"] is False

    view = discord.ui.View()
    view.add_item(discord.ui.UserSelect(custom_id="member"))
    view.add_item(discord.ui.ChannelSelect(custom_id="channel"))
    message = await env.bot.get_channel(channel.id).send(view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        headers = {
            "X-Simcord-Capability": preview.capability,
            "X-Simcord-Context": "python",
        }
        async with ClientSession() as client:
            async with client.get(preview.origin + "/api/state", headers=headers) as response:
                state = await response.json()
        base = {
            "generation": state["context"]["generation"],
            "bot_generation": state["botGeneration"],
        }
        invalid_user = await preview.action(
            "python",
            {
                **base,
                "sequence": 1,
                "request_id": "bad-member-id",
                "kind": "select",
                "custom_id": "member",
                "values": ["not-a-snowflake"],
            },
        )
        assert invalid_user["dispatched"] is False
        invalid_channel = await preview.action(
            "python",
            {
                **base,
                "sequence": 2,
                "request_id": "bad-channel-id",
                "kind": "select",
                "custom_id": "channel",
                "values": ["not-a-channel"],
            },
        )
        assert invalid_channel["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_public_multipart_unknown_part(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview:
        headers = {
            "X-Simcord-Capability": preview.capability,
            "X-Simcord-Context": "python",
        }
        form = FormData()
        form.add_field("payload", "{}")
        form.add_field("note", "ignored")
        async with ClientSession() as client:
            async with client.post(preview.origin + "/api/action", headers=headers, data=form) as response:
                assert response.status == 400


@pytest.mark.asyncio
async def test_preview_public_page_limit_and_http_size_limits(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        base = {"X-Simcord-Capability": preview.capability}
        headers = {**base, "X-Simcord-Context": "python"}
        for _ in range(preview._MAX_PAGES):
            preview.open_page()
        with pytest.raises(simcord.SetupError, match="page limit"):
            preview.open_page()

        response = await client.post(preview.origin + "/api/action", headers=headers, data=b"")
        assert response.status == 400
        response = await client.post(
            preview.origin + "/api/action",
            headers={"X-Simcord-Capability": "wrong", "X-Simcord-Context": "python"},
            json={},
        )
        assert response.status == 401

        form = FormData()
        form.add_field("payload", b"{" + b" " * (256 * 1024), filename="payload.json")
        response = await client.post(preview.origin + "/api/action", headers=headers, data=form)
        assert response.status == 413
        form = FormData()
        form.add_field("payload", b"not-json", filename="payload.json")
        response = await client.post(preview.origin + "/api/action", headers=headers, data=form)
        assert response.status == 400

        form = FormData()
        form.add_field("payload", "{}")
        for index in range(11):
            form.add_field(f"note-{index}", b"x", filename=f"{index}.txt")
        response = await client.post(preview.origin + "/api/action", headers=headers, data=form)
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
        base = {"generation": page.generation, "bot_generation": env._generation}
        cases = (
            {"custom_id": "choice", "values": ["one", "one"]},
            {"custom_id": "missing", "values": ["one"]},
            {"custom_id": "place", "values": [str(channel.id)]},
            {"custom_id": "place", "values": ["not-an-id"]},
        )
        for sequence, values in enumerate(cases, 1):
            result = await preview.action(
                "python",
                {
                    **base,
                    "sequence": sequence,
                    "request_id": f"select-{sequence}",
                    "kind": "select",
                    **values,
                },
            )
            assert result["dispatched"] is False
            assert result["settlement"] == "settled"
