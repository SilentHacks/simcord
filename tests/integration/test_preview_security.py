import io
from urllib.parse import unquote

import pytest

pytest.importorskip("PIL")

import discord
from aiohttp import ClientSession
from PIL import Image
from preview_helpers import gif_bytes, png_bytes, preview_headers, target_message

from simcord.backend.cdn import CDN_BASE
from simcord.preview._markdown import markdown_summary, markdown_tokens


def _walk_tokens(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_tokens(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_tokens(child)


@pytest.mark.asyncio
async def test_forwarded_snapshot_media_belongs_to_forward_not_live_source(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    private = env.guild.create_text_channel(
        "source", overwrites={bob: discord.PermissionOverwrite(view_channel=False)}
    )
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Historical control", custom_id="source-only"))
    source = await env.bot.get_channel(private.id).send(
        "Frozen forwarded content",
        embed=discord.Embed(title="Frozen evidence"),
        file=discord.File(io.BytesIO(png_bytes()), filename="evidence.png"),
        view=view,
    )
    forwarded = await alice.forward(source, channel)
    async with env.preview(channel, viewers=[bob]) as preview:
        await preview.show(forwarded)
        projected = target_message(await preview.snapshot())
        snapshot = projected["snapshots"][0]
        assert projected["reply"]["state"] == "unavailable"
        assert "author" not in snapshot
        assert snapshot["content"] == "Frozen forwarded content"
        assert snapshot["embeds"][0]["title"] == "Frozen evidence"
        assert snapshot["components"][0]["components"][0]["disabled"]
        asset_id = snapshot["attachments"][0]["asset_id"]
        await source.edit(content="Changed live source", view=None, embed=None)
        await source.delete()
        await preview.refresh()
        assert target_message(await preview.snapshot())["snapshots"][0]["content"] == snapshot["content"]
        async with ClientSession() as client:
            url = preview._origin + f"/api/assets/{asset_id}?download=1"
            headers = preview_headers(preview, preview._python.id)
            response = await client.get(url, headers=headers)
            assert response.status == 200
            assert await response.read() == png_bytes()
            await alice.delete(forwarded)
            response = await client.get(url, headers=headers)
            assert response.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["image", "thumbnail", "video", "author", "footer", "gallery", "accessory", "sticker"],
)
async def test_forwarded_snapshot_urls_reauthorize_against_forward(env, channel, alice, kind):
    private = env.guild.create_text_channel("original-media")
    native = env.bot.get_channel(private.id)
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    data = png_bytes()
    url = f"https://media.example.test/{kind}.png"
    if kind == "sticker":
        sticker = env.guild.create_sticker("frozen")
        url = env.backend.cdn.store_sticker(sticker.id, sticker.format_type, data)
        native_sticker = await env.bot.get_guild(env.guild.id).fetch_sticker(sticker.id)
        source = await native.send(stickers=[native_sticker])
    elif kind in {"gallery", "accessory"}:
        view = discord.ui.LayoutView()
        if kind == "gallery":
            view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(url)))
        else:
            view.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay("Retained thumbnail"),
                    accessory=discord.ui.Thumbnail(url),
                )
            )
        source = await native.send(view=view)
    else:
        field = {"name": "Frozen", "icon_url": url} if kind in {"author", "footer"} else {"url": url}
        if kind == "footer":
            field = {"text": "Frozen", "icon_url": url}
        source = await native.send(embed=discord.Embed.from_dict({kind: field}))
    forwarded = await alice.forward(source, channel)
    async with (
        env.preview(channel, viewers=[alice], assets={url: (f"{kind}.png", data)}) as preview,
        ClientSession() as client,
    ):
        await preview.show(forwarded)
        snapshot = target_message(await preview.snapshot())["snapshots"][0]
        if kind == "sticker":
            asset = snapshot["stickers"][0]
        elif kind == "gallery":
            asset = snapshot["components"][0]["items"][0]["media"]
        elif kind == "accessory":
            asset = snapshot["components"][0]["accessory"]["media"]
        else:
            asset = snapshot["embeds"][0][kind]
        prefix = "icon_" if kind in {"author", "footer"} else ""
        assert asset[f"{prefix}available"] is True
        asset_id = asset[f"{prefix}asset_id"]
        asset_url = preview._origin + f"/api/assets/{asset_id}?download=1"
        headers = preview_headers(preview, preview._python.id)
        for state in ("original", "revoked", "deleted"):
            if state == "revoked":
                await native.set_permissions(member, view_channel=False)
            elif state == "deleted":
                await source.delete()
                if kind == "sticker":
                    await native_sticker.delete()
            await preview.refresh()
            response = await client.get(asset_url, headers=headers)
            assert response.status == 200
            assert await response.read() == data
        destination = env.bot.get_channel(channel.id)
        await destination.set_permissions(member, view_channel=False)
        response = await client.get(asset_url, headers=headers)
        assert response.status == 404
        await destination.set_permissions(member, overwrite=None)
        await alice.delete(forwarded)
        response = await client.get(asset_url, headers=headers)
        assert response.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("copied_kind", ["attachment", "sticker"])
