import asyncio
from types import SimpleNamespace

import pytest

from simcord import _runtime, _settlement
from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.backend.models import Poll, PollAnswer
from simcord.backend.ops import base


def _world():
    backend = Backend()
    guild = backend.create_guild("world")
    channel = backend.create_channel(guild.id, "history")
    return backend, guild, channel


async def test_targeted_wake_checks_later_tasks_and_preserves_native_cancellation():
    loop = asyncio.get_running_loop()
    first_waiter = loop.create_future()
    target_waiter = loop.create_future()

    async def wait(future):
        return await future

    first = asyncio.create_task(wait(first_waiter))
    target = asyncio.create_task(wait(target_waiter))
    await asyncio.sleep(0)
    state = SimpleNamespace(work=SimpleNamespace(tasks={first: None, target: None}, external_waits={target}))
    handle = loop.call_later(3600, target_waiter.set_result, "completed")
    record = _runtime._CallbackRecord(handle, handle.when(), "target", wake_targets=(target_waiter,))
    try:
        assert _settlement._wakes_recognized_wait(state, record)
        handle.cancel()
        assert not _settlement._wakes_recognized_wait(state, record)
        record.handle = None
        assert not _settlement._wakes_recognized_wait(state, record)
        loop.call_soon(target_waiter.set_result, "completed")
        assert await target == "completed"
        assert not first.done()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert first_waiter.cancelled()
    finally:
        handle.cancel()
        for task in (first, target):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, target, return_exceptions=True)


def test_replacement_and_edits_preserve_numeric_message_order():
    backend, _, channel = _world()
    for message_id, content in ((30, "thirty"), (10, "old"), (20, "twenty"), (10, "replacement")):
        prepared = backend.prepare_message(channel.id, backend.bot_user.id, content)
        prepared.id = message_id
        backend.commit_message(prepared, broadcast=False)
    assert [(m.id, m.content) for m in backend.select_messages(channel.id)] == [
        (10, "replacement"),
        (20, "twenty"),
        (30, "thirty"),
    ]
    backend.edit_message(channel.id, 20, {"content": "edited"})
    assert [m.content for m in backend.select_messages(channel.id)] == ["replacement", "edited", "thirty"]


def test_equal_id_replacement_retires_old_owner_and_pin_membership():
    backend, _, channel = _world()
    for flags, owner, pinned in (
        (0, None, True),
        (64, "123", True),
        (64, "456", False),
        (64, None, True),
        (0, None, False),
    ):
        replacement = backend.prepare_message(
            channel.id,
            backend.bot_user.id,
            "replacement",
            flags=flags,
            interaction_metadata=None if owner is None else {"user": {"id": owner}},
        )
        replacement.id = 10
        replacement.pinned = pinned
        backend.commit_message(replacement, broadcast=False)
        for viewer in (None, 123, 456):
            visible = flags == 0 or owner == str(viewer)
            expected = [replacement] if visible else []
            assert backend.select_messages(channel.id, viewer_id=viewer) == expected
            assert backend.select_messages(channel.id, viewer_id=viewer, pinned=True) == (
                expected if pinned else []
            )


@pytest.mark.parametrize("remove_guild", [False, True])
def test_channel_retirement_clears_mixed_membership_and_pending_deadlines(remove_guild):
    backend, guild, channel = _world()
    thread = backend.create_thread(channel.id, "thread", backend.bot_user.id)
    other_guild = backend.create_guild("other")
    survivor_channel = backend.create_channel(other_guild.id, "survivor")
    shared = Poll("Question", [PollAnswer(1, "Yes")], backend.iso_after(1))
    removed = []
    for flags, owner, pinned in ((0, None, True), (64, "123", True), (64, "456", False), (64, None, True)):
        message = backend.prepare_message(
            channel.id,
            backend.bot_user.id,
            "removed",
            flags=flags,
            interaction_metadata=None if owner is None else {"user": {"id": owner}},
            poll=shared,
        )
        message.pinned = pinned
        removed.append(backend.commit_message(message, broadcast=False))
    pending = backend.create_message(
        thread.id, backend.bot_user.id, poll=Poll("Pending", [PollAnswer(1, "Yes")], backend.iso_after(2))
    )
    survivor = backend.create_message(survivor_channel.id, backend.bot_user.id, "keep", poll=shared)
    events = []
    backend.subscribers.append(lambda event, payload: events.append(event))
    if remove_guild:
        backend.remove_guild(guild.id)
        expected_events = ["GUILD_DELETE"]
    else:
        backend.delete_channel(channel.id)
        backend.delete_channel(thread.id)
        expected_events = ["CHANNEL_DELETE", "THREAD_DELETE"]
    assert events == expected_events
    for channel_id in (channel.id, thread.id):
        assert channel_id not in backend.messages
        assert channel_id not in backend._channel_order
        assert not backend.select_messages(channel_id, viewer_id=123)
        assert not backend.select_messages(channel_id, viewer_id=456, pinned=True)
        assert not any(key[0] == channel_id for key in backend._message_ids)
        assert not any(key[0] == channel_id for key in backend._poll_deadlines._live)
    assert all(message.id not in backend._message_order for message in [*removed, pending])
    assert backend.select_messages(survivor_channel.id) == [survivor]
    assert not pending.poll.finalized and not shared.finalized
    assert backend.next_poll_deadline().isoformat() == backend.iso_after(1)
    backend.advance_clock(3)
    backend.expire_due_polls()
    assert shared.finalized and not pending.poll.finalized
    assert backend.next_poll_deadline() is None
    with pytest.raises(BackendError):
        backend.delete_channel(channel.id)


