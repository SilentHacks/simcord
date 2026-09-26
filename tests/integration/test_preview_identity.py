import io

import discord
import pytest
from aiohttp import ClientSession
from preview_helpers import png_bytes, preview_headers, target_message

from simcord.backend.cdn import CDN_BASE


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
        env.backend.cdn._blobs[first.attachments[0].url] = png_bytes() + b"replacement"
        response = await client.get(preview._origin + f"/api/assets/{asset_id}", headers=headers)
        assert response.status == 404


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
