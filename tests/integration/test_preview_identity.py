import asyncio
import io

import discord
import pytest
from aiohttp import ClientSession
from preview_helpers import png_bytes, preview_headers, target_message

from simcord.backend.cdn import CDN_BASE
from simcord.preview._markdown import markdown_summary, markdown_tokens


@pytest.mark.asyncio
async def test_preview_identity_precedence_role_color_and_avatar_assets(env, channel, alice):
    user = env.create_user("identity-user", global_name="Global Name", avatar="user-hash")
    low = env.guild.create_role("Low", color=0x112233, position=2)
    high = env.guild.create_role("High", color=0x445566, position=8)
    member = env.guild.add_member(user, roles=[low, high], nick="Guild Nick")
    message = await member.send(channel, "identity")
    avatar_url = f"{CDN_BASE}/avatars/{user.id}/user-hash.png"

    async with env.preview(
        channel,
        viewers=[alice],
        assets={avatar_url: ("avatar.png", png_bytes())},
    ) as preview:
        await preview.show(message)
        author = target_message(preview._page_payload(preview._python))["author"]
        assert author["name"] == "Guild Nick"
        assert author["global_name"] == "Global Name"
        assert author["avatar_kind"] == "custom"
        assert author["avatar_available"] is True
        assert author["role_color"] == 0x445566
        assert author["role_id"] == str(high.id)
        assert author["presence"] is None


@pytest.mark.asyncio
async def test_preview_webhook_override_and_default_avatar_identity(env, channel, alice):
    hook = env.guild.create_webhook(channel, "Build Hook")
    override_url = "https://assets.example.test/webhook.png"
    message = await hook.send("webhook", username="Per-message", avatar_url=override_url)
    plain = await alice.send(channel, "default avatar")

    async with env.preview(
        channel,
        viewers=[alice],
        assets={override_url: ("webhook.png", png_bytes())},
    ) as preview:
        await preview.show(message)
        author = target_message(preview._page_payload(preview._python))["author"]
        assert author["name"] == "Per-message"
        assert author["webhook"] is True
        assert author["avatar_kind"] == "webhook"
        assert author["avatar_available"] is True
        await preview.show(plain)
        default_author = target_message(preview._page_payload(preview._python))["author"]
        assert default_author["avatar_kind"] == "default"


@pytest.mark.asyncio
async def test_preview_avatar_authorization_copied_url_and_replacement(env, channel, alice):
    first = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(png_bytes()), filename="one.png")
    )
    copied = await env.bot.get_channel(channel.id).send(
        embed=discord.Embed().set_image(url=first.attachments[0].url)
    )
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        await preview.show(copied)
        selected = target_message(preview._page_payload(preview._python))
        assert selected["embeds"][0]["image"]["available"] is False

        await preview.show(first)
        selected = target_message(preview._page_payload(preview._python))
        asset_id = selected["attachments"][0]["asset_id"]
        headers = preview_headers(preview, "python")
        response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
        assert response.status == 200
        await response.read()
        original = env.backend.cdn.get(first.attachments[0].url)
        assert original is not None
        from PIL import Image

        replacement = io.BytesIO()
        Image.new("RGBA", (2, 2), (60, 40, 20, 255)).save(replacement, format="PNG")
        replacement_bytes = replacement.getvalue()
        assert len(replacement_bytes) == len(original)
        env.backend.cdn._blobs[first.attachments[0].url] = replacement_bytes
        response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
        assert response.status == 404

        await preview.refresh()
        replacement_id = target_message(preview._page_payload(preview._python))["attachments"][0]["asset_id"]
        assert replacement_id != asset_id
        response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
        assert response.status == 404
        response = await client.get(
            preview._origin + f"/api/assets/{replacement_id}?download=1", headers=headers
        )
        assert response.status == 200
        assert await response.read() == replacement_bytes
        retained = preview._retained_media_bytes
        await preview.refresh()
        assert (
            target_message(preview._page_payload(preview._python))["attachments"][0]["asset_id"]
            == replacement_id
        )
        assert preview._retained_media_bytes == retained


