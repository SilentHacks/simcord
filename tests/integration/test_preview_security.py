import io

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("PIL")

import discord
from aiohttp import ClientSession
from PIL import Image

from simcord.preview._markdown import markdown_tokens


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (2, 2), (20, 40, 60, 255)).save(output, format="PNG")
    return output.getvalue()


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
        file=discord.File(io.BytesIO(_png()), filename="upload.png"),
    )
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.TextDisplay("**v2**"))
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://upload.png")))
    v2 = await env.bot.get_channel(channel.id).send(
        view=view, file=discord.File(io.BytesIO(_png()), filename="upload.png")
    )

    async with env.preview(
        channel,
        viewers=[alice],
        assets={image_url: ("picture.png", _png())},
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
            headers = {
                "X-Simcord-Capability": preview.capability,
                "X-Simcord-Context": preview._python.id,
            }
            asset_id = selected["embeds"][0]["image"]["asset_id"]
            response = await client.get(preview.origin + f"/api/assets/{asset_id}", headers=headers)
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("image/png")
            assert await response.read() == _png()
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
