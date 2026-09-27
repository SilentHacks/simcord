import io

import pytest

pytest.importorskip("PIL")

import discord
from aiohttp import ClientSession
from PIL import Image
from preview_helpers import gif_bytes, png_bytes, preview_headers, target_message

from simcord.backend.cdn import CDN_BASE
from simcord.preview._markdown import markdown_tokens


def _walk_tokens(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_tokens(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_tokens(child)


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
        selected = target_message(preview._page_payload(preview._python))
        assert selected["reply"]["state"] == "resolved"
        assert selected["reply"]["message_id"] == str(referenced.id)
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
            response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("image/png")
            assert await response.read() == png_bytes()
            await reply.edit(embeds=[discord.Embed().set_image(url=image_url)])
            response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
            assert response.status == 200
            assert await response.read() == png_bytes()
            preview._explicit_assets[image_url] = ("picture.png", b"replacement bytes")
            response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
            assert response.status == 404
            preview._explicit_assets[image_url] = ("picture.png", png_bytes())
            await reply.edit(
                embeds=[discord.Embed().set_image(url="https://cdn.example.test/replacement.png")]
            )
            response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
            assert response.status == 404
            response = await client.get(preview._origin + "/api/assets/not-an-asset", headers=headers)
            assert response.status == 404

        await preview.show(v2)
        v2_selected = target_message(preview._page_payload(preview._python))
        assert v2_selected["components_v2"] is True
        media = v2_selected["components"][1]["items"][0]["media"]
        assert media["available"] is True
        assert media["asset_id"]
        assert v2_selected["components"][0]["markdown_tokens"]


@pytest.mark.asyncio
async def test_preview_projects_only_authorized_custom_emoji_across_surfaces(env, channel, alice):
    emoji = env.guild.create_emoji("party")
    private_role = env.guild.create_role("Emoji access")
    private_emoji = env.guild.create_emoji("private")
    env.backend.edit_emoji(env.guild.id, private_emoji.id, {"role_ids": [private_role.id]})
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="party",
            emoji=discord.PartialEmoji(name=emoji.name, id=emoji.id, animated=emoji.animated),
        )
    )
    content = f"<:party:{emoji.id}> <:private:{private_emoji.id}>"
    message = await env.bot.get_channel(channel.id).send(content=content, view=view)
    await alice.react(message, f"party:{emoji.id}")
    url = f"{CDN_BASE}/emojis/{emoji.id}.png"

    async with env.preview(channel, viewers=[alice], assets={url: ("party.png", png_bytes())}) as preview:
        await preview.show(message)
        selected = target_message(preview._page_payload(preview._python))
        content_emoji = next(
            token["emoji"]
            for token in _walk_tokens(selected["content_tokens"])
            if token.get("type") == "emoji"
        )
        button_emoji = next(
            token["emoji"]
            for token in _walk_tokens(selected["components"])
            if isinstance(token.get("emoji"), dict) and str(token["emoji"].get("id")) == str(emoji.id)
        )
        reaction_emoji = selected["reactions"][0]["emoji"]

        assert content_emoji["available"] is True
        assert button_emoji["available"] is True
        assert reaction_emoji["available"] is True
        assert content_emoji["asset_id"] == button_emoji["asset_id"] == reaction_emoji["asset_id"]
        assert any(
            token.get("type") == "text" and f"<:private:{private_emoji.id}>" in token.get("content", "")
            for token in _walk_tokens(selected["content_tokens"])
        )

        async with ClientSession() as client:
            response = await client.get(
                preview._origin + f"/api/assets/{content_emoji['asset_id']}",
                headers=preview_headers(preview, preview._python.id),
            )
            assert response.status == 200
            assert await response.read() == png_bytes()


@pytest.mark.asyncio
async def test_preview_animated_emoji_serves_original_gif_for_animation(env, channel, alice):
    emoji = env.guild.create_emoji("dancer", animated=True)
    original = gif_bytes()
    url = f"{CDN_BASE}/emojis/{emoji.id}.gif"
    message = await alice.send(channel, f"<a:dancer:{emoji.id}>")

    async with env.preview(channel, viewers=[alice], assets={url: ("dancer.gif", original)}) as preview:
        await preview.show(message)
        selected = target_message(preview._page_payload(preview._python))
        projected = next(
            token["emoji"]
            for token in _walk_tokens(selected["content_tokens"])
            if token.get("type") == "emoji"
        )
        assert projected["animated"] is True
        assert projected["available"] is True

        async with ClientSession() as client:
            headers = preview_headers(preview, preview._python.id)
            response = await client.get(
                preview._origin + f"/api/assets/{projected['asset_id']}?download=1",
                headers=headers,
            )
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("image/gif")
            assert await response.read() == original


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
    assert ("timestamp" in flat) == (profile != "label")
    assert "secret" in flat