@pytest.mark.asyncio
async def test_preview_two_viewers_see_only_authorized_identity_assets(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob", avatar="private"))
    private_url = f"{CDN_BASE}/avatars/{bob.id}/private.png"
    message = await bob.send(channel, "private identity")
    async with env.preview(
        channel,
        viewers=[alice, bob],
        assets={private_url: ("private.png", png_bytes())},
    ) as preview:
        await preview.show(message)
        bob_page = preview._open_page(bob.id, target_id=message.id)
        bob_author = target_message(preview._page_payload(bob_page))["author"]
        assert bob_author["id"] == str(bob.id)
        await env.bot.get_channel(channel.id).set_permissions(
            env.bot.get_guild(env.guild.id).get_member(bob.id), view_channel=False
        )
        await env.settle()
        denied = preview._page_payload(bob_page)
        assert denied["status"] == "access_denied"
        assert denied["entities"] == {}
        assert denied["assets"] == {}


@pytest.mark.asyncio
async def test_browser_revocation_during_asset_fetch_cannot_publish_old_pixels(env, channel, alice):
    from playwright.async_api import async_playwright

    bob = env.guild.add_member(env.create_user("bob"))
    message = await env.bot.get_channel(channel.id).send(
        "authorized image", file=discord.File(io.BytesIO(png_bytes()), filename="image.png")
    )
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        await preview.show(message)
        blocked = asyncio.Event()
        release = asyncio.Event()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                alice_page = await browser.new_page()

                async def delay_asset(route):
                    blocked.set()
                    await release.wait()
                    await route.continue_()

                await alice_page.route("**/api/assets/**", delay_asset)
                await alice_page.goto(preview.url)
                await asyncio.wait_for(blocked.wait(), 10)
                bob_page = await browser.new_page()
                await bob_page.goto(preview.url)
                await bob_page.wait_for_function("() => window.simcordPreview?.ready === true")
                await bob_page.locator("#viewer-picker").select_option(str(bob.id))
                await bob_page.wait_for_function(
                    "(id) => window.simcordPreview?.viewerId === id && window.simcordPreview?.ready",
                    arg=str(bob.id),
                )
                assert await bob_page.locator(".attachment-image").evaluate(
                    "image => image.complete && image.naturalWidth > 0"
                )

                member = env.bot.get_guild(env.guild.id).get_member(alice.id)
                await env.bot.get_channel(channel.id).set_permissions(member, view_channel=False)
                await env.settle()
                await preview.refresh()
                await alice_page.wait_for_function("() => window.simcordPreview?.authorized === false")
                release.set()
                await alice_page.wait_for_function("() => !document.querySelector('img[src^=\"blob:\"]')")
                assert await alice_page.locator("#focused-content").inner_text() == ""
                assert await bob_page.evaluate("() => window.simcordPreview?.authorized") is True
                assert await bob_page.locator(".attachment-image").evaluate(
                    "image => image.complete && image.naturalWidth > 0"
                )
            finally:
                release.set()
                await browser.close()


def test_preview_summary_hides_spoilers_and_keeps_graphemes_complete():
    tokens = markdown_tokens("visible ||private 👨‍👩‍👧|| 👩‍🚒xy")

    summary = markdown_summary(tokens)
    assert "private" not in summary
    assert "👨‍👩‍👧" not in summary
    assert "[spoiler]" in summary

    truncated = markdown_summary(markdown_tokens("👩‍🚒xy"), max_graphemes=2)
    assert truncated == "👩‍🚒…"

    assert markdown_summary(markdown_tokens("visible"), max_graphemes=0) == ""
    with pytest.raises(ValueError, match="max_graphemes"):
        markdown_summary(markdown_tokens("visible"), max_graphemes=-1)
