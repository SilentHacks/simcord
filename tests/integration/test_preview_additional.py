import asyncio
import sys

import discord
import pytest
from aiohttp import ClientSession, FormData
from preview_helpers import action_body, preview_headers

import simcord
from fixtures.sample_bot import create_bot
from fixtures.sample_bot.interactions import DeferEditView
from simcord.backend.models import Interaction
from simcord.preview._markdown import markdown_tokens


class _UploadModal(discord.ui.Modal, title="Upload"):
    upload = discord.ui.Label(
        text="File",
        component=discord.ui.FileUpload(custom_id="upload"),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("uploaded")


class _UploadView(discord.ui.View):
    @discord.ui.button(label="Upload", custom_id="open-upload")
    async def open_upload(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(_UploadModal())


class _BlockingView(discord.ui.View):
    @discord.ui.button(label="Block", custom_id="block")
    async def block(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await asyncio.Event().wait()


class _EntityModal(discord.ui.Modal, title="Member"):
    member = discord.ui.Label(
        text="Member",
        component=discord.ui.UserSelect(custom_id="member"),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("member submitted")


class _EntityModalView(discord.ui.View):
    @discord.ui.button(label="Member", custom_id="open-member")
    async def open_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(_EntityModal())


class _FilteredChannelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(
            discord.ui.ChannelSelect(
                custom_id="typed-channel",
                channel_types=[discord.ChannelType.text],
            )
        )


@pytest.mark.asyncio
async def test_preview_private_thread_membership_and_revocation(env, channel, alice):
    cached_parent = env.bot.get_channel(channel.id)
    thread = await cached_parent.create_thread(name="secret", type=discord.ChannelType.private_thread)
    cached_thread = env.bot.get_channel(thread.id)
    private = simcord.ChannelHandle(env, env.guild, env.backend.get_channel(thread.id))

    with pytest.raises(simcord.SetupError, match="lacks channel"):
        env.preview(private, viewers=[alice])

    await cached_thread.add_user(discord.Object(id=alice.id))
    await env.settle()
    message = await cached_thread.send("secret")

    async with env.preview(private, viewers=[alice]) as preview:
        await preview.show(message)
        assert preview._page_payload(preview._python)["selected"]["content"] == "secret"

        await cached_thread.remove_user(discord.Object(id=alice.id))
        await env.settle()
        await preview.refresh()
        assert preview._page_payload(preview._python)["status"] == "access_denied"


@pytest.mark.asyncio
async def test_preview_modal_capture_and_raster_limits(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice], width=640, height=360) as preview:
        await preview.show(feedback)
        capture = await preview.screenshot(tmp_path / "modal.png")
        assert capture.modal_id is not None
        assert capture.complete is True
        assert capture.geometry["surfaceExpanded"] is True
        assert capture.output_width > 0 and capture.output_height > 0

    oversized = env.preview(channel, viewers=[alice], width=32769, height=1)
    async with oversized:
        with pytest.raises(simcord.SetupError, match="raster exceeds"):
            await oversized.screenshot(tmp_path / "oversized.png")


@pytest.mark.asyncio
async def test_preview_capture_target_boundaries(tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    message = await env.bot.get_channel(channel.id).send(content="capture target")
    panel = await alice.slash(channel, "panel")
    feedback = await alice.slash(channel, "feedback")
    slow = await alice.slash(channel, "slow")
    bob = env.guild.add_member(env.create_user("bob"))
    ephemeral = await alice.context_menu(channel, "Report Member", bob)

    async with env.preview(channel, viewers=[alice, bob]) as preview:
        with pytest.raises(simcord.SetupError, match="presentable response"):
            await preview.screenshot(tmp_path / "slow.png", target=slow)
        with pytest.raises(simcord.SetupError, match="requested viewer"):
            await preview.screenshot(tmp_path / "modal-owner.png", viewer=bob, target=feedback)
        with pytest.raises(simcord.SetupError, match="not accessible"):
            await preview.screenshot(tmp_path / "ephemeral.png", viewer=bob, target=ephemeral.response)

        response_capture = await preview.screenshot(tmp_path / "response.png", target=panel.response)
        assert response_capture.target_id == str(panel.response.id)
        message_capture = await preview.screenshot(tmp_path / "message.png", target=message)
        assert message_capture.target_id == str(message.id)

        with pytest.raises(simcord.SetupError, match="path must be a file"):
            await preview.screenshot(tmp_path)
        with pytest.raises(simcord.SetupError, match="directory does not exist"):
            await preview.screenshot(tmp_path / "missing" / "capture.png")

        async with simcord.run(create_bot()) as other:
            guild = other.create_guild()
            other_channel = guild.create_text_channel("other")
            other_alice = guild.add_member(other.create_user("alice"))
            foreign = simcord.InteractionResult(
                other, Interaction(1, "token", other_alice.id, other_channel.id, guild.id, 1)
            )
            with pytest.raises(simcord.SetupError, match="target belongs to another Env"):
                await preview.screenshot(tmp_path / "foreign-target.png", target=foreign)
            with pytest.raises(simcord.SetupError, match="belongs to another Env"):
                await preview.screenshot(tmp_path / "foreign-viewer.png", viewer=other_alice)


@pytest.mark.asyncio
async def test_preview_deferred_action_and_multipart_limits(env, channel, alice):
    message = await env.bot.get_channel(channel.id).send(content="slow", view=DeferEditView())
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        await preview.show(message)
        page = preview._python
        result = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="defer",
                published_revision=page.revision,
                custom_id="slow_edit",
            ),
        )
        assert result["dispatched"] is True
        assert result["acknowledgement"] == "deferred"
        assert channel.last_message.content == "edited in place"

        headers = preview_headers(preview, page.id)
        form = FormData()
        form.add_field("file:upload", b"without envelope", filename="x.txt")
        response = await client.post(preview._origin + "/api/action", headers=headers, data=form)
        assert response.status == 400

        form = FormData()
        form.add_field("payload", "x" * (256 * 1024 + 1))
        response = await client.post(preview._origin + "/api/action", headers=headers, data=form)
        assert response.status == 413

        form = FormData()
        for index in range(12):
            form.add_field(f"file:{index}", b"x", filename=f"{index}.txt")
        response = await client.post(preview._origin + "/api/action", headers=headers, data=form)
        assert response.status == 413
        blob = b"x" * (8 * 1024 * 1024 + 524288)
        form = FormData()
        form.add_field("payload", '{"values":{}}', content_type="application/json")
        for index in range(3):
            form.add_field(f"file:{index}", blob, filename=f"{index}.bin")
        response = await client.post(preview._origin + "/api/action", headers=headers, data=form)
        assert response.status == 413

        response = await client.get(
            preview._origin + "/api/state",
            headers={**preview_headers(preview), "Host": "evil.invalid"},
        )
        assert response.status == 401


@pytest.mark.asyncio
async def test_preview_action_busy_cancellation_and_pending_replay(env, channel, alice):
    message = await env.bot.get_channel(channel.id).send(content="block", view=_BlockingView())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        body = action_body(
            page,
            "click",
            1,
            request_id="blocked",
            published_revision=page.revision,
            custom_id="block",
        )
        task = asyncio.create_task(preview._action("python", body))
        await asyncio.sleep(0)
        busy = await preview._action(
            "python",
            action_body(
                page,
                "click",
                2,
                request_id="busy",
                published_revision=page.revision,
                custom_id="block",
            ),
        )
        assert busy["rejected"] is True
        assert busy["diagnostics"][0]["code"] == "busy"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert page.status == "stale"
        # Cancelled results are retained: replaying the consumed sequence
        # returns the recorded outcome rather than re-dispatching.
        replayed = await preview._action("python", body)
        assert replayed["settlement"] == "cancelled"


@pytest.mark.asyncio
async def test_preview_modal_file_upload_validation_and_dispatch(env, channel, alice):
    message = await env.bot.get_channel(channel.id).send(content="upload", view=_UploadView())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        opened = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="open-upload",
                published_revision=page.revision,
                custom_id="open-upload",
            ),
        )
        assert opened["dispatched"] is True
        assert page.modal is not None

        invalid = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="bad-upload",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"upload": "not-a-file-list"},
            ),
        )
        assert invalid["dispatched"] is False

        submitted = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="good-upload",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"upload": [["report.txt", b"contents"]]},
            ),
        )
        assert submitted["dispatched"] is True
        assert channel.last_message.content == "uploaded"


