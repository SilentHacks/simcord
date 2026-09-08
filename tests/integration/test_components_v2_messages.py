import io

import discord
import pytest

import simcord
from simcord.http import router


async def test_layout_view_round_trips_through_discord_models(env, channel):
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.TextDisplay("hello <@123>"))

    message = await env.bot.get_channel(channel.id).send(view=view)
    await env.settle()

    assert message.content == ""
    assert message.flags.components_v2
    assert message.components[0].content == "hello <@123>"
    assert message.components[0].id == 1


async def test_v2_flag_assigns_ids_to_action_row_only_layout(env, channel):
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.ActionRow(discord.ui.Button(label="Go", custom_id="go", id=0), id=0))

    message = await env.bot.get_channel(channel.id).send(view=view)

    assert message.components[0].id == 1
    assert message.components[0].children[0].id == 2


async def test_component_assertions_match_real_message(env, channel):
    view = discord.ui.LayoutView()
    row = discord.ui.ActionRow(discord.ui.Button(label="Go", custom_id="go"))
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("status"), row))

    message = await env.bot.get_channel(channel.id).send(view=view)

    simcord.assert_message(message, component_text="status", component_custom_id="go")


def test_invalid_v2_edit_does_not_mutate_message(env, channel):
    message = env.backend.create_message(channel.id, env.backend.bot_user.id, "keep")

    with pytest.raises(simcord.BackendError) as caught:
        router.dispatch(
            env.backend,
            "PATCH",
            f"/channels/{channel.id}/messages/{message.id}",
            json={"content": "keep", "components": [{"type": 10, "content": "v2"}], "flags": 32768},
        )

    assert caught.value.status == 400
    assert env.backend.get_message(channel.id, message.id).content == "keep"
    assert env.backend.get_message(channel.id, message.id).components == []


def test_incoming_webhook_component_rules(env, channel):
    webhook = env.backend.create_webhook(channel.id, "hook", env.backend.bot_user.id)
    path = f"/webhooks/{webhook.id}/{webhook.token}"
    interactive = [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Go", "custom_id": "go"}]}]

    ignored = router.dispatch(env.backend, "POST", path, json={"content": "plain", "components": interactive})
    assert ignored["content"] == "plain"
    assert ignored["components"] == []

    with pytest.raises(simcord.BackendError, match="non-application webhooks"):
        router.dispatch(
            env.backend,
            "POST",
            path,
            json={"components": interactive},
            params={"with_components": True},
        )

    static = router.dispatch(
        env.backend,
        "POST",
        path,
        json={"flags": 32768, "components": [{"type": 10, "content": "status"}]},
        params={"with_components": "true"},
    )
    assert static["components"][0]["content"] == "status"


async def test_layout_media_attachment_resolves_and_reads(env, channel):
    view = discord.ui.LayoutView()
    view.add_item(
        discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://photo.png", description="photo"))
    )
    message = await env.bot.get_channel(channel.id).send(
        view=view, file=discord.File(io.BytesIO(b"png-bytes"), filename="photo.png")
    )
    await env.settle()

    item = message.components[0].items[0]
    assert item.media.url.startswith("https://cdn.simcord.invalid/")
    assert item.media.attachment_id == message.attachments[0].id
    assert await message.attachments[0].read() == b"png-bytes"


async def test_message_attachment_edits_retain_replace_and_validate_files(env, channel):
    message = await env.bot.get_channel(channel.id).send(
        file=discord.File(io.BytesIO(b"old"), filename="old.bin")
    )
    path = f"/channels/{channel.id}/messages/{message.id}"
    attachment_id = str(message.attachments[0].id)

    retained = router.dispatch(
        env.backend,
        "PATCH",
        path,
        json={"attachments": [{"id": attachment_id, "filename": "renamed.bin"}]},
    )
    assert retained["attachments"][0]["filename"] == "renamed.bin"

    for attachments, error in [
        ([None], "entries must be objects"),
        ([{"id": attachment_id}, {"id": attachment_id}], "duplicate id"),
        ([{"id": "missing"}], "unknown attachment id"),
        (None, None),
    ]:
        if error is None:
            cleared = router.dispatch(env.backend, "PATCH", path, json={"attachments": attachments})
            assert cleared["attachments"] == []
        else:
            with pytest.raises(simcord.BackendError, match=error):
                router.dispatch(env.backend, "PATCH", path, json={"attachments": attachments})

    upload = discord.File(io.BytesIO(b"new"), filename="new.bin")
    replaced = router.dispatch(
        env.backend,
        "PATCH",
        path,
        json={
            "attachments": [{"id": "0"}],
            "flags": 32768,
            "components": [{"type": 13, "file": {"url": "attachment://new.bin"}}],
        },
        files=[upload],
    )
    assert replaced["attachments"][0]["filename"] == "new.bin"
    assert replaced["components"][0]["file"]["attachment_id"] == replaced["attachments"][0]["id"]


def test_message_attachment_references_require_matching_uploads(env, channel):
    with pytest.raises(simcord.BackendError, match="has no uploaded file"):
        router.dispatch(
            env.backend,
            "POST",
            f"/channels/{channel.id}/messages",
            json={
                "flags": 32768,
                "components": [{"type": 13, "file": {"url": "attachment://missing.bin"}}],
            },
        )
