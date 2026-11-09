import json

import pytest
from preview_helpers import action_body


async def _navigation_history(env, channel, count):
    cached = env.bot.get_channel(channel.id)
    middle = None
    for number in range(count):
        message = await cached.send(f"INDEX {number:04} Café 👩🏽‍💻 ||classifiedneedle||")
        if number == count // 2:
            middle = message
    return middle


@pytest.mark.asyncio
async def test_navigation_bounds_query_privacy_and_page_scoped_cursors(env, channel, alice):
    await _navigation_history(env, channel, 1001)
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
        focused = await preview._action(page.id, action_body(page, "focus", 3, target_id=first[0]["id"]))
        assert not focused["rejected"]
        assert preview._page_payload(page)["navigation"]["query"] == "café"
        old_cursor = await preview._action(
            page.id, action_body(page, "browse_messages", 4, query="CAFE\u0301", cursor=cursor)
        )
        assert old_cursor["rejected"] and old_cursor["diagnostics"][0]["code"] == "stale-cursor"
        hidden = await preview._action(
            page.id, action_body(page, "browse_messages", 4, query="classifiedneedle")
        )
        assert hidden["result"]["messageIndex"] == []
        too_long = await preview._action(page.id, action_body(page, "browse_messages", 5, query="x" * 129))
        assert too_long["rejected"] and page.last_sequence == 4


@pytest.mark.asyncio
async def test_navigation_query_window_survives_logical_viewport_resize(env, channel, alice):
    await _navigation_history(env, channel, 101)
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