@pytest.mark.asyncio
async def test_preview_modal_entity_resolution_and_validation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    message = await env.bot.get_channel(channel.id).send(content="member", view=_EntityModalView())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        opened = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="open-member",
                published_revision=page.revision,
                custom_id="open-member",
            ),
        )
        assert opened["dispatched"] is True
        invalid = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="bad-member",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"member": ["999999999999"]},
            ),
        )
        assert invalid["dispatched"] is False
        submitted = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="good-member",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"member": [str(bob.id)]},
            ),
        )
        assert submitted["dispatched"] is True
        assert channel.last_message.content == "member submitted"


@pytest.mark.asyncio
async def test_preview_lifecycle_and_show_boundaries(env, channel, alice):
    unopened = env.preview(channel, viewers=[alice])
    assert unopened._origin == ""
    with pytest.raises(simcord.SetupError, match="not active"):
        await unopened.show(object())
    with pytest.raises(simcord.SetupError, match="not active"):
        await unopened.refresh()

    async with unopened as preview:
        with pytest.raises(simcord.SetupError, match="already entered"):
            await preview.__aenter__()
        with pytest.raises(simcord.SetupError, match="Only one active"):
            env.preview(channel, viewers=[alice])
        with pytest.raises(simcord.SetupError, match="unknown preview viewer"):
            preview._open_page(viewer_id="not-an-id")

        slow = await alice.slash(channel, "slow")
        with pytest.raises(simcord.SetupError, match="presentable response"):
            await preview.show(slow)
        other_channel = env.guild.create_text_channel("other")
        other_message = await env.bot.get_channel(other_channel.id).send("other")
        with pytest.raises(simcord.SetupError, match="another channel"):
            await preview.show(other_message)

    await unopened.close()
    with pytest.raises(simcord.SetupError, match="expired or unknown"):
        preview._get_page("python")


