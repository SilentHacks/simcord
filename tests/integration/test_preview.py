import json
from datetime import UTC, datetime

import discord
import pytest
from aiohttp import ClientSession, FormData
from preview_helpers import action_body, control_key, preview_headers, target_message

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
        state = preview._page_payload(page)
        assert target_message(state)["content"] == "Panel"

        ping_key = control_key(state, "persistent:ping")
        body = action_body(
            page,
            "click",
            1,
            request_id="preview-click",
            published_revision=page.revision,
            control_key=ping_key,
        )
        first = await preview._action("python", body)
        replay = await preview._action("python", body)
        assert first["dispatched"] is True
        assert replay == first
        refreshed = preview._page_payload(page)
        assert any(item["excerpt"] == "pong" for item in refreshed["messageIndex"])
        assert set(refreshed["timeline"]) == set(refreshed["messages"])

    await preview.close()
    await preview.wait_closed()
    assert preview._server.port is None
    assert env._preview is None


@pytest.mark.asyncio
async def test_preview_presentation_time_is_deterministic_and_timezone_aware(env, channel, alice):
    naive = datetime(2030, 1, 2, 3, 4, 5)
    with pytest.raises(simcord.SetupError, match="timezone-aware"):
        env.preview(channel, viewers=[alice], presentation_time=naive)

    when = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    async with env.preview(channel, viewers=[alice], presentation_time=when) as preview:
        first = await preview.snapshot()
        await preview.refresh()
        second = await preview.snapshot()
        assert first["profile"]["presentationTime"] == when.isoformat()
        assert second["profile"]["presentationTime"] == when.isoformat()


@pytest.mark.asyncio
async def test_preview_eager_validation_and_dm_access(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))

    invalid = (
        ({"viewers": []}, "viewers must be a non-empty sequence"),
        ({"viewers": [alice, alice]}, "viewers must be unique"),
        ({"viewers": [env.create_user("outsider")]}, "guild previews require members"),
        ({"viewers": [alice], "width": True}, "width must be"),
        ({"viewers": [alice], "height": 0}, "height must be"),
        ({"viewers": [alice], "locale": "xx"}, "unsupported locale"),
        ({"viewers": [alice], "timezone": "Mars/Olympus"}, "unsupported timezone"),
        ({"viewers": [alice], "assets": {"u": "not-a-tuple"}}, "assets must map"),
    )
    for options, message in invalid:
        with pytest.raises(simcord.SetupError, match=message):
            env.preview(channel, **options)
    # There is no theme option: dark is the only rendered theme, so a
    # theme= argument is a signature error rather than a validation one.
    with pytest.raises(TypeError):
        env.preview(channel, viewers=[alice], theme="light")
    assert env._preview is None
    await alice.send_dm("open the DM")
    dm = alice.user.dm_channel
    with pytest.raises(simcord.SetupError, match="DM preview requires"):
        env.preview(dm, viewers=[bob])
    async with env.preview(dm, viewers=[alice.user]) as preview:
        payload = preview._page_payload(preview._python)
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
        assert target_message(preview._page_payload(page))["id"] == str(message.id)

        await message.delete()
        await preview.refresh()
        assert target_message(preview._page_payload(page)) is None

        fresh = await alice.slash(channel, "panel")
        await preview.show(fresh.response)
        page = preview._python
        ping_key = control_key(preview._page_payload(page), "persistent:ping")
        cached = env.bot.get_channel(channel.id)
        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await cached.set_permissions(member, view_channel=False)
        await preview.refresh()
        assert preview._page_payload(page)["status"] == "access_denied"
        result = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="revoked",
                published_revision=page.revision,
                control_key=ping_key,
            ),
        )
        assert result["dispatched"] is False
        assert result["rejected"] is True


