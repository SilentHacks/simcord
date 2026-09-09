import json

import discord
import pytest
from aiohttp import ClientSession, FormData

import simcord
from fixtures.sample_bot import create_bot
from fixtures.sample_bot.interactions import AssignView


@pytest.mark.asyncio
async def test_preview_public_flow_and_at_most_once(env, channel, alice):
    await alice.slash(channel, "panel")

    async with env.preview(channel, viewers=[alice]) as preview:
        assert preview.url.startswith("http://127.0.0.1:")
        assert "#" in preview.url
        page = preview._python
        state = preview.page_payload(page)
        assert state["selected"]["content"] == "Panel"

        body = {
            "sequence": 1,
            "request_id": "preview-click",
            "generation": page.generation,
            "bot_generation": env._generation,
            "kind": "click",
            "custom_id": "persistent:ping",
        }
        first = await preview.action("python", body)
        replay = await preview.action("python", body)
        assert first["dispatched"] is True
        assert replay == first
        assert [item["content"] for item in preview.page_payload(page)["messages"]][-1] == "pong"

    await preview.close()
    await preview.wait_closed()
    assert preview._server.port is None
    assert env._preview is None


@pytest.mark.asyncio
async def test_preview_eager_validation_and_dm_access(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))

    invalid = (
        ({"viewers": []}, "viewers must be a non-empty sequence"),
        ({"viewers": [alice, alice]}, "viewers must be unique"),
        ({"viewers": [env.create_user("outsider")]}, "guild previews require members"),
        ({"viewers": [alice], "theme": "blue"}, "theme must be"),
        ({"viewers": [alice], "width": True}, "width must be"),
        ({"viewers": [alice], "height": 0}, "height must be"),
        ({"viewers": [alice], "locale": "xx"}, "unsupported locale"),
        ({"viewers": [alice], "timezone": "Mars/Olympus"}, "unsupported timezone"),
        ({"viewers": [alice], "assets": {"u": "not-a-tuple"}}, "assets must map"),
    )
    for options, message in invalid:
        with pytest.raises(simcord.SetupError, match=message):
            env.preview(channel, **options)
    assert env._preview is None
    await alice.send_dm("open the DM")
    dm = alice.user.dm_channel
    with pytest.raises(simcord.SetupError, match="DM preview requires"):
        env.preview(dm, viewers=[bob])
    async with env.preview(dm, viewers=[alice.user]) as preview:
        payload = preview.page_payload(preview._python)
        assert payload["channel"]["guildId"] is None
        assert payload["viewerId"] == str(alice.id)


@pytest.mark.asyncio
async def test_preview_rejects_cross_env_handles(env, channel, alice):
    async with simcord.run(create_bot()) as other:
        other_guild = other.create_guild()
        other_channel = other_guild.create_text_channel("other")
        other_alice = other_guild.add_member(other.create_user("other-alice"))
        with pytest.raises(simcord.SetupError, match="channel must belong"):
            env.preview(other_channel, viewers=[alice])
        with pytest.raises(simcord.SetupError, match="same-Env"):
            env.preview(channel, viewers=[other_alice])


