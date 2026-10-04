import json
import socket
from importlib import resources

import discord
import jsonschema
import pytest
import regex
from aiohttp import ClientSession
from preview_helpers import action_body, control_key, preview_headers, target_message

import simcord


@pytest.mark.asyncio
async def test_preview_snapshot_matches_packaged_schema(env, channel, alice):
    schema = json.loads(resources.files("simcord.preview").joinpath("protocol.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    await alice.slash(channel, "panel")

    async with env.preview(channel, viewers=[alice]) as preview:
        snapshot = await preview.snapshot()

    validator.validate(snapshot)
    invalid = {**snapshot, "protocolVersion": 1}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


@pytest.mark.asyncio
async def test_v2_text_display_summary_hides_spoilers_but_searches_visible_text(env, channel, alice):
    secret = "unrevealed-review-secret"
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.TextDisplay(f"Visible ||{secret}||"))
    message = await env.bot.get_channel(channel.id).send(view=view)

    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        summary = next(
            item for item in preview._page_payload(page)["messageIndex"] if item["id"] == str(message.id)
        )
        assert summary["excerpt"] == "Visible [spoiler]"
        assert summary["components"][0]["label"] == "Visible [spoiler]"
        assert secret not in json.dumps(summary)

        hidden = await preview._action(page.id, action_body(page, "browse_messages", 1, query=secret))
        assert hidden["result"]["messageIndex"] == []
        visible = await preview._action(page.id, action_body(page, "browse_messages", 2, query="visible"))
        assert [item["id"] for item in visible["result"]["messageIndex"]] == [str(message.id)]


@pytest.mark.asyncio
async def test_preview_unicode_grapheme_summaries_validate_against_schema(env, channel, alice):
    family = "👨‍👩‍👧‍👦"
    combining_cluster = "e" + "\u0301" * 300
    content = family * 99 + combining_cluster
    label_content = family * 79 + combining_cluster
    cached = env.bot.get_channel(channel.id)
    plain = await cached.send(content)
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.TextDisplay(label_content))
    v2 = await cached.send(view=view)
    assert plain.content == content
    assert v2.components[0].content == label_content

    schema = json.loads(resources.files("simcord.preview").joinpath("protocol.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    async with env.preview(channel, viewers=[alice]) as preview:
        snapshot = await preview.snapshot()
        validator.validate(snapshot)
        summaries = {item["id"]: item for item in snapshot["messageIndex"]}
        excerpt = summaries[str(plain.id)]["excerpt"]
        label = summaries[str(v2.id)]["components"][0]["label"]
        assert len(excerpt) > 512 and len(regex.findall(r"\X", excerpt)) == 100
        assert excerpt.endswith(combining_cluster)
        assert len(label) > 200 and len(regex.findall(r"\X", label)) == 80
        assert label.endswith(combining_cluster)


@pytest.mark.asyncio
async def test_preview_snapshot_returns_detached_projection(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        snap = await preview.snapshot()
        assert snap["protocolVersion"] == 3
        assert "selected" not in snap
        assert snap["viewerId"] == str(alice.id)
        assert target_message(snap)["content"] == "Panel"
        assert isinstance(snap["messageIndex"], list)
        assert isinstance(snap["messages"], dict)
        assert snap["targetId"] in snap["messages"]
        assert snap["timeline"] == [snap["targetId"]]
        for key in (
            "messages",
            "messageIndex",
            "timeline",
            "modal",
            "candidates",
            "assets",
            "diagnostics",
            "lastAction",
            "status",
        ):
            assert key in snap

        await alice.send(channel, "hello")
        snap = await preview.snapshot()
        assert "hello" in [item["excerpt"] for item in snap["messageIndex"]]
        assert target_message(snap)["content"] == "Panel"

        target_message(snap)["content"] = "mutated"
        assert target_message(preview._page_payload(preview._python))["content"] == "Panel"
        snap = await preview.snapshot()
        assert target_message(snap)["content"] == "Panel"


@pytest.mark.asyncio
async def test_preview_control_keys_are_scoped_per_message(env, channel, alice):
    first_view = discord.ui.View()
    first_view.add_item(discord.ui.Button(label="First", custom_id="same"))
    second_view = discord.ui.View()
    second_view.add_item(discord.ui.Button(label="Second", custom_id="same"))
    first = await env.bot.get_channel(channel.id).send(view=first_view)
    second = await env.bot.get_channel(channel.id).send(view=second_view)

    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(first)
        first_snapshot = preview._page_payload(preview._python)
        await preview.show(second)
        second_snapshot = preview._page_payload(preview._python)
        first_key = control_key(first_snapshot, "same")
        second_key = control_key(second_snapshot, "same")
        assert first_key != second_key
        assert first_key.startswith(f"message:{first.id}:")
        assert second_key.startswith(f"message:{second.id}:")


@pytest.mark.asyncio
async def test_preview_snapshot_requires_active_session(env, channel, alice):
    preview = env.preview(channel, viewers=[alice])
    with pytest.raises(simcord.SetupError, match="not active"):
        await preview.snapshot()
    async with preview:
        pass
    with pytest.raises(simcord.SetupError, match="not active"):
        await preview.snapshot()


@pytest.mark.asyncio
async def test_preview_port_pins_loopback_port(env, channel, alice):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    async with env.preview(channel, viewers=[alice], port=port) as preview:
        assert f"127.0.0.1:{port}" in preview.url


@pytest.mark.asyncio
async def test_preview_port_validation(env, channel, alice):
    for port in (-1, 65536, "8080", True):
        with pytest.raises(simcord.SetupError, match="port must be an integer"):
            env.preview(channel, viewers=[alice], port=port)


@pytest.mark.asyncio
async def test_preview_port_bind_failure(env, channel, alice):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    try:
        port = held.getsockname()[1]
        preview = env.preview(channel, viewers=[alice], port=port)
        with pytest.raises(simcord.SetupError, match="could not bind port"):
            async with preview:
                pass
        # A failed entry is dead but settled: the env is released and
        # wait_closed() returns instead of hanging.
        assert env._preview is None
        await preview.wait_closed()
        async with env.preview(channel, viewers=[alice]) as recovered:
            assert recovered.url
    finally:
        held.close()


@pytest.mark.asyncio
async def test_preview_port_80_elided_host_and_origin(env, channel, alice):
    from simcord.preview._server import PreviewServer

    async with env.preview(channel, viewers=[alice]) as preview:
        # Browsers strip the http scheme-default port from Host and Origin, so
        # a server bound to :80 must accept the port-less forms.
        server = PreviewServer(preview)
        server.port = 80
        headers = {**preview_headers(preview), "Host": "127.0.0.1", "Origin": "http://127.0.0.1"}
        assert server._authorized(_Request(headers)) is True
        headers = {**headers, "Host": "localhost", "Origin": "http://localhost"}
        assert server._authorized(_Request(headers)) is True
        assert server._authorized(_Request({**headers, "Host": "127.0.0.1:80"})) is True
        assert server._authorized(_Request({**headers, "Host": "evil.example"})) is False
        assert server._authorized(_Request({**headers, "Origin": "http://evil.example"})) is False


class _Request:
    """Minimal stand-in for an aiohttp request for ``_authorized``."""

    def __init__(self, headers):
        self.headers = headers


@pytest.mark.asyncio
async def test_preview_localhost_origin_allowed(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview, ClientSession() as client:
        port = preview._server.port
        headers = {
            **preview_headers(preview),
            "Host": f"localhost:{port}",
            "Origin": f"http://localhost:{port}",
        }
        response = await client.post(preview._origin + "/api/pages", headers=headers, json={})
        assert response.status == 200
        response = await client.post(
            preview._origin + "/api/pages",
            headers={**headers, "Origin": "http://evil.example"},
            json={},
        )
        assert response.status == 401


@pytest.mark.asyncio
async def test_maximum_unicode_message_query_cursors_round_trip(env, channel, alice):
    query = "😀" * 80
    cached = env.bot.get_channel(channel.id)
    for index in range(51):
        await cached.send(f"{query} result {index}")
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        first = await preview._action(page.id, action_body(page, "browse_messages", 1, query=query))
        navigation = first["result"]["navigation"]
        assert len(first["result"]["messageIndex"]) == 50
        assert len(navigation["nextCursor"]) <= 1024
        last = await preview._action(
            page.id,
            action_body(page, "browse_messages", 2, query=query, cursor=navigation["nextCursor"]),
        )
        assert len(last["result"]["messageIndex"]) == 1
        previous = last["result"]["navigation"]["previousCursor"]
        assert previous
        back = await preview._action(
            page.id, action_body(page, "browse_messages", 3, query=query, cursor=previous)
        )
        assert len(back["result"]["messageIndex"]) == 50
        tampered = navigation["nextCursor"][:-1] + ("A" if navigation["nextCursor"][-1] != "A" else "B")
        invalid = await preview._action(
            page.id, action_body(page, "browse_messages", 4, query=query, cursor=tampered)
        )
        assert invalid["rejected"]


@pytest.mark.asyncio
async def test_unicode_candidate_cursor_uses_compact_position(env, channel, alice):
    query = "😀" * 80
    users = [env.guild.add_member(env.create_user(f"{query}{'x' * 1024}-{index:02}")) for index in range(55)]
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.UserSelect(custom_id="unicode-candidates"))
    message = await env.bot.get_channel(channel.id).send("candidates", view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        payload = preview._page_payload(page)
        key = control_key(payload, "unicode-candidates")

        def browse(sequence, cursor=None):
            return preview._action(
                page.id,
                action_body(
                    page,
                    "browse_candidates",
                    sequence,
                    control_key=key,
                    modal_handle=None,
                    query=query,
                    cursor=cursor,
                ),
            )

        first = await browse(1)
        descriptor = first["result"]["candidate"]
        assert len(descriptor["entries"]) == 50
        assert len(descriptor["entries"][0]["label"]) > 1024
        cursor = descriptor["nextCursor"]
        assert cursor and len(cursor) <= 1024
        second = await browse(2, cursor)
        assert len(second["result"]["candidate"]["entries"]) == 5
        previous = second["result"]["candidate"]["previousCursor"]
        assert previous
        back = await browse(3, previous)
        assert len(back["result"]["candidate"]["entries"]) == 50
        assert {row["id"] for row in back["result"]["candidate"]["entries"]} == {
            str(user.id) for user in users[:50]
        }


@pytest.mark.parametrize("kind", ["users", "roles", "channels"])
@pytest.mark.parametrize("change", ["remove", "rename"])
@pytest.mark.asyncio
async def test_candidate_cursor_recovers_lost_live_anchor(env, channel, alice, kind, change):
    if kind == "users":
        for index in range(55):
            env.guild.add_member(env.create_user(f"cursor-{index:02}"))
        select = discord.ui.UserSelect(custom_id="cursor")
    elif kind == "roles":
        for index in range(55):
            env.guild.create_role(f"cursor-{index:02}")
        select = discord.ui.RoleSelect(custom_id="cursor")
    else:
        for index in range(55):
            env.guild.create_text_channel(f"cursor-{index:02}")
        select = discord.ui.ChannelSelect(custom_id="cursor")
    await env.settle()
    view = discord.ui.View(timeout=None)
    view.add_item(select)
    message = await env.bot.get_channel(channel.id).send(view=view)
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        key = control_key(preview._page_payload(page), "cursor")

        async def browse(sequence, cursor=None):
            return await preview._action(
                page.id,
                action_body(
                    page,
                    "browse_candidates",
                    sequence,
                    control_key=key,
                    modal_handle=None,
                    query="cursor-",
                    cursor=cursor,
                ),
            )

        first = (await browse(1))["result"]["candidate"]
        cursor = first["nextCursor"]
        anchor_id = int(first["entries"][-1]["id"])
        await browse(2, cursor)
        cached_guild = env.bot.get_guild(env.guild.id)
        if kind == "users":
            anchor = cached_guild.get_member(anchor_id)
            if change == "remove":
                await anchor.kick()
            else:
                await anchor.edit(nick="outside the query")
        else:
            anchor = cached_guild.get_role(anchor_id) if kind == "roles" else env.bot.get_channel(anchor_id)
            if change == "remove":
                await anchor.delete()
            else:
                await anchor.edit(name="outside the query")
        await preview.refresh()
        refreshed = preview._page_payload(page)
        candidate = refreshed["candidates"][key]
        assert candidate["hasPrevious"] is False
        assert str(anchor_id) not in {row["id"] for row in candidate["entries"]}
        assert any(
            item["code"] == "stale-cursor" and item["state"] == "recovered"
            for item in refreshed["diagnostics"]
        )
        rejected = await browse(3, cursor)
        assert rejected["rejected"] and rejected["diagnostics"][0]["code"] == "stale-cursor"
        assert page.last_sequence == 2


@pytest.mark.asyncio
async def test_navigation_is_bounded_query_scoped_and_preserved_on_resize(env, channel, alice):
    cached = env.bot.get_channel(channel.id)
    history_target = None
    for number in range(1001):
        message = await cached.send(f"INDEX {number:04} Café 👩🏽‍💻 ||classifiedneedle||")
        if number == 500:
            history_target = message
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        other = preview._open_page(alice.id)
        initial = preview._page_payload(page)
        assert len(initial["messageIndex"]) == 50
        assert len(initial["messages"]) == 1
        assert "classifiedneedle" not in json.dumps(initial["messageIndex"])

        query = await preview._action(page.id, action_body(page, "browse_messages", 1, query="CAFE\u0301"))
        assert query["settlement"] == "settled"
        first = query["result"]["messageIndex"]
        cursor = query["result"]["navigation"]["nextCursor"]
        assert len(first) == 50 and cursor
        rejected = await preview._action(
            other.id, action_body(other, "browse_messages", 1, query="CAFE\u0301", cursor=cursor)
        )
        assert rejected["rejected"] and other.last_sequence == 0
        assert rejected["diagnostics"][0]["code"] == "stale-cursor"
        second = await preview._action(
            page.id, action_body(page, "browse_messages", 2, query="CAFE\u0301", cursor=cursor)
        )
        assert not ({row["id"] for row in first} & {row["id"] for row in second["result"]["messageIndex"]})
        generation = page.generation
        resized = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                3,
                layout="message",
                display="responsive",
                width=960,
                height=720,
                host_width=320,
                host_height=240,
            ),
        )
        assert not resized["rejected"]
        assert page.generation == generation
        snapshot = preview._page_payload(page)
        assert snapshot["presentation"]["viewport"] == {"width": 320, "height": 240}
        assert snapshot["presentation"]["exactProfile"] == {"width": 960, "height": 720}
        assert snapshot["navigation"]["query"] == "café"
        assert snapshot["messageIndex"] == second["result"]["messageIndex"]
        assert preview._page_payload(preview._python)["presentation"]["viewport"] == {
            "width": 960,
            "height": 720,
        }
        focused = await preview._action(page.id, action_body(page, "focus", 4, target_id=first[0]["id"]))
        assert not focused["rejected"]
        assert preview._page_payload(page)["navigation"]["query"] == "café"
        old_cursor = await preview._action(
            page.id, action_body(page, "browse_messages", 5, query="CAFE\u0301", cursor=cursor)
        )
        assert old_cursor["rejected"] and old_cursor["diagnostics"][0]["code"] == "stale-cursor"
        hidden = await preview._action(
            page.id, action_body(page, "browse_messages", 5, query="classifiedneedle")
        )
        assert hidden["result"]["messageIndex"] == []
        too_long = await preview._action(page.id, action_body(page, "browse_messages", 6, query="x" * 129))
        assert too_long["rejected"] and page.last_sequence == 5

        focused_middle = await preview._action(
            page.id, action_body(page, "focus", 6, target_id=history_target.id)
        )
        assert not focused_middle["rejected"]
        configured_channel = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                7,
                layout="channel",
                display="responsive",
                width=960,
                height=720,
                host_width=320,
                host_height=240,
            ),
        )
        assert not configured_channel["rejected"]
        older = await preview._action(page.id, action_body(page, "history", 8, direction="older"))
        assert not older["rejected"]
        older_snapshot = preview._page_payload(page)
        assert older_snapshot["targetId"] is None
        assert older_snapshot["history"]["hasAfter"]

        resized_channel = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                9,
                layout="channel",
                display="responsive",
                width=1280,
                height=900,
                host_width=400,
                host_height=300,
            ),
        )
        assert not resized_channel["rejected"]
        resized_snapshot = preview._page_payload(page)
        assert resized_snapshot["history"] == older_snapshot["history"]
        assert resized_snapshot["timeline"] == older_snapshot["timeline"]
        assert resized_snapshot["presentation"]["viewport"] == {"width": 400, "height": 300}

        message_layout = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                10,
                layout="message",
                display="responsive",
                width=1280,
                height=900,
                host_width=400,
                host_height=300,
            ),
        )
        assert not message_layout["rejected"]
        assert preview._page_payload(page)["history"]["hasAfter"] is False
        focused_again = await preview._action(
            page.id, action_body(page, "focus", 11, target_id=history_target.id)
        )
        assert not focused_again["rejected"]
        channel_again = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                12,
                layout="channel",
                display="responsive",
                width=1280,
                height=900,
                host_width=400,
                host_height=300,
            ),
        )
        assert not channel_again["rejected"]
        channel_snapshot = preview._page_payload(page)
        assert channel_snapshot["targetId"] == str(history_target.id)
        assert str(history_target.id) in channel_snapshot["timeline"]
        assert channel_snapshot["history"]["windowEndId"] != older_snapshot["history"]["windowEndId"]