@pytest.mark.asyncio
async def test_preview_select_modal_and_result_states(env, channel, alice):
    selected = await alice.slash(channel, "color")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(selected.response)
        page = preview._python
        color_key = control_key(preview._page_payload(page), "color")
        result = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="pick",
                control_key=color_key,
                values=["red"],
                published_revision=page.revision,
            ),
        )
        assert result["dispatched"] is True
        assert channel.last_message.content == "Picked red"

        result = await preview._action(
            "python",
            action_body(
                page,
                "select",
                2,
                request_id="dupe",
                control_key=color_key,
                values=["red", "red"],
                published_revision=page.revision,
            ),
        )
        assert result["dispatched"] is False
        gap = await preview._action("python", action_body(page, "refresh", 4, request_id="gap"))
        assert gap["rejected"] is True
        assert gap["diagnostics"][0]["code"] == "sequence-gap"
        stale = await preview._action("python", action_body(page, "refresh", 1, request_id="old"))
        assert stale["rejected"] is True
        assert stale["diagnostics"][0]["code"] == "stale-sequence"

    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        body = action_body(
            page,
            "modal_submit",
            1,
            request_id="modal",
            published_revision=page.revision,
            modal_handle=page.modal_handle,
            values={"name": "Ada"},
        )
        result = await preview._action("python", body)
        assert result["settlement"] == "settled"
        assert channel.last_message.content == "Thanks Ada"
        stale = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="stale",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"name": "Ada"},
            ),
        )
        assert stale["dispatched"] is False


