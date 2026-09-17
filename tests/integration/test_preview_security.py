import io

import pytest

pytest.importorskip("PIL")

import discord
from aiohttp import ClientSession
from PIL import Image
from preview_helpers import gif_bytes, png_bytes, preview_headers

from simcord.preview._markdown import markdown_tokens


@pytest.mark.asyncio
async def test_preview_snapshot_filters_entities_references_links_and_assets(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    role = env.guild.create_role("Readers")
    referenced = await alice.send(channel, "referenced")
    image_url = "https://cdn.example.test/picture.png"
    embed = discord.Embed(
        title="[safe](https://example.test) [unsafe](javascript:alert(1))",
        description="<script>alert(1)</script> **body**",
        url="javascript:alert(2)",
    )
    embed.add_field(name="[field](https://example.test)", value="||hidden||")
    embed.set_image(url=image_url)
    safe_embed = discord.Embed(title="safe", url="https://example.test/embed")
    safe_embed.set_thumbnail(url=image_url)
    reply = await env.bot.get_channel(channel.id).send(
        content=f"hello <@{bob.id}> <@999999999999> <@&{role.id}> <@&{env.guild.id}>",
        embeds=[embed, safe_embed],
        reference=referenced,
        file=discord.File(io.BytesIO(png_bytes()), filename="upload.png"),
    )
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.TextDisplay("**v2**"))
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://upload.png")))
    v2 = await env.bot.get_channel(channel.id).send(
        view=view, file=discord.File(io.BytesIO(png_bytes()), filename="upload.png")
    )

    async with env.preview(
        channel,
        viewers=[alice],
        assets={image_url: ("picture.png", png_bytes())},
    ) as preview:
        await preview.show(reply)
        selected = preview.page_payload(preview._python)["selected"]
        assert selected["reference"]["message_id"] == str(referenced.id)
        assert selected["mention_user_ids"] == [str(bob.id)]
        assert selected["mention_role_ids"] == [str(role.id)]
        assert "url" not in selected["embeds"][0]
        assert selected["embeds"][0]["image"]["available"] is True
        assert selected["embeds"][0]["image"]["asset_id"]
        assert selected["embeds"][1]["url"] == "https://example.test/embed"
        assert selected["embeds"][1]["thumbnail"]["asset_id"]
        assert selected["attachments"][0]["available"] is True
        assert selected["attachments"][0]["asset_id"]
        assert "token" not in selected

        async with ClientSession() as client:
            headers = preview_headers(preview, preview._python.id)
            asset_id = selected["embeds"][0]["image"]["asset_id"]
            response = await client.get(preview.origin + f"/api/assets/{asset_id}", headers=headers)
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("image/png")
            assert await response.read() == png_bytes()
            response = await client.get(preview.origin + "/api/assets/not-an-asset", headers=headers)
            assert response.status == 404

        await preview.show(v2)
        v2_selected = preview.page_payload(preview._python)["selected"]
        assert v2_selected["components_v2"] is True
        media = v2_selected["components"][1]["items"][0]["media"]
        assert media["available"] is True
        assert media["asset_id"]
        assert v2_selected["components"][0]["markdown_tokens"]


@pytest.mark.parametrize(
    "profile", ["message", "text_display", "embed_title", "embed_description", "label", "unknown"]
)
def test_preview_markdown_safe_profiles_and_tokens(profile):
    tokens = markdown_tokens(
        "**bold** `code` [ok](https://example.test/x) [bad](javascript:alert(1)) "
        "![image](https://example.test/x.png) ||secret|| <t:123:R>\nnext",
        profile,
    )
    flat = str(tokens)
    links = [
        item for item in tokens[0]["children"] if isinstance(item, dict) and item.get("type") == "link_open"
    ]
    assert all(item["href"].startswith(("http://", "https://", "mailto:")) for item in links)
    assert "code" in flat
    assert "timestamp" in flat
    assert "secret" in flat


def test_preview_markdown_links_breaks_styles_and_spoilers():
    tokens = markdown_tokens(
        "[safe](https://example.test) [mail](mailto:test@example.test) "
        "[bad](javascript:alert(1))  \nhard **bold** ~~strike~~ __underline__ ||secret||",
    )
    children = tokens[0]["children"][0]["children"]
    links = [item for item in children if item.get("type") == "link_open"]
    assert [item["href"] for item in links] == ["https://example.test", "mailto:test@example.test"]
    assert any(item.get("type") == "break" for item in children)
    kinds = {item.get("type") for item in children}
    assert {
        "s_open",
        "s_close",
        "strong_open",
        "strong_close",
        "u_open",
        "u_close",
        "spoiler_open",
        "spoiler_close",
    } <= kinds
    assert "secret" in str(tokens)


def test_preview_markdown_non_text_is_safe_plain_text():
    assert markdown_tokens(None) == []
    assert markdown_tokens(123, "message")[0]["children"][0]["children"][0]["content"] == "123"


@pytest.mark.asyncio
async def test_delete_python_page_is_rejected_not_an_error(env, channel, alice):
    """The reserved python context maps to 400, not an untranslated 500."""
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        response = await client.delete(
            preview.origin + "/api/pages/python", headers=preview_headers(preview, "python")
        )
        assert response.status == 400
        assert "python" in (await response.text()).lower()


@pytest.mark.asyncio
async def test_pages_body_over_limit_is_rejected(env, channel, alice):
    """/api/pages enforces the same 256 KiB body cap as /api/action."""
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        oversized = b'{"viewer_id":"' + b"0" * (256 * 1024) + b'"}'
        response = await client.post(
            preview.origin + "/api/pages",
            headers={**preview_headers(preview, "python"), "Content-Type": "application/json"},
            data=oversized,
        )
        assert response.status == 413


@pytest.mark.asyncio
async def test_deleted_channel_snapshot_reports_access_denied(env, channel, alice):
    """Deleting the previewed channel denies access instead of surfacing BackendError."""
    message = await env.bot.get_channel(channel.id).send("before delete")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        await env.bot.get_channel(channel.id).delete()
        await env.settle()
        await preview.refresh()
        payload = preview.page_payload(preview._python)
        assert payload["status"] == "access_denied"
        assert payload["messages"] == []
        assert payload["selected"] is None
        assert payload["channelId"] == str(channel.id)
        assert payload["channel"]["name"] is None


@pytest.mark.asyncio
async def test_asset_download_serves_original_bytes(env, channel, alice):
    """?download=1 returns the original bytes as an attachment; the default normalizes."""
    original = gif_bytes()
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(original), filename="anim.gif")
    )
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        await preview.show(message)
        asset_id = preview.page_payload(preview._python)["selected"]["attachments"][0]["asset_id"]
        display = await client.get(
            preview.origin + f"/api/assets/{asset_id}", headers=preview_headers(preview, "python")
        )
        assert display.status == 200
        assert "inline" in display.headers["Content-Disposition"]
        with Image.open(io.BytesIO(await display.read())) as image:
            assert image.format == "PNG"
            assert getattr(image, "n_frames", 1) == 1
        download = await client.get(
            preview.origin + f"/api/assets/{asset_id}?download=1",
            headers=preview_headers(preview, "python"),
        )
        assert download.status == 200
        assert "attachment" in download.headers["Content-Disposition"]
        assert "anim.gif" in download.headers["Content-Disposition"]
        assert await download.read() == original