@pytest.mark.asyncio
async def test_preview_show_refresh_delete_and_access_revocation(env, channel, alice):
    await alice.slash(channel, "panel")
    message = channel.last_message
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        assert preview.page_payload(page)["selected"]["id"] == str(message.id)

        await message.delete()
        await preview.refresh()
        assert preview.page_payload(page)["selected"] is None

        fresh = await alice.slash(channel, "panel")
        await preview.show(fresh.response)
        cached = env.bot.get_channel(channel.id)
        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await cached.set_permissions(member, view_channel=False)
        await preview.refresh()
        assert preview.page_payload(page)["status"] == "access_denied"
        result = await preview.action(
            "python",
            {
                "sequence": 1,
                "request_id": "revoked",
                "generation": page.generation,
                "bot_generation": env._generation,
                "kind": "click",
                "custom_id": "persistent:ping",
            },
        )
        assert result["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_select_modal_and_result_states(env, channel, alice):
    selected = await alice.slash(channel, "color")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(selected.response)
        page = preview._python
        base = {
            "generation": page.generation,
            "bot_generation": env._generation,
        }
        result = await preview.action(
            "python",
            {
                **base,
                "sequence": 1,
                "request_id": "pick",
                "kind": "select",
                "custom_id": "color",
                "values": ["red"],
            },
        )
        assert result["dispatched"] is True
        assert channel.last_message.content == "Picked red"

        result = await preview.action(
            "python",
            {
                **base,
                "sequence": 2,
                "request_id": "dupe",
                "kind": "select",
                "custom_id": "color",
                "values": ["red", "red"],
            },
        )
        assert result["dispatched"] is False
        with pytest.raises(simcord.SetupError, match="action sequence has a gap"):
            await preview.action(
                "python",
                {**base, "sequence": 4, "request_id": "gap", "kind": "refresh"},
            )
        with pytest.raises(simcord.SetupError, match="action sequence is stale"):
            await preview.action(
                "python",
                {**base, "sequence": 1, "request_id": "old", "kind": "refresh"},
            )

    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        body = {
            "sequence": 1,
            "request_id": "modal",
            "generation": page.generation,
            "bot_generation": env._generation,
            "kind": "modal_submit",
            "modal_handle": page.modal_handle,
            "values": {"name": "Ada"},
        }
        result = await preview.action("python", body)
        assert result["settlement"] == "settled"
        assert channel.last_message.content == "Thanks Ada"
        stale = await preview.action("python", {**body, "sequence": 2, "request_id": "stale"})
        assert stale["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_http_security_and_limits(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        headers = {"X-Simcord-Capability": preview.capability}
        response = await client.get(
            preview.origin + "/app.js", headers={"Host": f"localhost:{preview._server.port}"}
        )
        assert response.status == 200
        assert response.headers["X-Frame-Options"] == "DENY"
        response = await client.get(
            preview.origin + "/missing", headers={"Host": f"localhost:{preview._server.port}"}
        )
        assert response.status == 404
        response = await client.post(
            preview.origin + "/api/pages", headers=headers, json={"viewer_id": "999999999999"}
        )
        assert response.status == 400
        response = await client.get(preview.origin + "/", headers={"Host": "evil.invalid"})
        assert response.status == 403
        response = await client.post(preview.origin + "/api/pages", headers={})
        assert response.status == 401
        response = await client.post(
            preview.origin + "/api/pages",
            headers={**headers, "Origin": "https://evil.invalid"},
            json={"viewer_id": str(alice.id)},
        )
        assert response.status == 401
        response = await client.post(preview.origin + "/api/pages", headers=headers, json=[])
        assert response.status == 400

        page_response = await client.post(
            preview.origin + "/api/pages", headers=headers, json={"viewer_id": str(alice.id)}
        )
        page = await page_response.json()
        context_headers = {**headers, "X-Simcord-Context": page["context"]["id"]}
        response = await client.get(
            preview.origin + "/api/assets/missing",
            headers={"X-Simcord-Capability": "wrong", "X-Simcord-Context": "wrong"},
        )
        assert response.status == 401
        response = await client.post(preview.origin + "/api/action", headers=headers, json={})
        assert response.status == 400
        response = await client.post(
            preview.origin + "/api/action",
            headers={**context_headers, "Content-Type": "application/json"},
            data=b"{",
        )
        assert response.status == 400
        multipart = FormData()
        multipart.add_field("payload", json.dumps({"kind": "refresh"}), content_type="application/json")
        response = await client.post(preview.origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 400
        response = await client.post(
            preview.origin + "/api/action",
            headers={**context_headers, "Content-Type": "application/json"},
            data=b"x" * (256 * 1024 + 1),
        )
        assert response.status == 413
        response = await client.post(preview.origin + "/api/action", headers=context_headers, data=b"[]")
        assert response.status == 400

        multipart = FormData()
        multipart.add_field("payload", "{")
        response = await client.post(preview.origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 400
        multipart = FormData()
        multipart.add_field(
            "payload",
            json.dumps(
                {
                    "sequence": 1,
                    "request_id": "multipart",
                    "generation": page["context"]["generation"],
                    "bot_generation": env._generation,
                    "kind": "refresh",
                    "values": {},
                }
            ),
            content_type="application/json",
        )
        multipart.add_field("file:extra", b"upload", filename="extra.txt")
        response = await client.post(preview.origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 200
        assert (await response.json())["settlement"] == "settled"
        multipart = FormData()
        multipart.add_field("file:name", b"x" * (10 * 1024 * 1024 + 1), filename="x.bin")
        response = await client.post(preview.origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 413

        response = await client.delete(
            preview.origin + f"/api/pages/{page['context']['id']}", headers=context_headers
        )
        assert response.status == 200
        response = await client.get(preview.origin + "/api/state", headers=context_headers)
        assert response.status == 410


@pytest.mark.asyncio
async def test_preview_pages_keep_viewers_and_reject_stale_generation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    await alice.slash(channel, "panel")

    async with env.preview(channel, viewers=[alice, bob]) as preview:
        headers = {"X-Simcord-Capability": preview.capability}
        async with ClientSession() as client:
            response = await client.post(
                preview.origin + "/api/pages", headers=headers, json={"viewer_id": str(alice.id)}
            )
            assert response.status == 200
            alice_page = await response.json()
            response = await client.post(
                preview.origin + "/api/pages", headers=headers, json={"viewer_id": str(bob.id)}
            )
            assert response.status == 200
            bob_page = await response.json()

            assert alice_page["viewerId"] == str(alice.id)
            assert bob_page["viewerId"] == str(bob.id)
            assert alice_page["context"]["id"] != bob_page["context"]["id"]

            context = alice_page["context"]
            action_headers = {**headers, "X-Simcord-Context": context["id"]}
            switch = {
                "sequence": 1,
                "request_id": "switch-viewer",
                "generation": context["generation"],
                "bot_generation": env._generation,
                "kind": "viewer",
                "viewer_id": str(bob.id),
            }
            response = await client.post(preview.origin + "/api/action", headers=action_headers, json=switch)
            assert response.status == 200
            switched = await response.json()
            assert switched["dispatched"] is False

            stale = {**switch, "sequence": 2, "request_id": "stale", "kind": "refresh"}
            response = await client.post(preview.origin + "/api/action", headers=action_headers, json=stale)
            assert response.status == 400

            other_headers = {**headers, "X-Simcord-Context": bob_page["context"]["id"]}
            response = await client.get(preview.origin + "/api/state", headers=other_headers)
            assert response.status == 200
            other = await response.json()
            assert other["viewerId"] == str(bob.id)


@pytest.mark.asyncio
async def test_preview_candidates_focus_pages_and_failure_states(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    role = env.guild.create_role("Readers")
    assign = await alice.slash(channel, "assign")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(assign.response)
        page = preview._python
        payload = preview.page_payload(page)
        assert {item["kind"] for item in payload["candidates"]["who"]} == {"user"}
        assert {item["kind"] for item in payload["candidates"]["role"]} == {"role"}
        assert {item["kind"] for item in payload["candidates"]["chan"]} == {"channel"}

        base = {"generation": page.generation, "bot_generation": env._generation}
        for sequence, custom_id, value in (
            (1, "who", [str(bob.id)]),
            (2, "role", [str(role.id)]),
            (3, "chan", [str(channel.id)]),
        ):
            result = await preview.action(
                "python",
                {
                    **base,
                    "sequence": sequence,
                    "request_id": custom_id,
                    "kind": "select",
                    "custom_id": custom_id,
                    "values": value,
                },
            )
            assert result["dispatched"] is True
        assert "Channel general" in channel.last_message.content
        rejected = await preview.action(
            "python",
            {**base, "sequence": 4, "request_id": "bad", "kind": "select", "custom_id": "who"},
        )
        assert rejected["dispatched"] is False
        rejected = await preview.action(
            "python",
            {
                **base,
                "sequence": 5,
                "request_id": "bad-option",
                "kind": "select",
                "custom_id": "who",
                "values": ["999999999999"],
            },
        )
        assert rejected["dispatched"] is False
        with pytest.raises(simcord.SetupError, match="action sequence has a gap"):
            await preview.action(
                "python",
                {**base, "sequence": 7, "request_id": "gap", "kind": "refresh"},
            )
        focused = await alice.slash(channel, "panel")
        focus = await preview.action(
            "python",
            {
                **base,
                "sequence": 6,
                "request_id": "focus",
                "kind": "focus",
                "target_id": str(focused.response.id),
            },
        )
        assert focus["dispatched"] is False
        page = preview._python
        assert preview.page_payload(page)["targetId"] == str(focused.response.id)

        for _ in range(16):
            preview.open_page(alice.id)
        with pytest.raises(simcord.SetupError, match="page limit"):
            preview.open_page(alice.id)
        with pytest.raises(simcord.SetupError, match="cannot be closed"):
            preview.close_page("python")


@pytest.mark.asyncio
async def test_preview_click_error_timeout_and_close_action(env, channel, alice):
    errors = discord.ui.View()
    boom = discord.ui.Button(label="Boom", custom_id="boom")
    timeout = discord.ui.Button(label="Timeout", custom_id="timeout")

    async def fail(_interaction):
        raise RuntimeError("callback exploded")

    async def expire(_interaction):
        raise TimeoutError("callback timed out")

    boom.callback = fail
    timeout.callback = expire
    errors.add_item(boom)
    errors.add_item(timeout)
    message = await env.bot.get_channel(channel.id).send(content="controls", view=errors)

    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        base = {"generation": page.generation, "bot_generation": env._generation}
        failed = await preview.action(
            "python",
            {**base, "sequence": 1, "request_id": "boom", "kind": "click", "custom_id": "boom"},
        )
        assert failed["dispatched"] is True
        assert failed["acknowledgement"] == "unacknowledged"

        timed = await preview.action(
            "python",
            {**base, "sequence": 2, "request_id": "timeout", "kind": "click", "custom_id": "timeout"},
        )
        assert timed["dispatched"] is True
        assert timed["acknowledgement"] == "unacknowledged"

        closed = await preview.action(
            "python",
            {**base, "sequence": 3, "request_id": "close", "kind": "close"},
        )
        assert closed["settlement"] == "settled"
        await preview.wait_closed()


@pytest.mark.asyncio
async def test_preview_boundary_errors_and_lazy_asset_validation(tmp_path, env, channel, alice):
    unopened = env.preview(channel, viewers=[alice])
    with pytest.raises(simcord.SetupError, match="not entered"):
        _ = unopened.url
    with pytest.raises(simcord.SetupError, match="active"):
        unopened.open_page()

    bad_url = "https://cdn.example.test/bad.png"
    embed = discord.Embed().set_image(url=bad_url)
    message = await env.bot.get_channel(channel.id).send(embed=embed)
    async with env.preview(channel, viewers=[alice], assets={bad_url: ("bad.png", b"not-image")}) as preview:
        page = preview._python
        with pytest.raises(simcord.SetupError, match="unknown"):
            preview.get_page("missing")
        with pytest.raises(simcord.SetupError, match="snowflake"):
            preview.open_page(target_id="bad")
        with pytest.raises(simcord.SetupError, match="expects"):
            await preview.show(object())
        with pytest.raises(simcord.SetupError, match="unavailable"):
            preview.open_page(target_id="999999999999")
        with pytest.raises(simcord.SetupError, match="unavailable"):
            await preview.screenshot(tmp_path / "bad.png", target=999999999999)
        with pytest.raises(simcord.SetupError, match="filesystem-like"):
            await preview.screenshot(object())
        with pytest.raises(simcord.SetupError, match="boolean"):
            await preview.screenshot(tmp_path / "bad.png", allow_incomplete=1)
        with pytest.raises(simcord.SetupError, match="capture target"):
            await preview.screenshot(tmp_path / "bad.png", target=object())
        await preview.show(message)
        asset = preview.page_payload(page)["selected"]["embeds"][0]["image"]["asset_id"]
        with pytest.raises(simcord.SetupError, match="valid PNG"):
            await preview.prepare_asset("python", asset)
        with pytest.raises(simcord.SetupError, match="asset is unavailable"):
            preview.asset("python", "missing")
    await unopened.close()
    with pytest.raises(simcord.SetupError, match="not active"):
        await unopened.screenshot(tmp_path / "closed.png")


@pytest.mark.asyncio
async def test_preview_ephemeral_filter_and_action_validation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    ephemeral = await alice.context_menu(channel, "Report Member", bob)
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        await preview.show(ephemeral.response)
        alice_payload = preview.page_payload(preview._python)
        assert alice_payload["selected"]["ephemeral"] is True
        bob_page = preview.open_page(bob.id, target_id=ephemeral.response.id)
        assert preview.page_payload(bob_page)["selected"] is None

        base = {"generation": preview._python.generation, "bot_generation": env._generation}
        with pytest.raises(simcord.SetupError, match="action must be an object"):
            await preview.action("python", [])
        with pytest.raises(simcord.SetupError, match="positive integer"):
            await preview.action("python", {**base, "sequence": True, "request_id": "bad"})
        with pytest.raises(simcord.SetupError, match="non-empty string"):
            await preview.action("python", {**base, "sequence": 1, "request_id": ""})
        with pytest.raises(simcord.SetupError, match="unknown preview action"):
            await preview.action("python", {**base, "sequence": 1, "request_id": "kind", "kind": "unknown"})
        with pytest.raises(simcord.SetupError, match="generation is stale"):
            await preview.action(
                "python",
                {**base, "sequence": 1, "request_id": "generation", "generation": 999, "kind": "refresh"},
            )
        with pytest.raises(simcord.SetupError, match="bot generation is stale"):
            await preview.action(
                "python",
                {**base, "sequence": 1, "request_id": "bot", "bot_generation": 999, "kind": "refresh"},
            )

        body = {**base, "sequence": 1, "request_id": "refresh", "kind": "refresh"}
        settled = await preview.action("python", body)
        assert settled["settlement"] == "settled"
        assert await preview.action("python", body) == settled
        with pytest.raises(simcord.SetupError, match="conflicts"):
            await preview.action("python", {**body, "kind": "focus"})

    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        invalid = await preview.action(
            "python",
            {
                "sequence": 1,
                "request_id": "bad-modal",
                "generation": page.generation,
                "bot_generation": env._generation,
                "kind": "modal_submit",
                "modal_handle": page.modal_handle,
                "values": {"unknown": "x"},
            },
        )
        assert invalid["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_show_rejects_foreign_channel_and_modal_opener(env, channel, alice):
    other_channel = env.guild.create_text_channel("other")
    foreign_result = await alice.slash(other_channel, "panel")
    feedback = await alice.slash(channel, "feedback")

    async with env.preview(channel, viewers=[alice]) as preview:
        with pytest.raises(simcord.SetupError, match="another Env or channel"):
            await preview.show(foreign_result)

    bob = env.guild.add_member(env.create_user("bob"))
    async with env.preview(channel, viewers=[bob]) as preview:
        with pytest.raises(simcord.SetupError, match="opener"):
            await preview.show(feedback)


@pytest.mark.asyncio
async def test_preview_dm_entity_candidates_and_select(env, alice):
    await alice.send_dm("start")
    dm = alice.user.dm_channel
    message = await (await env.bot.fetch_channel(dm.id)).send(content="pick", view=AssignView())
    async with env.preview(dm, viewers=[alice.user]) as preview:
        await preview.show(message)
        page = preview._python
        payload = preview.page_payload(page)
        assert payload["candidates"]["who"][0]["id"] == str(alice.id)
        base = {"generation": page.generation, "bot_generation": env._generation}
        result = await preview.action(
            "python",
            {
                **base,
                "sequence": 1,
                "request_id": "dm-user",
                "kind": "select",
                "custom_id": "who",
                "values": [str(alice.id)],
            },
        )
        assert result["dispatched"] is True
        assert dm.last_message.content == "Picked alice"