def test_emit_detaches_canonical_and_history_but_shares_ordered_live_envelope():
    backend, _, channel = _world()
    message = backend.create_message(channel.id, backend.bot_user.id, embeds=[{"description": "canonical"}])
    source = {"embeds": message.embeds}
    seen = []

    def first(event, payload):
        seen.append(("first", event, payload))
        if event == "OUTER":
            payload["embeds"][0]["description"] = "subscriber"
            backend.emit("INNER", {"value": [1]})

    def second(event, payload):
        seen.append(("second", event, payload))

    backend.subscribers.extend([first, second])
    start = len(backend.transcript)
    backend.emit("OUTER", source)
    assert [(listener, event) for listener, event, _ in seen] == [
        ("first", "OUTER"),
        ("first", "INNER"),
        ("second", "INNER"),
        ("second", "OUTER"),
    ]
    assert seen[0][2] is seen[-1][2]
    assert seen[-1][2]["embeds"][0]["description"] == "subscriber"
    assert message.embeds[0]["description"] == "canonical"
    assert backend.transcript[start][2] == {"embeds": [{"description": "canonical"}]}
    source["embeds"][0]["description"] = "later canonical edit"
    assert backend.transcript[start][2]["embeds"][0]["description"] == "canonical"
    backend.transcript[start][2]["embeds"][0]["description"] = "history edit"
    assert seen[-1][2]["embeds"][0]["description"] == "subscriber"


def test_emit_subscriber_failure_preserves_committed_state_and_partial_delivery():
    backend, _, channel = _world()
    seen = []

    def fail(event, payload):
        if event == "MESSAGE_CREATE":
            seen.append("first")
            payload["content"] = "envelope only"
            raise RuntimeError("subscriber")

    backend.subscribers.extend([fail, lambda event, payload: seen.append("later")])
    with pytest.raises(RuntimeError, match="subscriber"):
        backend.create_message(channel.id, backend.bot_user.id, "committed")
    assert seen == ["first"]
    assert [message.content for message in backend.select_messages(channel.id)] == ["committed"]
    assert backend.transcript[-1][1] == "MESSAGE_CREATE"
    assert backend.transcript[-1][2]["content"] == "committed"


def test_history_copy_failure_keeps_commit_without_record_or_delivery(monkeypatch):
    backend, _, channel = _world()
    seen = []
    backend.subscribers.append(lambda event, payload: seen.append(event))
    copy = base.deepcopy
    copies = 0

    def fail_history(value):
        nonlocal copies
        copies += 1
        if copies == 2:
            raise RuntimeError("history snapshot")
        return copy(value)

    monkeypatch.setattr(base, "deepcopy", fail_history)
    start = len(backend.transcript)
    with pytest.raises(RuntimeError, match="history snapshot"):
        backend.create_message(channel.id, backend.bot_user.id, "committed")
    assert len(backend.transcript) == start
    assert seen == []
    assert [message.content for message in backend.select_messages(channel.id)] == ["committed"]
    monkeypatch.setattr(base, "deepcopy", copy)
    backend.create_message(channel.id, backend.bot_user.id, "next")
    assert seen == ["MESSAGE_CREATE"]
    assert [message.content for message in backend.select_messages(channel.id)] == ["committed", "next"]
