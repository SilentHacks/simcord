from copy import deepcopy
from datetime import timedelta
from io import BytesIO

import discord
import pytest

from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.backend.models import Poll, PollAnswer
from simcord.components import COMPONENTS_V2_FLAG
from simcord.http import router


def _world():
    backend = Backend()
    guild = backend.create_guild("world")
    channel = backend.create_channel(guild.id, "history")
    return backend, guild, channel


def test_omitted_components_follow_effective_normalization_policy():
    backend, _, channel = _world()
    tree = [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Go", "custom_id": "go"}]}]
    message = backend.create_message(channel.id, backend.bot_user.id, components=tree)
    previous = message.components
    assert previous[0]["id"] == 0
    backend.edit_message(channel.id, message.id, {"flags": COMPONENTS_V2_FLAG})
    assert message.components is not previous
    assert previous[0]["id"] == 0
    assert message.components[0]["id"] > 0
    assert message.components[0]["components"][0]["id"] > 0
    normalized = message.components
    backend.edit_message(channel.id, message.id, {})
    assert message.components is normalized
    before = deepcopy(message)
    with pytest.raises(BackendError):
        backend.edit_message(channel.id, message.id, {"content": "forbidden"})
    assert message == before
    assert tree[0].get("id") is None


def test_rest_omitted_v2_components_are_normalized_once_and_atomic():
    backend, _, channel = _world()
    tree = [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Go", "custom_id": "go"}]}]
    message = backend.create_message(channel.id, backend.bot_user.id, components=tree)
    response = router.dispatch(
        backend, "PATCH", f"/channels/{channel.id}/messages/{message.id}", json={"flags": COMPONENTS_V2_FLAG}
    )
    assert response["components"][0]["id"] > 0
    assert message.flags & COMPONENTS_V2_FLAG
    before = deepcopy(message)
    with pytest.raises(BackendError):
        router.dispatch(
            backend,
            "PATCH",
            f"/channels/{channel.id}/messages/{message.id}",
            json={"content": "rejected"},
            files=[discord.File(BytesIO(b"secret"), filename="secret.txt")],
        )
    assert not backend.cdn._blobs
    assert message == before


def test_upload_read_and_metadata_failures_do_not_install_partial_assets():
    backend, _, channel = _world()

    class BrokenRead(BytesIO):
        def read(self, *args, **kwargs):
            raise OSError("unreadable")

    files = [
        discord.File(BytesIO(b"first"), filename="first.txt"),
        discord.File(BrokenRead(b"second"), filename="second.txt"),
    ]
    with pytest.raises(OSError, match="unreadable"):
        router.dispatch(
            backend, "POST", f"/channels/{channel.id}/messages", json={"content": "send"}, files=files
        )
    assert not backend.cdn._blobs
    assert not backend.messages[channel.id]
    with pytest.raises(TypeError):
        backend.cdn.store_attachment(1, channel.id, "bad.txt", None, None)
    assert not backend.cdn._blobs


def test_numeric_selector_live_visibility_cursors_pins_and_deletion():
    backend, _, channel = _world()
    author = backend.bot_user.id
    messages = []
    for value in (30, 10, 20):
        message = backend.prepare_message(channel.id, author, str(value))
        message.id = value  # Detached preparation permits fixture identity arrangement.
        messages.append(backend.commit_message(message, broadcast=False))
    own = backend.create_message(
        channel.id, author, "own", flags=64, interaction_metadata={"user": {"id": "123"}}
    )
    backend.create_message(
        channel.id, author, "other", flags=64, interaction_metadata={"user": {"id": "456"}}
    )
    backend.create_message(channel.id, author, "unowned", flags=64)
    assert [m.id for m in backend.select_messages(channel.id)] == [10, 20, 30]
    assert [m.id for m in backend.select_messages(channel.id, viewer_id=123)] == [10, 20, 30, own.id]
    assert backend.message_count(channel.id, viewer_id=123) == 4
    assert backend.message_position(channel.id, 25, viewer_id=123) == 2
    assert [m.id for m in backend.select_messages(channel.id, after=10, before=30, limit=1)] == [20]
    backend.set_pinned(channel.id, 20, True)
    assert [m.id for m in backend.select_messages(channel.id, pinned=True)] == [20]
    response = router.dispatch(
        backend, "GET", f"/channels/{channel.id}/messages", params={"around": 15, "limit": 2}
    )
    assert [int(m["id"]) for m in response] == [20, 10]
    assert backend.bulk_delete_messages(channel.id, [10, 10, 20]) == [10, 20]
    assert not backend.select_messages(channel.id, pinned=True)
    backend.delete_message(channel.id, 30)
    assert backend.message_position(channel.id, own.id, viewer_id=123) == 0
    backend.delete_channel(channel.id)
    assert not backend.select_messages(channel.id, viewer_id=123)


def test_deadline_failure_keeps_unprocessed_polls_and_deleted_channels_retire():
    backend, _, channel = _world()
    polls = [
        backend.create_message(
            channel.id,
            backend.bot_user.id,
            poll=Poll("Question", [PollAnswer(1, "Yes")], backend.iso_after(1)),
        )
        for _ in range(2)
    ]
    backend.advance_clock(2)

    def fail(event, payload):
        if event == "MESSAGE_UPDATE":
            raise RuntimeError("subscriber")

    backend.subscribers.append(fail)
    with pytest.raises(RuntimeError, match="subscriber"):
        backend.expire_due_polls()
    backend.subscribers.remove(fail)
    assert polls[0].poll.finalized and not polls[1].poll.finalized
    assert backend.next_poll_deadline() is not None
    backend.expire_due_polls()
    assert polls[1].poll.finalized
    pending = backend.create_message(
        channel.id, backend.bot_user.id, poll=Poll("Question", [PollAnswer(1, "Yes")], backend.iso_after(1))
    )
    backend.delete_channel(channel.id)
    assert backend.next_poll_deadline() is None
    assert not pending.poll.finalized


def test_event_delayed_end_error_and_reentrant_reschedule():
    backend, guild, _ = _world()
    first = backend.create_scheduled_event(
        guild.id,
        name="broken end",
        entity_type=3,
        scheduled_start_time=backend.iso_after(1),
        scheduled_end_time="bad",
    )
    second = backend.create_scheduled_event(
        guild.id, name="later", entity_type=3, scheduled_start_time=backend.iso_after(1)
    )
    backend.advance_clock(2)
    with pytest.raises(ValueError):
        backend.activate_due_scheduled_events()
    assert first.status == 2 and second.status == 1
    backend.edit_scheduled_event(guild.id, first.id, {"scheduled_end_time": backend.iso_after(10)})

    def reschedule(event, payload):
        if event == "GUILD_SCHEDULED_EVENT_UPDATE" and int(payload["id"]) == second.id:
            backend.edit_scheduled_event(guild.id, first.id, {"scheduled_end_time": backend.iso_after(20)})

    backend.subscribers.append(reschedule)
    backend.activate_due_scheduled_events()
    backend.subscribers.remove(reschedule)
    assert second.status == 2
    assert backend.next_scheduled_event_deadline().isoformat() == backend.iso_after(20)
    backend.advance_clock(20)
    backend.activate_due_scheduled_events()
    assert first.status == 3


def test_entitlement_owner_order_raw_dual_owners_and_callback_exception():
    backend = Backend()
    sku = backend.create_sku("subscription")
    now = backend.clock.now()
    personal = backend.seed_entitlement(
        sku.id, user_id=123, id=300, scheduled_end_at=now + timedelta(seconds=1)
    )
    dual = backend.seed_entitlement(
        sku.id, user_id=123, guild_id=456, id=100, scheduled_end_at=now + timedelta(seconds=1)
    )
    guild = backend.seed_entitlement(sku.id, guild_id=456, id=200)
    assert [int(e["id"]) for e in backend.interaction_entitlements(123, 456)] == [
        dual.id,
        guild.id,
        personal.id,
    ]
    assert [int(e["id"]) for e in backend.interaction_entitlements(123)] == [personal.id]
    assert [e.id for e in backend.iter_entitlements(user_id=123)] == [300, 100]
    assert [e.id for e in backend.iter_entitlements(after=100, newest_first=False)] == [200, 300]
    backend.advance_clock(2)

    def fail(event, payload):
        if event == "ENTITLEMENT_UPDATE":
            raise RuntimeError("subscriber")

    backend.subscribers.append(fail)
    with pytest.raises(RuntimeError):
        backend.process_entitlement_deadlines()
    backend.subscribers.remove(fail)
    assert personal.ends_at is not None and dual.ends_at is None
    backend.process_entitlement_deadlines()
    assert dual.ends_at is not None
    assert backend.next_entitlement_deadline() is None


async def test_rejected_driver_uploads_leave_no_assets(env, channel, alice):
    before = dict(env.backend.cdn._blobs)
    with pytest.raises(BackendError):
        await alice.send(channel, "x" * 2001, attachments=[("member.txt", b"member")])
    assert env.backend.cdn._blobs == before
    webhook = env.guild.create_webhook(channel)
    with pytest.raises(BackendError):
        await webhook.send("x" * 2001, attachments=[("webhook.txt", b"webhook")])
    assert env.backend.cdn._blobs == before


def test_reentrant_event_edit_reaches_later_resource_in_same_traversal():
    backend, guild, _ = _world()
    first = backend.create_scheduled_event(
        guild.id, name="first", entity_type=3, scheduled_start_time=backend.iso_after(1)
    )
    later = backend.create_scheduled_event(
        guild.id, name="later", entity_type=3, scheduled_start_time=backend.iso_after(100)
    )
    backend.advance_clock(2)

    def change_later(event, payload):
        if event == "GUILD_SCHEDULED_EVENT_UPDATE" and int(payload["id"]) == first.id:
            backend.edit_scheduled_event(guild.id, later.id, {"scheduled_start_time": backend.now_iso()})

    backend.subscribers.append(change_later)
    backend.activate_due_scheduled_events()
    backend.subscribers.remove(change_later)
    assert first.status == later.status == 2
    assert backend.next_scheduled_event_deadline() is None


def test_naive_poll_comparison_errors_remain_delayed():
    backend, _, channel = _world()
    naive = backend.clock.now().replace(tzinfo=None)
    message = backend.create_message(
        channel.id,
        backend.bot_user.id,
        poll=Poll("Question", [PollAnswer(1, "Yes")], naive.isoformat()),
    )
    assert backend.next_poll_deadline() == naive
    with pytest.raises(TypeError):
        backend.expire_due_polls()
    assert not message.poll.finalized
    backend.create_message(
        channel.id,
        backend.bot_user.id,
        poll=Poll("Question", [PollAnswer(1, "Yes")], backend.iso_after(1)),
    )
    with pytest.raises(TypeError):
        backend.next_poll_deadline()
    backend.expire_poll(channel.id, message.id)
    assert backend.next_poll_deadline().isoformat() == backend.iso_after(1)


def test_missing_event_start_keeps_processing_error_boundary_and_retry():
    backend, guild, _ = _world()
    event = backend.create_scheduled_event(
        guild.id, name="missing start", entity_type=3, scheduled_start_time=None
    )
    assert backend.next_scheduled_event_deadline() is None
    with pytest.raises(TypeError):
        backend.activate_due_scheduled_events()
    assert event.status == 1
    backend.edit_scheduled_event(guild.id, event.id, {"scheduled_start_time": backend.now_iso()})
    backend.activate_due_scheduled_events()
    assert event.status == 2


def test_deadline_replacements_cancel_old_actions_without_early_transition():
    backend, guild, _ = _world()
    event = backend.create_scheduled_event(
        guild.id, name="rescheduled", entity_type=3, scheduled_start_time=backend.iso_after(1)
    )
    for offset in range(2, 150):
        backend.edit_scheduled_event(guild.id, event.id, {"scheduled_start_time": backend.iso_after(offset)})
    backend.advance_clock(148)
    backend.activate_due_scheduled_events()
    assert event.status == 1
    assert backend.next_scheduled_event_deadline().isoformat() == backend.iso_after(1)
    backend.delete_scheduled_event(guild.id, event.id)
    assert backend.next_scheduled_event_deadline() is None


def test_shared_poll_state_does_not_leave_a_finalized_deadline_live():
    backend, _, channel = _world()
    poll = Poll("Question", [PollAnswer(1, "Yes")], backend.iso_after(1))
    first = backend.create_message(channel.id, backend.bot_user.id, poll=poll)
    second = backend.create_message(channel.id, backend.bot_user.id, poll=poll)
    backend.advance_clock(2)
    backend.expire_due_polls()
    assert first.poll.finalized and second.poll.finalized
    assert backend.next_poll_deadline() is None


@pytest.mark.parametrize(
    ("kinds", "error"),
    [(("aware", "naive", "invalid"), TypeError), (("aware", "invalid", "naive"), ValueError)],
)
def test_deadline_errors_follow_resource_traversal_order(kinds, error):
    backend, _, channel = _world()
    values = {
        "aware": backend.iso_after(1),
        "naive": backend.clock.now().replace(tzinfo=None).isoformat(),
        "invalid": "invalid",
    }
    for kind in kinds:
        backend.create_message(
            channel.id, backend.bot_user.id, poll=Poll("Question", [PollAnswer(1, "Yes")], values[kind])
        )
    with pytest.raises(error):
        backend.next_poll_deadline()