@pytest.mark.asyncio
async def test_preview_http_security_and_limits(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        headers = preview_headers(preview)
        response = await client.get(
            preview._origin + "/app.js", headers={"Host": f"localhost:{preview._server.port}"}
        )
        assert response.status == 200
        assert response.headers["X-Frame-Options"] == "DENY"
        response = await client.get(
            preview._origin + "/missing", headers={"Host": f"localhost:{preview._server.port}"}
        )
        assert response.status == 404
        response = await client.post(
            preview._origin + "/api/pages", headers=headers, json={"viewer_id": "999999999999"}
        )
        assert response.status == 400
        response = await client.get(preview._origin + "/", headers={"Host": "evil.invalid"})
        assert response.status == 403
        response = await client.post(preview._origin + "/api/pages", headers={})
        assert response.status == 401
        response = await client.post(
            preview._origin + "/api/pages",
            headers={**headers, "Origin": "https://evil.invalid"},
            json={"viewer_id": str(alice.id)},
        )
        assert response.status == 401
        response = await client.post(preview._origin + "/api/pages", headers=headers, json=[])
        assert response.status == 400

        page_response = await client.post(
            preview._origin + "/api/pages", headers=headers, json={"viewer_id": str(alice.id)}
        )
        page = await page_response.json()
        context_headers = preview_headers(preview, page["context"]["id"])
        response = await client.get(
            preview._origin + "/api/assets/missing",
            headers={"X-Simcord-Capability": "wrong", "X-Simcord-Context": "wrong"},
        )
        assert response.status == 401
        response = await client.post(preview._origin + "/api/action", headers=headers, json={})
        assert response.status == 400
        response = await client.post(preview._origin + "/api/action", headers=context_headers, json={})
        assert response.status == 200
        assert (await response.json())["rejected"] is True
        response = await client.post(
            preview._origin + "/api/action",
            headers={**context_headers, "Content-Type": "application/json"},
            data=b"{",
        )
        assert response.status == 400
        multipart = FormData()
        multipart.add_field("payload", json.dumps({"kind": "refresh"}), content_type="application/json")
        response = await client.post(preview._origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 400
        response = await client.post(
            preview._origin + "/api/action",
            headers={**context_headers, "Content-Type": "application/json"},
            data=b"x" * (256 * 1024 + 1),
        )
        assert response.status == 413
        response = await client.post(preview._origin + "/api/action", headers=context_headers, data=b"[]")
        assert response.status == 200
        assert (await response.json())["rejected"] is True

        multipart = FormData()
        multipart.add_field("payload", "{")
        response = await client.post(preview._origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 400
        multipart = FormData()
        multipart.add_field(
            "payload",
            json.dumps(
                action_body(page["context"], "refresh", 1, env=env, request_id="multipart", values={})
            ),
            content_type="application/json",
        )
        multipart.add_field("file:extra", b"upload", filename="extra.txt")
        response = await client.post(preview._origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 200
        assert (await response.json())["settlement"] == "settled"
        multipart = FormData()
        multipart.add_field("file:name", b"x" * (10 * 1024 * 1024 + 1), filename="x.bin")
        response = await client.post(preview._origin + "/api/action", headers=context_headers, data=multipart)
        assert response.status == 413

        response = await client.delete(
            preview._origin + f"/api/pages/{page['context']['id']}", headers=context_headers
        )
        assert response.status == 200
        response = await client.get(preview._origin + "/api/state", headers=context_headers)
        assert response.status == 410


@pytest.mark.asyncio
async def test_preview_pages_keep_viewers_and_reject_stale_generation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    await alice.slash(channel, "panel")

    async with env.preview(channel, viewers=[alice, bob]) as preview:
        headers = preview_headers(preview)
        async with ClientSession() as client:
            response = await client.post(
                preview._origin + "/api/pages", headers=headers, json={"viewer_id": str(alice.id)}
            )
            assert response.status == 200
            alice_page = await response.json()
            response = await client.post(
                preview._origin + "/api/pages", headers=headers, json={"viewer_id": str(bob.id)}
            )
            assert response.status == 200
            bob_page = await response.json()

            assert alice_page["viewerId"] == str(alice.id)
            assert bob_page["viewerId"] == str(bob.id)
            assert alice_page["context"]["id"] != bob_page["context"]["id"]

            context = alice_page["context"]
            action_headers = preview_headers(preview, context["id"])
            switch = action_body(
                context, "viewer", 1, env=env, request_id="switch-viewer", viewer_id=str(bob.id)
            )
            response = await client.post(preview._origin + "/api/action", headers=action_headers, json=switch)
            assert response.status == 200
            switched = await response.json()
            assert switched["dispatched"] is False

            stale = action_body(context, "refresh", 2, env=env, request_id="stale")
            response = await client.post(preview._origin + "/api/action", headers=action_headers, json=stale)
            assert response.status == 200
            assert (await response.json())["rejected"] is True

            other_headers = preview_headers(preview, bob_page["context"]["id"])
            response = await client.get(preview._origin + "/api/state", headers=other_headers)
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
        payload = preview._page_payload(page)
        control_keys = {custom_id: control_key(payload, custom_id) for custom_id in ("who", "role", "chan")}
        for sequence, custom_id, value in (
            (1, "who", [str(bob.id)]),
            (2, "role", [str(role.id)]),
            (3, "chan", [str(channel.id)]),
        ):
            result = await preview._action(
                "python",
                action_body(
                    page,
                    "select",
                    sequence,
                    request_id=custom_id,
                    control_key=control_keys[custom_id],
                    values=value,
                    published_revision=page.revision,
                ),
            )
            assert result["dispatched"] is True
        assert "Channel general" in channel.last_message.content
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "select",
                4,
                request_id="bad",
                control_key=control_keys["who"],
                published_revision=page.revision,
            ),
        )
        assert rejected["dispatched"] is False
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "select",
                4,
                request_id="bad-option",
                control_key=control_keys["who"],
                values=["999999999999"],
                published_revision=page.revision,
            ),
        )
        assert rejected["dispatched"] is False
        gap = await preview._action("python", action_body(page, "refresh", 7, request_id="gap"))
        assert gap["rejected"] is True
        focused = await alice.slash(channel, "panel")
        focus = await preview._action(
            "python",
            action_body(page, "focus", 4, request_id="focus", target_id=str(focused.response.id)),
        )
        assert focus["dispatched"] is False
        page = preview._python
        assert preview._page_payload(page)["targetId"] == str(focused.response.id)

        for _ in range(16):
            preview._open_page(alice.id)
        with pytest.raises(simcord.SetupError, match="page limit"):
            preview._open_page(alice.id)
        with pytest.raises(simcord.SetupError, match="cannot be closed"):
            preview._close_page("python")


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
        payload = preview._page_payload(page)
        boom_key = control_key(payload, "boom")
        timeout_key = control_key(payload, "timeout")
        failed = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="boom",
                control_key=boom_key,
                published_revision=page.revision,
            ),
        )
        assert failed["dispatched"] is True
        assert failed["acknowledgement"] == "unacknowledged"

        timed = await preview._action(
            "python",
            action_body(
                page,
                "click",
                2,
                request_id="timeout",
                control_key=timeout_key,
                published_revision=page.revision,
            ),
        )
        assert timed["dispatched"] is True
        assert timed["acknowledgement"] == "unacknowledged"

        closed = await preview._action("python", action_body(page, "close", 3, request_id="close"))
        assert closed["settlement"] == "settled"
        await preview.wait_closed()