@pytest.mark.asyncio
async def test_preview_requires_message_history_permission(env, channel, alice):
    cached = env.bot.get_channel(channel.id)
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    await cached.set_permissions(member, read_message_history=False)
    await env.settle()
    with pytest.raises(simcord.SetupError, match="lacks channel"):
        env.preview(channel, viewers=[alice])

    await cached.set_permissions(member, read_message_history=True)
    await env.settle()
    async with env.preview(channel, viewers=[alice]) as preview:
        assert preview._page_payload(preview._python)["status"] == "current"


@pytest.mark.asyncio
async def test_preview_show_rejects_foreign_and_cross_channel_modals(env, channel, alice):
    other_channel = env.guild.create_text_channel("other")
    feedback = await alice.slash(other_channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        with pytest.raises(simcord.SetupError, match="another channel"):
            await preview.show(feedback)
    bob = env.guild.add_member(env.create_user("bob"))
    ephemeral = await alice.context_menu(channel, "Report Member", bob)
    async with env.preview(channel, viewers=[bob]) as preview:
        # A denied explicit target degrades to bob's own initial target
        # rather than raising — the ephemeral is simply never pinned.
        denied = preview._open_page(target_id=str(ephemeral.response.id))
        assert denied.target_id is None
        assert preview._page_payload(denied)["selected"] is None
        with pytest.raises(simcord.SetupError, match="not accessible"):
            await preview.show(ephemeral.response)
        with pytest.raises(simcord.SetupError, match="not accessible"):
            await preview.show(ephemeral.response.message)


@pytest.mark.asyncio
async def test_preview_start_failure_cleans_registration(monkeypatch, env, channel, alice):
    preview = env.preview(channel, viewers=[alice])

    async def fail_start():
        raise RuntimeError("bind failed")

    monkeypatch.setattr(preview._server, "start", fail_start)
    with pytest.raises(RuntimeError, match="bind failed"):
        await preview.__aenter__()
    assert env._preview is None


@pytest.mark.asyncio
async def test_preview_missing_assets_and_restored_private_access(env, channel, alice):
    url = "https://cdn.example.test/missing.png"
    message = await env.bot.get_channel(channel.id).send(embed=discord.Embed().set_image(url=url))
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        selected = preview._page_payload(preview._python)["selected"]
        asset_id = selected["embeds"][0]["image"]["asset_id"]
        assert selected["embeds"][0]["image"]["available"] is False
        with pytest.raises(simcord.SetupError, match="asset is unavailable"):
            preview._asset("python", asset_id)

    private_parent = env.bot.get_channel(channel.id)
    private_thread = await private_parent.create_thread(
        name="private", type=discord.ChannelType.private_thread
    )
    private_cached = env.bot.get_channel(private_thread.id)
    await private_cached.add_user(discord.Object(id=alice.id))
    await env.settle()
    private_handle = simcord.ChannelHandle(env, env.guild, env.backend.get_channel(private_thread.id))
    async with env.preview(private_handle, viewers=[alice]) as preview:
        await private_cached.send("visible")
        await preview.refresh()
        await private_cached.remove_user(discord.Object(id=alice.id))
        await env.settle()
        await preview.refresh()
        assert preview._page_payload(preview._python)["status"] == "access_denied"
        await private_cached.add_user(discord.Object(id=alice.id))
        await env.settle()
        await preview.refresh()
        assert preview._page_payload(preview._python)["status"] == "current"


@pytest.mark.asyncio
async def test_preview_snapshot_deleted_reference_embeds_and_channel_filter(env, channel, alice):
    source = await env.bot.get_channel(channel.id).send("source")
    await source.delete()
    embed = discord.Embed.from_dict(
        {
            "title": "Title",
            "description": "Description",
            "url": "https://example.test/embed",
            "video": {"url": "https://cdn.example.test/video.mp4"},
            "footer": {"text": "Footer"},
            "fields": [{"name": "Field", "value": "Value"}],
        }
    )
    reply = await env.bot.get_channel(channel.id).send(content="reply", embed=embed, reference=source)
    controls = await env.bot.get_channel(channel.id).send(view=_FilteredChannelView())
    voice = env.guild.create_voice_channel("voice")

    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(reply)
        selected = preview._page_payload(preview._python)["selected"]
        assert "reference" not in selected
        assert selected["embeds"][0]["url"] == "https://example.test/embed"
        assert selected["embeds"][0]["video"]["available"] is False
        assert selected["embeds"][0]["footer_tokens"]
        assert selected["embeds"][0]["fields"][0]["name_tokens"]

        await preview.show(controls)
        candidates = preview._page_payload(preview._python)["candidates"]["typed-channel"]
        candidate_ids = {item["id"] for item in candidates}
        assert str(channel.id) in candidate_ids
        assert str(voice.id) not in candidate_ids
        page = preview._python
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="wrong-channel-type",
                published_revision=page.revision,
                custom_id="typed-channel",
                values=[str(voice.id)],
            ),
        )
        assert rejected["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_capture_rejects_unsettled_browser_action(monkeypatch, tmp_path, env, channel, alice):
    pytest.importorskip("playwright")
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:

        async def pending_status(_page):
            return {
                "ready": True,
                "complete": True,
                "lastAction": {"settlement": "pending"},
                "diagnostics": [],
            }

        monkeypatch.setattr(preview._capture_manager, "_status", pending_status)
        with pytest.raises(simcord.SetupError, match="settled action"):
            await preview.screenshot(tmp_path / "pending.png")


@pytest.mark.asyncio
async def test_preview_capture_readiness_failure_closes_browser_objects(
    monkeypatch, tmp_path, env, channel, alice
):
    await alice.slash(channel, "panel")

    class FakePage:
        closed = False

        async def route(self, *_args):
            return None

        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_function(self, *_args, **_kwargs):
            raise RuntimeError("page did not become ready")

        async def close(self):
            self.closed = True

    class FakeContext:
        closed = False

        async def new_page(self):
            self.page = FakePage()
            return self.page

        async def close(self):
            self.closed = True

    class FakeBrowser:
        async def new_context(self, **_kwargs):
            self.context = FakeContext()
            return self.context

    browser = FakeBrowser()
    async with env.preview(channel, viewers=[alice]) as preview:

        async def ensure_browser():
            return browser

        monkeypatch.setattr(preview._capture_manager, "_ensure_browser", ensure_browser)
        with pytest.raises(simcord.SetupError, match="readiness deadline exceeded"):
            await preview.screenshot(tmp_path / "not-ready.png")
        assert browser.context.page.closed is True
        assert browser.context.closed is True


@pytest.mark.asyncio
async def test_preview_action_validation_without_target_and_bad_values(env, channel, alice):
    empty = env.guild.create_text_channel("empty")
    async with env.preview(empty, viewers=[alice]) as preview:
        page = preview._python
        result = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="no-target",
                custom_id="x",
                published_revision=page.revision,
            ),
        )
        assert result["dispatched"] is False

    assign = await alice.slash(channel, "assign")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(assign.response)
        page = preview._python
        non_string = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="non-string",
                custom_id="who",
                values=[1],
                published_revision=page.revision,
            ),
        )
        assert non_string["dispatched"] is False
        unhashable = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="unhashable",
                custom_id="who",
                values=[[]],
                published_revision=page.revision,
            ),
        )
        assert unhashable["dispatched"] is False
        unavailable = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="missing-select",
                custom_id="missing",
                values=["x"],
                published_revision=page.revision,
            ),
        )
        assert unavailable["dispatched"] is False

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
                request_id="bad-values",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values=[],
            ),
        )
        assert invalid["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_eager_validation_remaining_boundaries(env, channel, alice):
    invalid = (
        ({"viewers": None}, "non-empty sequence"),
        ({"viewers": [object()]}, "same-Env UserHandle"),
        ({"viewers": [alice.user]}, "guild previews require members"),
        ({"viewers": [alice], "timezone": ""}, "unsupported timezone"),
        ({"viewers": [alice], "assets": []}, "assets must map"),
        ({"viewers": [alice], "assets": {"u": ("name", "bytes")}}, "assets must map"),
        ({"viewers": [alice], "assets": {"u": (1, b"bytes")}}, "assets must map"),
    )
    for options, message in invalid:
        with pytest.raises(simcord.SetupError, match=message):
            env.preview(channel, **options)


def test_preview_markdown_without_optional_parser(monkeypatch):
    monkeypatch.setitem(sys.modules, "markdown_it", None)
    assert markdown_tokens("**safe**") == [
        {"type": "inline", "children": [{"type": "text", "content": "**safe**"}]}
    ]