def test_preview_markdown_timestamp_styles_are_preserved():
    tokens = markdown_tokens(" ".join(f"<t:1700000000:{style}>" for style in "tTdDfFR"))
    styles = [
        child["style"]
        for block in tokens
        for inline in block.get("children", [])
        for child in inline.get("children", [])
        if child.get("type") == "timestamp"
    ]
    assert styles == list("tTdDfFR")


@pytest.mark.asyncio
async def test_preview_renders_timestamps_highlight_and_inert_html_in_browser(env, channel, alice):
    from playwright.async_api import async_playwright

    styles = "tTdDfFR"
    content = (
        " ".join(f"<t:1700000000:{style}>" for style in styles)
        + "\n```python\nprint('<script>')\n```\n<script>window.previewInjected = true</script>"
    )
    await env.bot.get_channel(channel.id).send(content)
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert await page.locator("time.discord-timestamp").count() == 7
                assert await page.locator("time.discord-timestamp").evaluate_all(
                    "elements => elements.every(element => Boolean(element.title))"
                )
                assert await page.locator(".code-block .hljs-built_in").count() > 0
                assert not await page.evaluate("() => window.previewInjected")
                assert await page.locator(".message-content script").count() == 0
            finally:
                await browser.close()


def test_preview_markdown_field_policies_resolve_references_only_in_bodies():
    context = {"users": {"42": "Alice"}, "commands": {"99": "launch"}}
    source = "<@42> </launch:99> **bold** [link](https://example.test)"
    message = list(_walk_tokens(markdown_tokens(source, "message", context=context)))
    title = list(_walk_tokens(markdown_tokens(source, "embed_title", context=context)))
    description = list(_walk_tokens(markdown_tokens(source, "embed_description", context=context)))

    assert {"mention", "command", "link_open"} <= {token.get("type") for token in message}
    assert not {"mention", "command", "link_open"} & {token.get("type") for token in title}
    assert "link_open" in {token.get("type") for token in description}
    assert not {"mention", "command"} & {token.get("type") for token in description}


def test_preview_markdown_code_html_and_unsafe_links_remain_inert():
    tokens = list(
        _walk_tokens(
            markdown_tokens(
                "`<@42>` <script onload=alert(1)> [bad](javascript:alert(1))",
                context={"users": {"42": "Alice"}},
            )
        )
    )

    assert any(token.get("type") == "code" and token.get("content") == "<@42>" for token in tokens)
    assert not any(token.get("type") == "mention" for token in tokens)
    assert not any(token.get("type") == "link_open" for token in tokens)
    assert any(
        token.get("type") == "text" and "<script onload=alert(1)>" in token.get("content", "")
        for token in tokens
    )


def test_preview_discord_multiline_quote_preserves_fenced_code():
    tokens = list(
        _walk_tokens(markdown_tokens(">>> quoted line\ncontinued\n```py\n>>> literal code\n```\noutside"))
    )

    assert any(token.get("type") == "blockquote" for token in tokens)
    assert any(
        token.get("type") == "code_block" and token.get("content") == ">>> literal code\n" for token in tokens
    )


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
            preview._origin + "/api/pages/python", headers=preview_headers(preview, "python")
        )
        assert response.status == 400
        assert "python" in (await response.text()).lower()


@pytest.mark.asyncio
async def test_pages_body_over_limit_is_rejected(env, channel, alice):
    """/api/pages enforces the same 256 KiB body cap as /api/action."""
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        oversized = b'{"viewer_id":"' + b"0" * (256 * 1024) + b'"}'
        response = await client.post(
            preview._origin + "/api/pages",
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
        payload = preview._page_payload(preview._python)
        assert payload["status"] == "access_denied"
        assert payload["messageIndex"] == []
        assert payload["messages"] == {}
        assert payload["targetId"] is None
        assert payload["channelId"] == str(channel.id)
        assert payload["channel"]["name"] is None


@pytest.mark.asyncio
async def test_asset_download_serves_original_bytes(env, channel, alice):
    """The animated inline display and original download preserve the GIF."""
    original = gif_bytes()
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(original), filename="anim.gif")
    )
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        await preview.show(message)
        asset_id = target_message(preview._page_payload(preview._python))["attachments"][0]["asset_id"]
        display = await client.get(
            preview._origin + f"/api/assets/{asset_id}", headers=preview_headers(preview, "python")
        )
        assert display.status == 200
        assert "inline" in display.headers["Content-Disposition"]
        with Image.open(io.BytesIO(await display.read())) as image:
            assert image.format == "GIF"
            assert image.n_frames == 2
        download = await client.get(
            preview._origin + f"/api/assets/{asset_id}?download=1",
            headers=preview_headers(preview, "python"),
        )
        assert download.status == 200
        assert "attachment" in download.headers["Content-Disposition"]
        assert "anim.gif" in download.headers["Content-Disposition"]
        assert await download.read() == original
