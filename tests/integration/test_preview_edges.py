import io
import json
from pathlib import Path

import discord
import pytest
from aiohttp import ClientSession, FormData
from PIL import Image

import simcord
from simcord.backend.models import Interaction
from simcord.preview import _markdown


class _EdgeView(discord.ui.View):
    @discord.ui.button(label="go", custom_id="go")
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("clicked")

    @discord.ui.select(
        custom_id="edge-select",
        options=[discord.SelectOption(label="one", value="one")],
        min_values=1,
        max_values=1,
    )
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.send_message(select.values[0])


@pytest.mark.asyncio
async def test_preview_edges_configuration_and_page_lifecycle(env, channel, alice):
    invalid = (
        ({"viewers": object()}, "non-empty sequence"),
        ({"viewers": [alice], "assets": {1: ("x", b"x")}}, "assets must map"),
        ({"viewers": [alice], "assets": {"u": ("x",)}}, "assets must map"),
        ({"viewers": [alice], "assets": {"u": ["x", b"x"]}}, "assets must map"),
        ({"viewers": [alice], "assets": {"u": ("x", bytearray())}}, "assets must map"),
        ({"viewers": [alice], "width": 1.0}, "width must be"),
        ({"viewers": [alice], "height": False}, "height must be"),
    )
    for options, text in invalid:
        with pytest.raises(simcord.SetupError, match=text):
            env.preview(channel, **options)

    await alice.slash(channel, "panel")
    preview = env.preview(channel, viewers=[alice])
    assert preview.origin == ""
    with pytest.raises(simcord.SetupError, match="not entered"):
        _ = preview.url
    with pytest.raises(simcord.SetupError, match="not active"):
        preview.open_page()
    async with preview:
        assert preview.origin.startswith("http://127.0.0.1:")
        assert preview.url.startswith(preview.origin + "/#")
        with pytest.raises(simcord.SetupError, match="unknown preview viewer"):
            preview._viewer(None)
        with pytest.raises(simcord.SetupError, match="authorized"):
            preview._viewer("999999999")
        with pytest.raises(simcord.SetupError, match="target_id"):
            preview._target_id("bad")
        page = preview.open_page(alice.id)
        preview.close_page(page.id)
        preview.close_page(page.id)
        with pytest.raises(simcord.SetupError, match="expired or unknown"):
            preview.get_page(page.id)
        with pytest.raises(simcord.SetupError, match="cannot be closed"):
            preview.close_page("python")
    await preview.close()
    await preview.wait_closed()


@pytest.mark.asyncio
async def test_preview_edges_show_target_ownership_and_capture_target_errors(env, channel, alice, tmp_path):
    other_channel = env.guild.create_text_channel("other")
    foreign = await env.bot.get_channel(other_channel.id).send("foreign")
    await alice.slash(channel, "panel")
    slow = await alice.slash(channel, "slow")
    feedback = await alice.slash(channel, "feedback")
    async with simcord.run(__import__("fixtures.sample_bot", fromlist=["create_bot"]).create_bot()) as other:
        guild = other.create_guild()
        ch = guild.create_text_channel("other")
        result = simcord.InteractionResult(other, Interaction(1, "token", 2, ch.id, guild.id, 1))
        async with env.preview(channel, viewers=[alice]) as preview:
            with pytest.raises(simcord.SetupError, match="another Env"):
                await preview.show(result)
            with pytest.raises(simcord.SetupError, match="another channel"):
                await preview.show(foreign)
            with pytest.raises(simcord.SetupError, match="presentable response"):
                await preview.show(slow)
            await preview.show(feedback)
            with pytest.raises(simcord.SetupError, match="presentable response"):
                await preview.screenshot(tmp_path / "slow.png", target=slow)
            with pytest.raises(simcord.SetupError, match="capture target"):
                await preview.screenshot(tmp_path / "bad.png", target=object())
            with pytest.raises(simcord.SetupError, match="target is unavailable"):
                await preview.screenshot(tmp_path / "missing.png", target=999999999999)