@pytest.mark.asyncio
async def test_viewer_transition_redacts_prior_private_action_receipts(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))

    class PrivateOutputView(discord.ui.View):
        @discord.ui.button(label="Output", custom_id="private-output")
        async def output(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message("private response", ephemeral=True)
            await interaction.followup.send("private followup", ephemeral=True)

    source = await env.bot.get_channel(channel.id).send("source", view=PrivateOutputView())
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        await preview.show(source)
        page = preview._open_page(alice.id)
        click = await preview._action(
            page.id,
            action_body(
                page,
                "click",
                1,
                custom_id="private-output",
                control_key=control_key(preview._page_payload(page), "private-output"),
            ),
        )
        old_action = page.latest_action
        old_correlation = click["correlation"]
        output_ids = {item["messageId"] for item in click["outcomes"]}
        assert (
            old_action is not None and old_action.interaction is not None and old_action.response is not None
        )
        assert {item["kind"] for item in click["outcomes"]} == {"response", "followup"}

        transition = await preview._action(page.id, action_body(page, "viewer", 2, viewer_id=bob.id))
        assert transition["settlement"] == "settled"
        snapshot = preview._page_payload(page)
        assert snapshot["viewerId"] == str(bob.id)
        assert len(snapshot["activity"]) == 1
        assert snapshot["activity"][0]["correlation"] == transition["correlation"]
        assert snapshot["activity"][0]["outcomes"] == []
        assert snapshot["lastAction"]["correlation"] == transition["correlation"]
        assert old_action.interaction is None and old_action.outcomes == []
        assert old_action.target is None and old_action.response["outcomes"] == []
        serialized = json.dumps(snapshot)
        assert old_correlation not in serialized
        assert all(output_id not in serialized for output_id in output_ids)


@pytest.mark.asyncio
async def test_receipts_only_discover_causal_authorized_outputs_and_never_replay(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    calls = []
    unrelated = []

    class OutputView(discord.ui.View):
        @discord.ui.button(label="Output", custom_id="causal-output")
        async def output(self, interaction: discord.Interaction, button: discord.ui.Button):
            calls.append(interaction.id)
            await interaction.response.send_message("private response", ephemeral=True)
            await interaction.followup.send("private followup", ephemeral=True)
            unrelated.append(await env.bot.get_channel(channel.id).send("unrelated bot output"))

    source = await env.bot.get_channel(channel.id).send("source", view=OutputView())
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        await preview.show(source)
        page = preview._open_page(alice.id)
        body = action_body(
            page,
            "click",
            1,
            custom_id="causal-output",
            control_key=control_key(preview._page_payload(page), "causal-output"),
        )
        receipt = await preview._action(page.id, body)
        assert receipt["dispatch"] == "dispatched" and receipt["settlement"] == "settled"
        assert {item["kind"] for item in receipt["outcomes"]} == {"response", "followup"}
        output_ids = {item["messageId"] for item in receipt["outcomes"]}
        assert str(unrelated[0].id) not in output_ids
        assert receipt["target"]["messageId"] == str(source.id)
        assert page.target_id == source.id
        replay = await preview._action(page.id, body)
        assert replay == receipt and len(calls) == 1
        other = preview._open_page(bob.id)
        private_focus = await preview._action(
            other.id, action_body(other, "focus", 1, target_id=next(iter(output_ids)))
        )
        assert private_focus["rejected"] and other.target_id == source.id
        await env.bot.get_channel(channel.id).set_permissions(
            env.bot.get_guild(env.guild.id).get_member(alice.id), view_channel=False
        )
        await preview.refresh()
        replay_denied = await preview._action(page.id, body)
        assert replay_denied["presentation"] == "access_denied"
        assert replay_denied["target"] is None
        assert replay_denied["outcomes"] == [] and len(calls) == 1
        assert not preview._page_payload(page)["messageIndex"]


@pytest.mark.asyncio
async def test_preview_modal_entity_search_enter_selects_highlighted_candidate(env, channel, alice):
    from playwright.async_api import async_playwright

    users = {name: env.guild.add_member(env.create_user(name)) for name in ("candidate-028", "candidate-029")}
    submitted = []

    class SearchForm(discord.ui.Modal, title="Search"):
        user = discord.ui.Label(
            text="User",
            component=discord.ui.UserSelect(
                custom_id="user",
                default_values=[
                    discord.SelectDefaultValue(
                        id=users["candidate-029"].id,
                        type=discord.SelectDefaultValueType.user,
                    )
                ],
            ),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            submitted.append([member.id for member in self.user.component.values])
            await interaction.response.send_message("selected")

    class OpenSearchForm(discord.ui.View):
        @discord.ui.button(label="Open form", custom_id="open-search-form")
        async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.send_modal(SearchForm())

    message = await env.bot.get_channel(channel.id).send("Search", view=OpenSearchForm())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("button", name="Open form").click()
                await page.locator(".modal-dialog").wait_for()
                await page.locator(".modal-field[data-custom-id='user'] .select-trigger").click()
                await page.wait_for_function(
                    "() => document.querySelector('.select-list:popover-open [role=listbox]')"
                    "?.getAttribute('aria-busy') === 'false'",
                    timeout=5000,
                )
                search = page.get_by_role("searchbox", name="Search options")
                assert await page.locator(".select-candidate-status").get_attribute("role") == "status"
                assert await page.locator(".select-candidate-status").get_attribute("aria-live") == "polite"
                await search.fill("candidate-028")
                candidate = page.locator(f'.select-option[data-value="{users["candidate-028"].id}"]')
                await candidate.wait_for()
                await page.wait_for_function(
                    """id => {
                        const options = document.querySelectorAll('.modal-dialog .select-option');
                        return options.length === 1 && options[0].dataset.value === id;
                    }""",
                    arg=str(users["candidate-028"].id),
                )
                assert await search.evaluate("element => element === document.activeElement")
                await search.press("ArrowDown")
                await search.press("Enter")
                await page.wait_for_function(
                    """name => document.querySelector(
                      '.modal-field[data-custom-id="user"] .select-trigger'
                    )?.getAttribute("aria-valuetext") === name""",
                    arg="candidate-028",
                )
                assert submitted == []
                assert await page.locator(".modal-dialog").count() == 1
                await page.get_by_role("button", name="Submit").click()
                await page.locator(".modal-dialog").wait_for(state="detached")
                await page.wait_for_function(
                    "() => !window.simcordPreview?.pendingAction "
                    "&& window.simcordPreview?.lastAction?.settlement === 'settled'"
                )
                assert submitted == [[users["candidate-028"].id]]
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_preview_modal_optional_select_clear_focusout_and_disabled_entity(env, channel, alice):
    from playwright.async_api import async_playwright

    locked_user = env.guild.add_member(env.create_user("locked-user"))
    submitted = []

    class BoundaryForm(discord.ui.Modal, title="Select boundaries"):
        note = discord.ui.Label(
            text="Optional note",
            component=discord.ui.TextInput(custom_id="note", required=False, min_length=3),
        )
        single = discord.ui.Label(
            text="Optional destination",
            component=discord.ui.Select(
                custom_id="single",
                required=False,
                min_values=0,
                max_values=1,
                options=[discord.SelectOption(label="Moon Base", value="moon", default=True)],
            ),
        )
        multi = discord.ui.Label(
            text="Destinations",
            component=discord.ui.Select(
                custom_id="multi",
                min_values=1,
                max_values=2,
                options=[
                    discord.SelectOption(label="Forest Camp", value="forest", default=True),
                    discord.SelectOption(label="Moon Base", value="moon"),
                ],
            ),
        )
        locked = discord.ui.Label(
            text="Locked user",
            component=discord.ui.UserSelect(
                custom_id="locked",
                required=False,
                min_values=0,
                max_values=1,
                disabled=True,
                default_values=[
                    discord.SelectDefaultValue(
                        id=locked_user.id,
                        type=discord.SelectDefaultValueType.user,
                    )
                ],
            ),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            submitted.append(
                {
                    "note": self.note.component.value,
                    "single": self.single.component.values,
                    "multi": self.multi.component.values,
                    "locked": [member.id for member in self.locked.component.values],
                }
            )
            await interaction.response.send_message("submitted")

    class OpenBoundaryForm(discord.ui.View):
        @discord.ui.button(label="Open form", custom_id="open-boundary-form")
        async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.send_modal(BoundaryForm())

    message = await env.bot.get_channel(channel.id).send("Boundaries", view=OpenBoundaryForm())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("button", name="Open form").click()
                await page.locator(".modal-dialog").wait_for()

                single = page.locator(".modal-field[data-custom-id='single']")
                assert await single.locator(".select-trigger").get_attribute("aria-valuetext") == "Moon Base"
                await single.get_by_role("button", name="Clear selection").click()
                assert (
                    await single.locator(".select-trigger").get_attribute("aria-valuetext")
                    == "Select an option"
                )

                locked = page.locator(".modal-field[data-custom-id='locked']")
                clear_locked = locked.get_by_role("button", name="Clear selection")
                await clear_locked.wait_for()
                assert await clear_locked.is_disabled()
                assert (
                    await locked.locator(".select-trigger").get_attribute("aria-valuetext") == "locked-user"
                )

                multi = page.locator(".modal-field[data-custom-id='multi']")
                trigger = multi.locator(".select-trigger")
                await trigger.click()
                await trigger.press("ArrowDown")
                await trigger.press("Space")
                await page.keyboard.press("Tab")
                await page.keyboard.press("Tab")
                await page.keyboard.press("Tab")
                await page.wait_for_function(
                    "() => document.querySelector("
                    "'.modal-field[data-custom-id=\"multi\"] .select-trigger'"
                    ")?.getAttribute('aria-expanded') === 'false'"
                )
                assert await multi.locator(".select-trigger").get_attribute("aria-valuetext") == "Forest Camp"

                await trigger.click()
                await trigger.press("ArrowDown")
                await trigger.press("Space")
                await page.locator("input[name='note']").click()
                note = page.locator("input[name='note']")
                await note.fill("abc")
                await note.fill("")
                assert (
                    await multi.locator(".select-trigger").get_attribute("aria-valuetext")
                    == "Forest Camp, Moon Base"
                )

                await page.get_by_role("button", name="Submit").click()
                await page.locator(".modal-dialog").wait_for(state="detached")
                await page.wait_for_function(
                    "() => !window.simcordPreview?.pendingAction "
                    "&& window.simcordPreview?.lastAction?.settlement === 'settled'"
                )
                assert submitted == [
                    {
                        "note": "",
                        "single": [],
                        "multi": ["forest", "moon"],
                        "locked": [locked_user.id],
                    }
                ]
            finally:
                await browser.close()


@pytest.mark.asyncio
async def test_preview_modal_optional_multivalues_clear_to_empty_but_required_stays_invalid(
    env, channel, alice
):
    from playwright.async_api import async_playwright

    candidate = env.guild.add_member(env.create_user("optional-candidate"))
    submitted = []

    class MultiForm(discord.ui.Modal, title="Optional values"):
        entity = discord.ui.Label(
            text="Optional users",
            component=discord.ui.UserSelect(
                custom_id="entity",
                required=False,
                min_values=1,
                max_values=2,
            ),
        )
        checks = discord.ui.Label(
            text="Optional checks",
            component=discord.ui.CheckboxGroup(
                custom_id="checks",
                options=[
                    discord.CheckboxGroupOption(label="One", value="one"),
                    discord.CheckboxGroupOption(label="Two", value="two"),
                ],
                required=False,
                min_values=1,
                max_values=2,
            ),
        )
        upload = discord.ui.Label(
            text="Optional files",
            component=discord.ui.FileUpload(
                custom_id="upload",
                required=False,
                min_values=1,
                max_values=2,
            ),
        )
        required = discord.ui.Label(
            text="Required choices",
            component=discord.ui.Select(
                custom_id="required",
                min_values=1,
                max_values=2,
                options=[
                    discord.SelectOption(label="Required choice", value="required", default=True),
                    discord.SelectOption(label="Other choice", value="other"),
                ],
            ),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            submitted.append(
                {
                    "entity": [member.id for member in self.entity.component.values],
                    "checks": self.checks.component.values,
                    "upload": [file.filename for file in self.upload.component.values],
                    "required": self.required.component.values,
                }
            )
            await interaction.response.send_message("submitted")

    class OpenMultiForm(discord.ui.View):
        @discord.ui.button(label="Open form", custom_id="open-multi-form")
        async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.send_modal(MultiForm())

    message = await env.bot.get_channel(channel.id).send("Optional values", view=OpenMultiForm())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                await page.get_by_role("button", name="Open form").click()
                await page.locator(".modal-dialog").wait_for()

                required = page.locator(".modal-field[data-custom-id='required']")
                await required.locator(".select-trigger").click()
                await required.locator('.select-option[data-value="required"]').click()
                await required.locator(".select-apply").click()
                await required.locator('.select-option[data-value="required"]').wait_for()
                assert await required.locator(".select-trigger").get_attribute("aria-invalid") == "true"
                await required.locator('.select-option[data-value="required"]').click()
                await required.locator(".select-apply").click()

                entity = page.locator(".modal-field[data-custom-id='entity']")
                await entity.locator(".select-trigger").click()
                search = entity.locator(".select-candidate-search")
                await search.fill("optional-candidate")
                await entity.locator(f'.select-option[data-value="{candidate.id}"]').wait_for()
                await entity.locator(f'.select-option[data-value="{candidate.id}"]').click()
                await entity.locator(".select-apply").click()
                await entity.locator(".select-trigger").click()
                search = entity.locator(".select-candidate-search")
                await search.fill("optional-candidate")
                await entity.locator(f'.select-option[data-value="{candidate.id}"]').wait_for()
                await entity.locator(f'.select-option[data-value="{candidate.id}"]').click()
                await entity.locator(".select-apply").click()
                cleared = await entity.locator(".select-trigger").evaluate(
                    "element => window.simcordPreview.selectStates[element.dataset.controlKey]"
                )
                assert cleared["values"] == []
                assert cleared["valid"] is True

                checks = page.locator(".modal-field[data-custom-id='checks']")
                checkbox = checks.locator('input[type="checkbox"]').first
                await checkbox.click()
                await checks.locator('input[type="checkbox"]').first.click()
                upload = page.locator(".modal-field[data-custom-id='upload']")
                # A file chooser can complete after an asynchronous modal redraw.
                pending_input = await upload.locator('input[type="file"]').evaluate_handle("input => input")
                await required.locator(".select-trigger").click()
                await required.locator(".select-cancel").click()
                await pending_input.evaluate(
                    """input => {
                        const files = new DataTransfer();
                        files.items.add(new File(['content'], 'clear.txt', {type: 'text/plain'}));
                        input.files = files.files;
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                    }"""
                )
                await pending_input.dispose()
                await upload.get_by_role("button", name="Remove clear.txt").click()

                await page.get_by_role("button", name="Submit").click()
                await page.locator(".modal-dialog").wait_for(state="detached")
                await page.wait_for_function(
                    "() => !window.simcordPreview?.pendingAction "
                    "&& window.simcordPreview?.lastAction?.settlement === 'settled'"
                )
                assert submitted == [{"entity": [], "checks": [], "upload": [], "required": ["required"]}]
            finally:
                await browser.close()