@pytest.mark.asyncio
async def test_channel_history_window_survives_resize_and_reanchors_after_layout_switch(env, channel, alice):
    history_target = await _navigation_history(env, channel, 151)
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        focused_middle = await preview._action(
            page.id, action_body(page, "focus", 1, target_id=history_target.id)
        )
        assert not focused_middle["rejected"]
        configured_channel = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                2,
                layout="channel",
                display="responsive",
                width=960,
                height=720,
                host_width=320,
                host_height=240,
            ),
        )
        assert not configured_channel["rejected"]
        older = await preview._action(page.id, action_body(page, "history", 3, direction="older"))
        assert not older["rejected"]
        older_snapshot = preview._page_payload(page)
        assert older_snapshot["targetId"] is None
        assert older_snapshot["history"]["hasAfter"]

        resized_channel = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                4,
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
                5,
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
            page.id, action_body(page, "focus", 6, target_id=history_target.id)
        )
        assert not focused_again["rejected"]
        channel_again = await preview._action(
            page.id,
            action_body(
                page,
                "configure_presentation",
                7,
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
async def test_snapshot_selects_independent_navigation_and_centered_history(env, channel, alice):
    native = env.bot.get_channel(channel.id)
    messages = [await native.send(f"plain history {number}") for number in range(121)]
    async with env.preview(channel, viewers=[alice], layout="channel") as preview:
        snapshot = await preview.snapshot()
        assert [row["id"] for row in snapshot["messageIndex"]] == [str(item.id) for item in messages[:50]]
        assert snapshot["timeline"] == [str(item.id) for item in messages[-50:]]
        assert snapshot["navigation"]["hasNext"] and not snapshot["navigation"]["hasPrevious"]
        await preview.show(messages[60])
        centered = await preview.snapshot()
        assert centered["timeline"] == [str(item.id) for item in messages[36:86]]
        assert centered["history"]["hasBefore"] and centered["history"]["hasAfter"]
        await messages[85].delete()
        reanchored = await preview.snapshot()
        assert reanchored["timeline"] == [str(item.id) for item in messages[35:85]]
        assert reanchored["history"]["windowEndId"] == str(messages[84].id)
        await preview.show(messages[-1])
        backfilled = await preview.snapshot()
        expected = [item for item in messages if item.id != messages[85].id][-50:]
        assert backfilled["timeline"] == [str(item.id) for item in expected]
        assert not backfilled["history"]["hasAfter"]


def _assert_navigation(result, messages, has_previous, has_next):
    assert [row["id"] for row in result["messageIndex"]] == [str(message.id) for message in messages]
    navigation = result["navigation"]
    assert navigation["hasPrevious"] is has_previous
    assert navigation["hasNext"] is has_next
    assert bool(navigation["previousCursor"]) is has_previous
    assert bool(navigation["nextCursor"]) is has_next
    if not has_previous:
        assert navigation["previousCursor"] is None
    if not has_next:
        assert navigation["nextCursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 49, 50, 51, 100, 101])
@pytest.mark.parametrize("query", ["needle", ""])
async def test_navigation_exact_page_edges_in_both_directions(env, channel, alice, count, query):
    native = env.bot.get_channel(channel.id)
    messages = [await native.send(f"needle {number}") for number in range(count)]
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        sequence = 0
        cursor = None
        for start in range(0, max(count, 1), 50):
            sequence += 1
            response = await preview._action(
                page.id, action_body(page, "browse_messages", sequence, query=query, cursor=cursor)
            )
            assert not response["rejected"]
            result = response["result"]
            _assert_navigation(result, messages[start : start + 50], start > 0, start + 50 < count)
            cursor = result["navigation"]["nextCursor"]
        while start > 0:
            cursor = result["navigation"]["previousCursor"]
            end = start
            start = max(0, start - 50)
            sequence += 1
            response = await preview._action(
                page.id, action_body(page, "browse_messages", sequence, query=query, cursor=cursor)
            )
            assert not response["rejected"]
            result = response["result"]
            _assert_navigation(result, messages[start:end], start > 0, end < count)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["after", "before"])
async def test_text_navigation_retains_pivot_witness_and_collapses_empty_side(env, channel, alice, direction):
    native = env.bot.get_channel(channel.id)
    messages = [await native.send(f"needle {number}") for number in range(51)]
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        first = await preview._action(page.id, action_body(page, "browse_messages", 1, query="needle"))
        cursor = first["result"]["navigation"]["nextCursor"]
        sequence = 2
        if direction == "before":
            last = await preview._action(
                page.id, action_body(page, "browse_messages", sequence, query="needle", cursor=cursor)
            )
            sequence += 1
            cursor = last["result"]["navigation"]["previousCursor"]
            # Leave 1 and the pivot 51: the latter is the sole next-page witness.
            for message in messages[1:50]:
                await message.delete()
            expected = messages[:1]
        else:
            # Leave the pivot 50 and 51: the former is the sole previous-page witness.
            for message in messages[:49]:
                await message.delete()
            expected = messages[-1:]
        response = await preview._action(
            page.id, action_body(page, "browse_messages", sequence, query="needle", cursor=cursor)
        )
        assert not response["rejected"]
        _assert_navigation(response["result"], expected, direction == "after", direction == "before")
        await expected[0].delete()
        empty = await preview._action(
            page.id, action_body(page, "browse_messages", sequence + 1, query="needle", cursor=cursor)
        )
        assert not empty["rejected"]
        _assert_navigation(empty["result"], [], False, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["delete", "edit"])
@pytest.mark.parametrize("direction", ["after", "before"])
async def test_text_navigation_does_not_assume_changed_anchor_is_a_match(
    env, channel, alice, mutation, direction
):
    native = env.bot.get_channel(channel.id)
    messages = [await native.send(f"needle {number}") for number in range(51)]
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        first = await preview._action(page.id, action_body(page, "browse_messages", 1, query="needle"))
        cursor = first["result"]["navigation"]["nextCursor"]
        sequence = 2
        if direction == "before":
            last = await preview._action(
                page.id, action_body(page, "browse_messages", sequence, query="needle", cursor=cursor)
            )
            sequence += 1
            cursor = last["result"]["navigation"]["previousCursor"]
            changed = messages[50:]
            expected = messages[:50]
        else:
            changed = messages[:50]
            expected = messages[50:]
        for message in changed:
            if mutation == "delete":
                await message.delete()
            else:
                await message.edit(content="unrelated replacement")
        response = await preview._action(
            page.id, action_body(page, "browse_messages", sequence, query="needle", cursor=cursor)
        )
        assert not response["rejected"]
        _assert_navigation(response["result"], expected, False, False)


@pytest.mark.asyncio
async def test_text_navigation_filters_private_matches_before_page_limits(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    backend = env.backend
    private = [
        backend.create_message(
            channel.id,
            env.bot.user.id,
            f"needle private {number}",
            flags=64,
            interaction_metadata={"user": {"id": str(alice.id)}},
            broadcast=False,
        )
        for number in range(51)
    ]
    public = await env.bot.get_channel(channel.id).send("needle public")
    async with env.preview(channel, viewers=[alice, bob]) as preview:
        alice_page = preview._open_page(alice.id)
        bob_page = preview._open_page(bob.id)
        visible = await preview._action(
            alice_page.id, action_body(alice_page, "browse_messages", 1, query="needle")
        )
        _assert_navigation(visible["result"], private[:50], False, True)
        cursor = visible["result"]["navigation"]["nextCursor"]
        rejected = await preview._action(
            bob_page.id, action_body(bob_page, "browse_messages", 1, query="needle", cursor=cursor)
        )
        assert rejected["rejected"] and bob_page.last_sequence == 0
        assert rejected["diagnostics"][0]["code"] == "stale-cursor"
        filtered = await preview._action(
            bob_page.id, action_body(bob_page, "browse_messages", 1, query="needle")
        )
        _assert_navigation(filtered["result"], [public], False, False)
        numeric = await preview._action(
            bob_page.id, action_body(bob_page, "browse_messages", 2, query=str(private[0].id))
        )
        _assert_navigation(numeric["result"], [], False, False)


@pytest.mark.asyncio
async def test_text_navigation_matches_rendered_summary_not_hidden_raw_fields(env, channel, alice):
    message = env.backend.create_message(
        channel.id,
        env.bot.user.id,
        "Visible Café ||classifiedneedle|| " + "x" * 300 + "tailneedle",
        author_name="Aliasneedle",
        embeds=[{"title": "embedneedle"}],
        components=[
            {
                "type": 1,
                "components": [{"type": 2, "style": 1, "label": "Labelneedle", "custom_id": "rawneedle"}],
            }
        ],
        broadcast=False,
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        for sequence, query in enumerate(
            [
                "CAFE\u0301",
                "aliasneedle",
                "labelneedle",
                "button",
                "components",
                "embeds",
                message.timestamp[:10],
                "classifiedneedle",
                "tailneedle",
                "embedneedle",
                "rawneedle",
                str(message.id),
            ],
            1,
        ):
            response = await preview._action(
                page.id, action_body(page, "browse_messages", sequence, query=query)
            )
            assert not response["rejected"]
            hidden = query in {"classifiedneedle", "tailneedle", "embedneedle", "rawneedle"}
            _assert_navigation(response["result"], [] if hidden else [message], False, False)


@pytest.mark.asyncio
async def test_text_navigation_rechecks_sparse_matches_and_insertions_between_pages(env, channel, alice):
    native = env.bot.get_channel(channel.id)
    messages = [
        await native.send(f"{'needle' if number % 2 == 0 else 'unrelated'} {number}") for number in range(101)
    ]
    matches = messages[::2]
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._open_page(alice.id)
        first = await preview._action(page.id, action_body(page, "browse_messages", 1, query="needle"))
        _assert_navigation(first["result"], matches[:50], False, True)
        cursor = first["result"]["navigation"]["nextCursor"]
        await matches[-1].edit(content="no longer a match")
        inserted = await native.send("new needle")
        next_page = await preview._action(
            page.id, action_body(page, "browse_messages", 2, query="needle", cursor=cursor)
        )
        _assert_navigation(next_page["result"], [inserted], True, False)
        previous = next_page["result"]["navigation"]["previousCursor"]
        await messages[-2].edit(content="newly matching needle")
        back = await preview._action(
            page.id, action_body(page, "browse_messages", 3, query="needle", cursor=previous)
        )
        _assert_navigation(back["result"], [*matches[1:50], messages[-2]], True, True)