@pytest.mark.asyncio
async def test_preview_edges_action_validation_controls_and_access(env, channel, alice):
    message = await env.bot.get_channel(channel.id).send("controls", view=_EdgeView())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        base = {"generation": page.generation, "bot_generation": env._generation}
        bads = (
            ({**base, "sequence": 1, "request_id": "click", "kind": "click", "custom_id": 1}, "custom_id"),
            (
                {
                    **base,
                    "sequence": 2,
                    "request_id": "values",
                    "kind": "select",
                    "custom_id": "edge-select",
                    "values": "x",
                },
                "values must be a list",
            ),
            (
                {
                    **base,
                    "sequence": 3,
                    "request_id": "scalar",
                    "kind": "select",
                    "custom_id": "edge-select",
                    "values": [[]],
                },
                "scalar",
            ),
            (
                {
                    **base,
                    "sequence": 4,
                    "request_id": "bounds",
                    "kind": "select",
                    "custom_id": "edge-select",
                    "values": [],
                },
                "expects",
            ),
            (
                {
                    **base,
                    "sequence": 5,
                    "request_id": "string",
                    "kind": "select",
                    "custom_id": "edge-select",
                    "values": [1],
                },
                "strings",
            ),
            (
                {
                    **base,
                    "sequence": 6,
                    "request_id": "option",
                    "kind": "select",
                    "custom_id": "edge-select",
                    "values": ["nope"],
                },
                "option",
            ),
            (
                {
                    **base,
                    "sequence": 7,
                    "request_id": "modal",
                    "kind": "modal_submit",
                    "modal_handle": "missing",
                    "values": {},
                },
                "modal is stale",
            ),
        )
        for body, text in bads:
            result = await preview.action("python", body)
            assert result["dispatched"] is False
            assert (
                text in " ".join(item["message"] for item in result["diagnostics"])
                or result["settlement"] == "settled"
            )

        backend = env.backend.get_message(channel.id, message.id)
        components = backend.components
        stack = [components]
        while stack:
            current = stack.pop()
            if isinstance(current, list):
                stack.extend(current)
            elif isinstance(current, dict):
                if current.get("custom_id") == "edge-select":
                    current["type"] = "broken"
                    break
                stack.extend(value for value in current.values() if isinstance(value, (dict, list)))
        broken = await preview.action(
            "python",
            {
                **base,
                "sequence": 8,
                "request_id": "broken",
                "kind": "select",
                "custom_id": "edge-select",
                "values": ["one"],
            },
        )
        assert broken["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_edges_action_revision_replay_and_viewer_switch(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        page = preview._python
        body = {"sequence": 1, "request_id": "refresh", "generation": page.generation, "kind": "refresh"}
        first = await preview.action("python", body)
        assert first["settlement"] == "settled"
        with pytest.raises(simcord.SetupError, match="conflicts"):
            await preview.action("python", {**body, "kind": "focus"})
        with pytest.raises(simcord.SetupError, match="stale"):
            await preview.action("python", {**body, "request_id": "old"})
        switch = await preview.action(
            "python",
            {
                "sequence": 2,
                "request_id": "viewer",
                "generation": page.generation,
                "kind": "viewer",
                "viewer_id": str(bob.id),
            },
        )
        assert switch["dispatched"] is False
        assert page.viewer.id == bob.id
        assert page.generation == 2
        focus = await preview.action(
            "python",
            {
                "sequence": 3,
                "request_id": "focus",
                "generation": page.generation,
                "kind": "focus",
                "target_id": "999999999999",
            },
        )
        assert focus["dispatched"] is False
        assert focus["settlement"] == "settled"


@pytest.mark.asyncio
async def test_preview_edges_server_authorization_json_and_multipart(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        base = {"X-Simcord-Capability": preview.capability}
        page_response = await client.post(preview.origin + "/api/pages", headers=base, json={})
        page = await page_response.json()
        context = page["context"]["id"]
        headers = {**base, "X-Simcord-Context": context}
        for path, method in (
            ("/api/state", client.get),
            (f"/api/pages/{context}", client.delete),
            ("/api/action", client.post),
        ):
            response = await method(preview.origin + path, headers={**headers, "X-Simcord-Context": "wrong"})
            assert response.status in (400, 401, 410)
        response = await client.post(preview.origin + "/api/pages", headers=base, data=b"{")
        assert response.status == 400
        response = await client.post(preview.origin + "/api/pages", headers=base, data=b"[]")
        assert response.status == 400
        response = await client.get(preview.origin + "/api/state", headers=headers)
        assert response.status == 200
        response = await client.post(preview.origin + "/api/action", headers=headers, data=b"null")
        assert response.status == 400
        form = FormData()
        form.add_field("payload", "not-json")
        response = await client.post(preview.origin + "/api/action", headers=headers, data=form)
        assert response.status == 400
        form = FormData()
        form.add_field("payload", json.dumps({"values": {}}))
        form.add_field("file:x", b"x", filename="x.txt")
        response = await client.post(preview.origin + "/api/action", headers=headers, data=form)
        assert response.status == 400
        response = await client.get(preview.origin + "/api/assets/missing", headers=headers)
        assert response.status == 404
        response = await client.get(
            preview.origin + "/api/app.js", headers={"Host": f"localhost:{preview._server.port}"}
        )
        assert response.status == 404
        await client.delete(preview.origin + f"/api/pages/{context}", headers=headers)
        assert (await client.get(preview.origin + "/api/state", headers=headers)).status == 410


@pytest.mark.asyncio
async def test_preview_edges_snapshot_entities_mentions_assets_and_v2(tmp_path, env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    role = env.guild.create_role("edge-role")
    image = io.BytesIO()
    Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(image, format="PNG")
    url = "https://cdn.example.test/edge.png"
    embed = discord.Embed(
        title="[title](https://example.test)", description="||secret||", url="javascript:bad"
    )
    embed.set_image(url=url)
    embed.set_thumbnail(url=url)
    message = await env.bot.get_channel(channel.id).send(
        content=f"hello <@{bob.id}> <@&{role.id}>",
        embed=embed,
        file=discord.File(io.BytesIO(image.getvalue()), filename="edge.png"),
    )
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.TextDisplay("**v2**"))
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(url)))
    view.add_item(
        discord.ui.Section(
            discord.ui.TextDisplay("thumbnail"),
            accessory=discord.ui.Thumbnail(url, description="edge thumbnail", spoiler=True),
        )
    )
    view.add_item(discord.ui.File("attachment://v2.png", spoiler=True))
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("spoiler container"),
            discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large),
            spoiler=True,
        )
    )
    v2_message = await env.bot.get_channel(channel.id).send(
        view=view, file=discord.File(io.BytesIO(image.getvalue()), filename="v2.png")
    )
    backend = env.backend.get_message(channel.id, message.id)
    backend.mention_user_ids = (bob.id, 999999999999)
    backend.mention_role_ids = (role.id, env.guild.id, 999999999999)
    backend.reference = {"message_id": None}
    async with env.preview(channel, viewers=[alice], assets={url: ("edge.png", image.getvalue())}) as preview:
        await preview.show(message)
        selected = preview.page_payload(preview._python)["selected"]
        assert selected["mention_names"][str(bob.id)] == "bob"
        assert str(role.id) in selected["mention_role_ids"]
        assert "reference" not in selected
        assert selected["embeds"][0]["image"]["available"] is True
        assert selected["embeds"][0]["thumbnail"]["available"] is True
        asset = selected["attachments"][0]["asset_id"]
        assert preview.asset("python", asset)[1] == image.getvalue()
        await preview.show(v2_message)
        selected = preview.page_payload(preview._python)["selected"]
        assert selected["components"][1]["items"][0]["media"]["asset_id"]
        assert selected["components"][0]["markdown_tokens"]
        assert selected["components"][2]["accessory"]["media"]["asset_id"]
        assert selected["components"][2]["accessory"]["description"] == "edge thumbnail"
        assert selected["components"][2]["accessory"]["spoiler"] is True
        assert selected["components"][3]["spoiler"] is True
        assert selected["components"][3]["file"]["available"] is True
        assert selected["components"][3]["file"]["filename"] == "v2.png"
        assert selected["components"][3]["file"]["content_type"] == "image/png"
        assert selected["components"][4]["spoiler"] is True
        assert selected["components"][4]["components"][1]["divider"] is False
        assert await preview.prepare_asset("python", asset)
        assert await preview.prepare_asset("python", asset)
        capture = await preview.screenshot(tmp_path / "v2.png", target=v2_message, allow_incomplete=True)
        assert capture.ready is True


