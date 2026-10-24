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