async def test_forwarded_copied_cdn_urls_do_not_transfer_media_ownership(env, channel, alice, copied_kind):
    data = png_bytes()
    if copied_kind == "sticker":
        sticker = env.guild.create_sticker("unrelated")
        url = env.backend.cdn.store_sticker(sticker.id, sticker.format_type, data)
    else:
        unrelated = await env.bot.get_channel(channel.id).send(
            file=discord.File(io.BytesIO(data), filename="unrelated.png")
        )
        url = unrelated.attachments[0].url
    source = await env.bot.get_channel(channel.id).send(embed=discord.Embed().set_image(url=url))
    forwarded = await alice.forward(source, channel)
    async with (
        env.preview(channel, viewers=[alice], assets={url: ("copied.png", data)}) as preview,
        ClientSession() as client,
    ):
        await preview.show(forwarded)
        asset = target_message(await preview.snapshot())["snapshots"][0]["embeds"][0]["image"]
        assert asset["available"] is False
        response = await client.get(
            preview._origin + f"/api/assets/{asset['asset_id']}?download=1",
            headers=preview_headers(preview, preview._python.id),
        )
        assert response.status == 404


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
async def test_preview_renders_markdown_spoilers_highlight_and_inert_html_in_browser(env, channel, alice):
    from playwright.async_api import async_playwright

    styles = "tTdDfFR"
    content = (
        " ".join(f"<t:1700000000:{style}>" for style in styles)
        + "\nfirst\n-# quiet line\nthird"
        + "\n||[masked](https://example.test) `inline secret`\n-# small spoiler middle\nnormal spoiler last||"
        + " and ||same paragraph spoiler||\n\n||separate paragraph spoiler||"
        + "\n```python\ndef hello():\n    return 1\n```\n<script>window.previewInjected = true</script>"
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
                subtext = page.locator(".message-content .markdown-subtext")
                assert await subtext.all_text_contents() == ["quiet line", "small spoiler middle"]
                assert "-# quiet line" not in await page.locator(".message-content").inner_text()
                code = page.locator(".code-block code")
                assert await code.text_content() == "def hello():\n    return 1\n"
                assert await code.locator(".hljs-title.function_").count() > 0
                paragraph = page.locator(".message-content .markdown-paragraph").first
                spoiler_fragments = paragraph.locator(".markdown-spoiler")
                assert await spoiler_fragments.count() == 4
                spoiler = spoiler_fragments.first
                spoiler_content = spoiler.locator(".markdown-spoiler-content")
                link = spoiler_content.locator("a.markdown-link")
                inline_code = spoiler_content.locator("code.inline-code")
                assert await spoiler_fragments.evaluate_all(
                    """elements => elements.every(element => {
                      const content = element.querySelector('.markdown-spoiler-content');
                      return content.inert && content.getAttribute('aria-hidden') === 'true'
                        && getComputedStyle(content).visibility === 'hidden';
                    })"""
                )
                separate_spoiler = (
                    page.locator(".message-content .markdown-paragraph").nth(1).locator(".markdown-spoiler")
                )
                assert await separate_spoiler.locator(".markdown-spoiler-content").evaluate(
                    "element => element.inert && element.getAttribute('aria-hidden') === 'true'"
                )
                assert await inline_code.text_content() == "inline secret"
                assert not await link.evaluate(
                    "element => { element.focus(); return document.activeElement === element; }"
                )
                await spoiler.focus()
                await page.keyboard.press("Enter")
                assert await spoiler_fragments.evaluate_all(
                    """elements => elements.slice(0, 3).every(element => {
                      const content = element.querySelector('.markdown-spoiler-content');
                      return element.classList.contains('is-revealed') && !content.inert
                        && !content.hasAttribute('aria-hidden');
                    }) && !elements[3].classList.contains('is-revealed')
                      && elements[3].querySelector('.markdown-spoiler-content').inert"""
                )
                assert await separate_spoiler.locator(".markdown-spoiler-content").evaluate(
                    "element => element.inert && element.getAttribute('aria-hidden') === 'true'"
                )
                assert await spoiler.evaluate("element => element.classList.contains('is-revealed')")
                assert await spoiler_content.evaluate(
                    "element => !element.inert && !element.hasAttribute('aria-hidden')"
                )
                assert await link.get_attribute("href") == "https://example.test/"
                assert await link.get_attribute("target") == "_blank"
                assert await link.get_attribute("rel") == "noopener noreferrer"
                assert await link.evaluate("element => getComputedStyle(element).visibility !== 'hidden'")
                assert await inline_code.evaluate(
                    "element => getComputedStyle(element).visibility !== 'hidden'"
                )
                assert await link.evaluate(
                    "element => { element.focus(); return document.activeElement === element; }"
                )
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
    children = list(_walk_tokens(tokens))
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


def test_preview_markdown_spoilers_respect_parsed_code_and_escaped_pipes():
    source = "||before `a||b` after||"
    tokens = markdown_tokens(source)
    children = list(_walk_tokens(tokens))

    assert any(token.get("type") == "code" and token.get("content") == "a||b" for token in children)
    assert markdown_summary(tokens) == "[spoiler]"
    escaped = markdown_tokens(r"\||literal||")
    assert not any(token.get("type", "").startswith("spoiler_") for token in _walk_tokens(escaped))
    incomplete = markdown_tokens("||incomplete")
    assert markdown_summary(incomplete) == "||incomplete"
    assert not any(token.get("type", "").startswith("spoiler_") for token in _walk_tokens(incomplete))


def test_preview_subtext_marker_applies_per_line_across_boundaries():
    formatted = "||**normal first\r\n-# small middle\r\nnormal last**||"
    cases = (
        ("first\n-# second\nthird", ["second"], ["first", "third"]),
        ("-# first\nsecond", ["first"], ["second"]),
        ("-# first\n-# second", ["first", "second"], []),
        ("first\n\n-# second", ["second"], ["first"]),
        ("first\r\n-# second\r\nthird", ["second"], ["first", "third"]),
        (
            "first\n-# ||small first\nnormal continuation||\nlast",
            ["small first"],
            ["first", "normal continuation", "last"],
        ),
        (
            "||normal first\n-# small middle\nnormal last||",
            ["small middle"],
            ["normal first", "normal last"],
        ),
        (formatted, ["small middle"], ["normal first", "normal last"]),
    )
    for profile in ("message", "text_display"):
        for source, expected_subtext, expected_inline in cases:
            tokens = markdown_tokens(source, profile)
            lines = [block for block in _walk_tokens(tokens) if block.get("type") in {"inline", "subtext"}]

            def text(block):
                return "".join(
                    child.get("content", "")
                    for child in block.get("children", [])
                    if child.get("type") == "text"
                )

            assert [text(block) for block in lines if block.get("type") == "subtext"] == expected_subtext
            inline_text = [text(block) for block in lines if block.get("type") == "inline"]
            assert [value for value in inline_text if value] == expected_inline
            visible = "".join(
                token.get("content", "") for token in _walk_tokens(tokens) if token.get("type") == "text"
            )
            assert "-#" not in visible
    formatted_lines = [
        block for block in _walk_tokens(markdown_tokens(formatted)) if block.get("type") == "subtext"
    ]
    assert {"strong_open", "strong_close"} <= {child["type"] for child in formatted_lines[0]["children"]}
    assert markdown_summary(markdown_tokens("||normal first\n-# small middle\nnormal last||")) == "[spoiler]"


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
        assert preview._python is preview._pages["python"]


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
async def test_access_revocation_redacts_cached_projection_without_refresh(env, channel, alice):
    message = await env.bot.get_channel(channel.id).send("private history")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        await preview.show(message)
        cached_channel = env.bot.get_channel(channel.id)
        await cached_channel.edit(topic="private topic")
        await preview.refresh()
        await cached_channel.set_permissions(
            env.bot.get_guild(env.guild.id).get_member(alice.id), view_channel=False
        )
        response = await client.get(
            preview._origin + "/api/state", headers=preview_headers(preview, "python")
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["status"] == "access_denied"
        assert payload["channel"]["topic"] is None
        assert payload["channel"]["canSendMessages"] is False
        assert payload["history"] == {
            "hasBefore": False,
            "hasAfter": False,
            "windowStartId": None,
            "windowEndId": None,
        }
        assert payload["messageIndex"] == [] and payload["messages"] == {}
        assert payload["navigation"]["hasPrevious"] is False
        assert payload["navigation"]["hasNext"] is False
        assert payload["targetId"] is None
        assert payload["assets"] == {} and payload["modal"] is None and payload["candidates"] == {}


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


def test_preview_markdown_linkify_handles_balanced_urls_unicode_paths_and_masked_links():
    source = (
        "https://example.com/a_(b)). https://example.com/路径?q=猫 "
        "[first](https://first.example.test)[second](https://second.example.test)"
    )
    links = [token for token in _walk_tokens(markdown_tokens(source)) if token.get("type") == "link_open"]
    hrefs = [unquote(token["href"]) for token in links]

    assert hrefs == [
        "https://example.com/a_(b)",
        "https://example.com/路径?q=猫",
        "https://first.example.test",
        "https://second.example.test",
    ]

    subtext = markdown_tokens("-# note https://example.com")
    subtext_tokens = list(_walk_tokens(subtext))
    assert [token["href"] for token in subtext_tokens if token.get("type") == "link_open"] == [
        "https://example.com"
    ]
    assert "".join(token.get("content", "") for token in subtext_tokens if token.get("type") == "text") == (
        "note https://example.com"
    )


def test_preview_markdown_linkify_keeps_escaped_code_literal_and_unsafe_text_unlinked():
    escaped = r"https\://escaped.example"
    source = (
        f"{escaped} https://safe.example.test ftp://files.example.test "
        "mailto:user@example.test //relative.example.test bare.example.test "
        "name@example.test javascript:alert(1)"
    )
    tokens = list(_walk_tokens(markdown_tokens(source)))
    links = [token for token in tokens if token.get("type") == "link_open"]

    assert [token["href"] for token in links] == ["https://safe.example.test"]
    escaped_text = "".join(token.get("content", "") for token in tokens if token.get("type") == "text")
    assert "https://escaped.example" in escaped_text

    code = list(
        _walk_tokens(markdown_tokens("`https://inline.example` \n```text\nhttps://fenced.example\n```"))
    )
    assert not any(token.get("type") == "link_open" for token in code)
    assert any(
        token.get("type") == "code" and token.get("content") == "https://inline.example" for token in code
    )
    assert any(
        token.get("type") == "code_block" and "https://fenced.example" in token.get("content", "")
        for token in code
    )
    literal = list(_walk_tokens(markdown_tokens("https://literal.example", "label")))
    assert not any(token.get("type") == "link_open" for token in literal)

    title = list(_walk_tokens(markdown_tokens("https://title.example", "embed_title")))
    assert not any(token.get("type") == "link_open" for token in title)