@pytest.mark.asyncio
async def test_preview_boundary_errors_and_lazy_asset_validation(tmp_path, env, channel, alice):
    unopened = env.preview(channel, viewers=[alice])
    with pytest.raises(simcord.SetupError, match="not entered"):
        _ = unopened.url
    with pytest.raises(simcord.SetupError, match="active"):
        unopened._open_page()

    bad_url = "https://cdn.example.test/bad.png"
    embed = discord.Embed().set_image(url=bad_url)
    message = await env.bot.get_channel(channel.id).send(embed=embed)
    async with env.preview(channel, viewers=[alice], assets={bad_url: ("bad.png", b"not-image")}) as preview:
        page = preview._python
        with pytest.raises(simcord.SetupError, match="unknown"):
            preview._get_page("missing")
        with pytest.raises(simcord.SetupError, match="authorized target"):
            preview._open_page(target_id="bad")
        with pytest.raises(simcord.SetupError, match="expects"):
            await preview.show(object())
        with pytest.raises(simcord.SetupError, match="unavailable"):
            preview._open_page(target_id="999999999999")
        with pytest.raises(simcord.SetupError, match="unavailable"):
            await preview.screenshot(tmp_path / "bad.png", target=999999999999)
        with pytest.raises(simcord.SetupError, match="filesystem-like"):
            await preview.screenshot(object())
        with pytest.raises(simcord.SetupError, match="boolean"):
            await preview.screenshot(tmp_path / "bad.png", allow_incomplete=1)
        with pytest.raises(simcord.SetupError, match="capture target"):
            await preview.screenshot(tmp_path / "bad.png", target=object())
        await preview.show(message)
        asset = target_message(preview._page_payload(page))["embeds"][0]["image"]["asset_id"]
        with pytest.raises(simcord.SetupError, match="valid PNG"):
            await preview._prepare_asset("python", asset)
        with pytest.raises(simcord.SetupError, match="asset is unavailable"):
            preview._asset("python", "missing")
    await unopened.close()
    with pytest.raises(simcord.SetupError, match="not active"):
        await unopened.screenshot(tmp_path / "closed.png")


@pytest.mark.asyncio
async def test_preview_ephemeral_filter_and_action_validation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    ephemeral = await alice.context_menu(channel, "Report Member", bob)
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        await preview.show(ephemeral.response)
        alice_payload = preview._page_payload(preview._python)
        assert target_message(alice_payload)["ephemeral"] is True
        bob_page = preview._open_page(bob.id, target_id=ephemeral.response.id)
        assert target_message(preview._page_payload(bob_page)) is None

        page = preview._python
        rejections = (
            await preview._action("python", []),
            await preview._action("python", action_body(page, "refresh", True, request_id="bad")),
            await preview._action("python", action_body(page, "refresh", 1, request_id="")),
            await preview._action("python", action_body(page, "unknown", 1, request_id="kind")),
            await preview._action(
                "python", action_body(page, "refresh", 1, request_id="generation", generation=999)
            ),
            await preview._action(
                "python", action_body(page, "refresh", 1, request_id="bot", bot_generation=999)
            ),
        )
        assert all(item["rejected"] is True for item in rejections)
        assert all(item["expectedSequence"] == 0 for item in rejections)

        body = action_body(page, "refresh", 1, request_id="refresh")
        settled = await preview._action("python", body)
        assert settled["settlement"] == "settled"
        assert await preview._action("python", body) == settled
        conflict = await preview._action("python", action_body(page, "focus", 1, request_id="refresh"))
        assert conflict["rejected"] is True
        assert conflict["diagnostics"][0]["code"] == "conflicting-request"

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
                request_id="bad-modal",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"unknown": "x"},
            ),
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
    await (await env.bot.fetch_channel(dm.id)).send(content="pick", view=AssignView())
    async with env.preview(dm, viewers=[alice.user]) as preview:
        page = preview._python
        payload = preview._page_payload(page)
        who_key = control_key(payload, "who")
        assert payload["candidates"][who_key][0]["id"] == str(alice.id)
        result = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="dm-user",
                control_key=who_key,
                values=[str(alice.id)],
                published_revision=page.revision,
            ),
        )
        assert result["dispatched"] is True
        assert dm.last_message.content == "Picked alice"