@pytest.mark.parametrize(
    "value,profile",
    [
        ("[bad](javascript:alert(1))", "message"),
        ("`code`\nline  \n\n__under__\n```py\nfenced\n```", "message"),
        ("<b>unsafe</b>", "unknown"),
        ("<t:-10:R> ||secret||", "label"),
    ],
)
def test_preview_edges_markdown_safety(value, profile):
    tokens = _markdown.markdown_tokens(value, profile)
    flat = json.dumps(tokens)
    if "javascript" in value:
        assert not any(
            item.get("type") == "link_open"
            and item.get("attrs", {}).get("href", "").startswith("javascript:")
            for group in tokens
            for item in group.get("children", [])
        )
    if "<b>" in value:
        assert "<b>" in flat


@pytest.mark.asyncio
async def test_preview_edges_capture_live_invalidation_and_assets(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python
        page.pinned_snapshot = {"selected": {}}
        page.pinned_generation = env._generation + 1
        with pytest.raises(simcord.SetupError, match="bot restart"):
            preview.page_payload(page)
        page.pinned_generation = env._generation
        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await env.bot.get_channel(channel.id).set_permissions(member, view_channel=False)
        await env.settle()
        with pytest.raises(simcord.SetupError, match="access was revoked"):
            preview.page_payload(page)


@pytest.mark.asyncio
async def test_preview_edges_private_channel_access_branches(env, channel, alice):
    private = await env.bot.get_channel(channel.id).create_thread(
        name="edge", type=discord.ChannelType.private_thread
    )
    cached = env.bot.get_channel(private.id)
    handle = simcord.ChannelHandle(env, env.guild, env.backend.get_channel(private.id))
    with pytest.raises(simcord.SetupError, match="lacks channel"):
        env.preview(handle, viewers=[alice])
    await cached.add_user(discord.Object(id=alice.id))
    await env.settle()
    async with env.preview(handle, viewers=[alice]) as preview:
        await cached.send("private")
        await preview.refresh()
        assert preview.page_payload(preview._python)["status"] == "current"


@pytest.mark.asyncio
async def test_preview_edges_server_static_missing_file(monkeypatch, env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        original = Path.read_text

        def fail(self, *args, **kwargs):
            if self.name == "index.html":
                raise OSError("missing")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail)
        async with ClientSession() as client:
            response = await client.get(
                preview.origin + "/", headers={"Host": f"localhost:{preview._server.port}"}
            )
            assert response.status == 404
