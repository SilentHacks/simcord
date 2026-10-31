"""Ownership at real request, return, event, and edit preparation seams."""

import sys
from copy import deepcopy
from io import BytesIO

import discord
import pytest

from simcord._payload import detach
from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.backend.models import Interaction, Poll, PollAnswer
from simcord.backend.ops import base, messages
from simcord.http import _helpers, router


def _world():
    backend = Backend()
    guild = backend.create_guild("world")
    channel = backend.create_channel(guild.id, "ownership")
    return backend, guild, channel


def _components():
    return [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Go", "custom_id": "go"}]}]


def test_create_and_direct_edit_detach_inputs_but_keep_shared_poll():
    backend, _, channel = _world()
    embeds = [{"footer": {"text": "original"}}]
    attachments = [{"id": "1", "extra": {"value": "original"}}]
    components = _components()
    metadata = {"id": "123", "user": {"id": "456"}}
    poll = Poll("Question", [PollAnswer(1, "Yes")], backend.iso_after(60))
    message = backend.create_message(
        channel.id,
        backend.bot_user.id,
        "original",
        embeds=embeds,
        attachments=attachments,
        components=components,
        interaction_metadata=metadata,
        poll=poll,
    )
    embeds[0]["footer"]["text"] = "caller"
    attachments[0]["extra"]["value"] = "caller"
    components[0]["components"][0]["label"] = "caller"
    metadata["user"]["id"] = "caller"
    assert message.embeds[0]["footer"]["text"] == "original"
    assert message.attachments[0]["extra"]["value"] == "original"
    assert message.components[0]["components"][0]["label"] == "Go"
    assert message.interaction_metadata["user"]["id"] == "456"
    assert message.poll is poll
    backend.edit_message(
        channel.id, message.id, {"embeds": embeds, "attachments": attachments, "components": components}
    )
    embeds[0]["footer"]["text"] = "later"
    attachments[0]["extra"]["value"] = "later"
    components[0]["components"][0]["label"] = "later"
    assert message.embeds[0]["footer"]["text"] == "caller"
    assert message.attachments[0]["extra"]["value"] == "caller"
    assert message.components[0]["components"][0]["label"] == "caller"
    assert message.poll is poll


@pytest.mark.parametrize("method", ["POST", "PATCH", "GET"])
def test_http_nested_responses_are_independent(method):
    backend, _, channel = _world()
    message = backend.create_message(
        channel.id,
        backend.bot_user.id,
        "original",
        embeds=[{"footer": {"text": "original"}}],
        components=_components(),
        attachments=[{"id": "1", "extra": {"value": "original"}}],
        interaction_metadata={"id": "123", "user": {"id": "456"}},
    )
    path = f"/channels/{channel.id}/messages"
    body = {"content": "original", "embeds": [{"footer": {"text": "original"}}], "components": _components()}
    if method != "POST":
        path += f"/{message.id}"
    response = router.dispatch(backend, method, path, json=body if method != "GET" else None)
    stored = backend.get_message(channel.id, int(response["id"]))
    before = deepcopy(stored)
    response["embeds"][0]["footer"]["text"] = "consumer"
    response["components"][0]["components"][0]["label"] = "consumer"
    if method != "POST":
        response["attachments"][0]["extra"]["value"] = "consumer"
        response["interaction_metadata"]["user"]["id"] = "consumer"
    assert stored == before


async def test_sdk_send_edit_fetch_and_webhook_embeds_are_detached(env, channel):
    await env.settle()
    sdk_channel = env.bot.get_channel(channel.id)
    embed = discord.Embed(title="original").set_footer(text="original")
    sent = await sdk_channel.send(embed=embed)
    sent.embeds[0].to_dict()["footer"]["text"] = "consumer"
    assert env.backend.get_message(channel.id, sent.id).embeds[0]["footer"]["text"] == "original"
    edited = await sent.edit(embed=embed)
    edited.embeds[0].to_dict()["footer"]["text"] = "edited consumer"
    fetched = await sdk_channel.fetch_message(sent.id)
    assert fetched.embeds[0].footer.text == "original"
    fetched.embeds[0].to_dict()["footer"]["text"] = "fetched consumer"
    assert env.backend.get_message(channel.id, sent.id).embeds[0]["footer"]["text"] == "original"
    hook = await sdk_channel.create_webhook(name="ownership")
    hooked = await hook.send(embed=embed, wait=True)
    hooked.embeds[0].to_dict()["footer"]["text"] = "hook consumer"
    assert (await hook.fetch_message(hooked.id)).embeds[0].footer.text == "original"


async def test_sdk_interaction_and_followup_embeds_are_detached(env, channel, alice):
    observed = []

    @env.bot.tree.command(description="Check response ownership")
    async def owned(interaction: discord.Interaction):
        embed = discord.Embed(title="original").set_footer(text="original")
        await interaction.response.send_message(embed=embed)
        original = await interaction.original_response()
        original.embeds[0].to_dict()["footer"]["text"] = "consumer"
        edited = await interaction.edit_original_response(embed=embed)
        edited.embeds[0].to_dict()["footer"]["text"] = "edited consumer"
        followup = await interaction.followup.send(embed=embed, wait=True)
        followup.embeds[0].to_dict()["footer"]["text"] = "followup consumer"
        observed.extend([original.id, followup.id])

    await env.bot.tree.sync()
    await alice.slash(channel, "owned")
    assert len(observed) == 2
    for message_id in observed:
        assert env.backend.get_message(channel.id, message_id).embeds[0]["footer"]["text"] == "original"


def test_http_transcript_and_request_log_are_independent_before_faults():
    backend, _, channel = _world()
    body = {"embeds": [{"footer": {"text": "original"}}]}
    params = {"nested": ["original"]}
    backend.faults.append(
        {"method": "POST", "path": "*", "times": 1, "status": 503, "code": 0, "message": "fault"}
    )
    with pytest.raises(BackendError):
        router.dispatch(
            backend, "POST", f"/channels/{channel.id}/messages", json=body, params=params, reason="reason"
        )
    entry = backend.http_requests[-1]
    history = backend.transcript[-1][2]
    body["embeds"][0]["footer"]["text"] = "caller"
    params["nested"].append("caller")
    assert history["embeds"][0]["footer"]["text"] == "original"
    entry.json["embeds"][0]["footer"]["text"] = "log consumer"
    assert history["embeds"][0]["footer"]["text"] == "original"
    assert entry.params == {"nested": ["original"]}
    assert entry.reason == "reason"
    array = [{"nested": ["original"]}]
    with pytest.raises(router.RouteNotImplemented):
        router.dispatch(backend, "PATCH", "/unsupported", json=array)
    array[0]["nested"].append("caller")
    assert backend.http_requests[-1].json == [{"nested": ["original"]}]
    assert backend.transcript[-1][2] is None


def test_shared_delivery_envelope_is_detached_and_response_uses_current_state():
    backend, _, channel = _world()
    seen = []

    def first(event, payload):
        if event == "MESSAGE_CREATE":
            payload["embeds"][0]["footer"]["text"] = "subscriber"
            backend.edit_message(channel.id, int(payload["id"]), {"content": "synchronous edit"})

    def second(event, payload):
        if event == "MESSAGE_CREATE":
            seen.append(payload["embeds"][0]["footer"]["text"])

    backend.subscribers.extend([first, second])
    response = router.dispatch(
        backend,
        "POST",
        f"/channels/{channel.id}/messages",
        json={"embeds": [{"footer": {"text": "original"}}]},
    )
    assert response["content"] == "synchronous edit"
    assert response["embeds"][0]["footer"]["text"] == "original"
    assert seen == ["subscriber"]
    events = [(event, payload) for kind, event, payload in backend.transcript if kind == "GATEWAY"]
    assert [event for event, _ in events[-2:]] == ["MESSAGE_CREATE", "MESSAGE_UPDATE"]
    assert events[-2][1]["embeds"][0]["footer"]["text"] == "original"


def test_subscriber_exception_preserves_commit_history_and_partial_delivery():
    backend, _, channel = _world()
    seen = []

    def fail(event, payload):
        seen.append("first")
        payload["embeds"][0]["footer"]["text"] = "subscriber"
        raise RuntimeError("subscriber")

    backend.subscribers.extend([fail, lambda *_: seen.append("second")])
    with pytest.raises(RuntimeError, match="subscriber"):
        backend.create_message(channel.id, backend.bot_user.id, embeds=[{"footer": {"text": "original"}}])
    assert seen == ["first"]
    message = next(iter(backend.messages[channel.id].values()))
    assert message.embeds[0]["footer"]["text"] == "original"
    assert backend.transcript[-1][2]["embeds"][0]["footer"]["text"] == "original"
    backend.subscribers.clear()
    assert backend.select_messages(channel.id) == [message]


@pytest.mark.parametrize("boundary", ["envelope", "history", "response"])
def test_postcommit_copy_failure_does_not_replay_or_rollback(monkeypatch, boundary):
    backend, _, channel = _world()
    seen = []
    backend.subscribers.append(lambda *_: seen.append("delivered"))
    original = base.detach

    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("snapshot")

    def fail_copy(value):
        raise RuntimeError("snapshot")

    def poison_envelope(value):
        envelope = original(value)
        envelope["uncopyable"] = Uncopyable()
        return envelope

    if boundary == "response":
        monkeypatch.setattr(_helpers, "detach", fail_copy)
    else:
        monkeypatch.setattr(base, "detach", fail_copy if boundary == "envelope" else poison_envelope)
    before = len(backend.transcript)
    with pytest.raises(RuntimeError, match="snapshot"):
        router.dispatch(backend, "POST", f"/channels/{channel.id}/messages", json={"content": "committed"})
    message = next(iter(backend.messages[channel.id].values()))
    assert message.content == "committed"
    assert backend.select_messages(channel.id) == [message]
    assert seen == (["delivered"] if boundary == "response" else [])
    assert len(backend.transcript) == before + (2 if boundary == "response" else 1)
    monkeypatch.undo()
    assert (
        router.dispatch(backend, "GET", f"/channels/{channel.id}/messages/{message.id}")["content"]
        == "committed"
    )
    assert len(backend.messages[channel.id]) == 1


@pytest.mark.parametrize("http", [False, True])
def test_ingress_copy_failure_precedes_deferred_index_and_cdn_changes(http):
    backend, guild, channel = _world()
    record = Interaction(123, "token", 2, channel.id, guild.id, backend.bot_user.id)
    message = backend.create_message(
        channel.id,
        backend.bot_user.id,
        flags=128,
        interaction_metadata={"id": "123", "user": {"id": str(record.user_id)}},
    )
    record.defer(message.id, ephemeral=False)
    backend.interactions[record.id] = record
    before = deepcopy(message)

    class Uncopyable(str):
        def __deepcopy__(self, memo):
            raise RuntimeError("ingress")

    embeds = [{"footer": {"text": Uncopyable("original")}}]
    with pytest.raises(RuntimeError, match="ingress"):
        if http:
            router.dispatch(
                backend,
                "PATCH",
                f"/channels/{channel.id}/messages/{message.id}",
                json={"embeds": embeds},
                files=[discord.File(BytesIO(b"asset"), filename="asset.txt")],
            )
        else:
            backend.edit_message(channel.id, message.id, {"embeds": embeds})
    assert message == before
    assert record.loading
    assert backend.select_messages(channel.id) == [message]
    assert not backend.cdn._blobs


@pytest.mark.parametrize("http", [False, True])
def test_content_edits_preserve_message_poll_and_omitted_components(http):
    backend, _, channel = _world()
    poll = Poll("Question", [PollAnswer(1, "Yes")], backend.iso_after(60))
    message = backend.create_message(
        channel.id, backend.bot_user.id, "original", components=_components(), poll=poll
    )
    components = deepcopy(message.components)
    if http:
        router.dispatch(
            backend, "PATCH", f"/channels/{channel.id}/messages/{message.id}", json={"content": "edited"}
        )
    else:
        assert backend.edit_message(channel.id, message.id, {"content": "edited"}) is message
    assert backend.get_message(channel.id, message.id) is message
    assert message.poll is poll
    assert message.components == components
    assert message.content == "edited"


@pytest.mark.parametrize("failure", ["read", "media"])
def test_http_edit_upload_failure_leaves_deferred_message_and_cdn_unchanged(failure):
    backend, guild, channel = _world()
    record = Interaction(123, "token", 2, channel.id, guild.id, backend.bot_user.id)
    message = backend.create_message(
        channel.id,
        backend.bot_user.id,
        flags=128,
        interaction_metadata={"id": "123", "user": {"id": str(record.user_id)}},
    )
    record.defer(message.id, ephemeral=False)
    backend.interactions[record.id] = record
    before = deepcopy(message)
    events = [event for event in backend.transcript if event[0] == "GATEWAY"]

    class Unreadable(BytesIO):
        def read(self, *args, **kwargs):
            raise RuntimeError("upload read")

    files = [
        discord.File(BytesIO(b"first"), filename="first.txt"),
        discord.File(Unreadable(b"second"), filename="second.txt"),
    ]
    body = {"attachments": [{"id": 0}, {"id": 1}]}
    if failure == "media":
        body.update(
            flags=1 << 15,
            components=[{"type": 13, "file": {"url": "attachment://missing.txt"}}],
        )
    with pytest.raises(
        RuntimeError if failure == "read" else BackendError,
        match="upload read" if failure == "read" else "has no uploaded file",
    ):
        router.dispatch(
            backend, "PATCH", f"/channels/{channel.id}/messages/{message.id}", json=body, files=files
        )
    assert backend.get_message(channel.id, message.id) is message
    assert message == before
    assert record.loading
    assert backend.select_messages(channel.id) == [message]
    assert not backend.cdn._blobs
    assert [event for event in backend.transcript if event[0] == "GATEWAY"] == events


def test_stable_edits_keep_order_pin_visibility():
    backend, _, channel = _world()
    public = backend.create_message(channel.id, backend.bot_user.id, "public")
    private = backend.create_message(
        channel.id, backend.bot_user.id, "private", flags=64, interaction_metadata={"user": {"id": "123"}}
    )
    backend.set_pinned(channel.id, private.id, True)
    visible_before = backend.select_messages(channel.id)
    own_before = backend.select_messages(channel.id, viewer_id=123)
    backend.edit_message(channel.id, public.id, {"content": "edited", "flags": 4})
    backend.edit_message(channel.id, private.id, {"content": "edited", "flags": 0})
    assert backend.select_messages(channel.id) == visible_before
    assert backend.select_messages(channel.id, viewer_id=123) == own_before
    assert backend.select_messages(channel.id, viewer_id=123, pinned=True) == [private]
    assert not backend.select_messages(channel.id, viewer_id=456, pinned=True)
    assert private.flags & 64


def test_http_retained_attachment_and_edit_body_are_detached():
    backend, _, channel = _world()
    message = backend.create_message(
        channel.id,
        backend.bot_user.id,
        "original",
        attachments=[{"id": "1", "filename": "original.txt", "extra": {"value": "original"}}],
    )
    body = {
        "embeds": [{"footer": {"text": "original"}}],
        "components": _components(),
        "attachments": [{"id": "1", "description": {"value": "original"}}],
    }
    router.dispatch(backend, "PATCH", f"/channels/{channel.id}/messages/{message.id}", json=body)
    body["embeds"][0]["footer"]["text"] = "caller"
    body["components"][0]["components"][0]["label"] = "caller"
    body["attachments"][0]["description"]["value"] = "caller"
    assert message.embeds[0]["footer"]["text"] == "original"
    assert message.components[0]["components"][0]["label"] == "Go"
    assert message.attachments[0]["description"]["value"] == "original"


def test_reply_ancestry_and_forward_snapshot_responses_are_detached():
    backend, _, channel = _world()
    source = backend.create_message(
        channel.id, backend.bot_user.id, embeds=[{"footer": {"text": "original"}}]
    )
    reply = backend.create_message(
        channel.id, backend.bot_user.id, "reply", reference={"message_id": str(source.id)}
    )
    response = router.dispatch(backend, "GET", f"/channels/{channel.id}/messages/{reply.id}")
    response["referenced_message"]["embeds"][0]["footer"]["text"] = "consumer"
    assert source.embeds[0]["footer"]["text"] == "original"
    forward = backend.create_message(
        channel.id,
        backend.bot_user.id,
        reference={"type": 1, "channel_id": str(channel.id), "message_id": str(source.id)},
    )
    response = router.dispatch(backend, "GET", f"/channels/{channel.id}/messages/{forward.id}")
    response["message_snapshots"][0]["message"]["embeds"][0]["footer"]["text"] = "consumer"
    assert forward.snapshots[0]["message"]["embeds"][0]["footer"]["text"] == "original"
    assert source.embeds[0]["footer"]["text"] == "original"


def test_create_copy_failure_does_not_install_uploads_or_canonical_state(monkeypatch):
    backend, _, channel = _world()
    embeds = [{"footer": {"text": "original"}}]
    original = messages.deepcopy

    def fail_embeds(value):
        if value is embeds:
            raise RuntimeError("ingress")
        return original(value)

    monkeypatch.setattr(messages, "deepcopy", fail_embeds)
    with pytest.raises(RuntimeError, match="ingress"):
        router.dispatch(
            backend,
            "POST",
            f"/channels/{channel.id}/messages",
            json={"embeds": embeds},
            files=[discord.File(BytesIO(b"asset"), filename="asset.txt")],
        )
    assert not backend.messages[channel.id]
    assert not backend.select_messages(channel.id)
    assert backend.get_channel(channel.id).last_message_id is None
    assert not backend.cdn._blobs


async def test_sdk_response_is_built_after_synchronous_backend_edit(env, channel):
    await env.settle()

    def edit(event, payload):
        if event == "MESSAGE_CREATE":
            env.backend.edit_message(channel.id, int(payload["id"]), {"content": "synchronous edit"})

    env.backend.subscribers.append(edit)
    try:
        response = await env.bot.get_channel(channel.id).send("before")
    finally:
        env.backend.subscribers.remove(edit)
    assert response.content == "synchronous edit"


@pytest.mark.parametrize("depth", [550, 750])
@pytest.mark.parametrize("subscribed", [False, True])
def test_long_reply_history_retains_complete_independent_ancestry(depth, subscribed):
    backend, _, channel = _world()
    recursion_limit = sys.getrecursionlimit()
    ancestry = []
    previous = None
    for index in range(depth):
        previous = backend.create_message(
            channel.id,
            backend.bot_user.id,
            str(index),
            embeds=[{"footer": {"text": "original"}}],
            reference={"message_id": str(previous.id)} if previous is not None else None,
            broadcast=False,
        )
        ancestry.append(previous)
    delivered = []
    if subscribed:
        backend.subscribers.append(lambda event, payload: delivered.append(payload))
    reply = backend.create_message(
        channel.id,
        backend.bot_user.id,
        "reply",
        reference={"message_id": str(previous.id)},
    )
    history = backend.transcript[-1][2]
    response = router.dispatch(backend, "GET", f"/channels/{channel.id}/messages/{reply.id}")
    graphs = [history, response, *delivered]
    oldest = []
    for graph in graphs:
        assert graph["id"] == str(reply.id)
        for ancestor in reversed(ancestry):
            graph = graph["referenced_message"]
            assert graph["id"] == str(ancestor.id)
            assert graph["embeds"][0]["footer"]["text"] == "original"
        assert "referenced_message" not in graph
        oldest.append(graph)
    oldest[0]["embeds"][0]["footer"]["text"] = "history"
    oldest[1]["embeds"][0]["footer"]["text"] = "response"
    assert ancestry[0].embeds[0]["footer"]["text"] == "original"
    if subscribed:
        assert oldest[2]["embeds"][0]["footer"]["text"] == "original"
        oldest[2]["embeds"][0]["footer"]["text"] = "subscriber"
    ancestry[0].embeds[0]["footer"]["text"] = "canonical"
    assert oldest[0]["embeds"][0]["footer"]["text"] == "history"
    assert oldest[1]["embeds"][0]["footer"]["text"] == "response"
    assert sys.getrecursionlimit() == recursion_limit


def test_wire_detachment_preserves_aliases_cycles_and_mapping_order():
    shared = {"nested": ["original"]}
    source = {"first": shared, "second": shared, "cycle": []}
    source["cycle"].extend([source, source["cycle"]])
    copied = detach(source)
    assert list(copied) == list(source)
    assert copied is not source
    assert copied["first"] is copied["second"]
    assert copied["first"] is not shared
    assert copied["cycle"][0] is copied
    assert copied["cycle"][1] is copied["cycle"]
    copied["first"]["nested"].append("consumer")
    assert shared["nested"] == ["original"]


def test_nonwire_detachment_uses_stdlib_hooks_once_with_original_traversal():
    observations = []

    class Custom:
        def __deepcopy__(self, memo):
            observations.append(list(memo[id(source)]))
            return {"copied": True}

    shared = {"custom": Custom()}
    source = {"first": shared, "second": shared, "tuple": (shared,)}
    copied = detach(source)
    assert observations == [[]]
    assert copied["first"] is copied["second"] is copied["tuple"][0]
    assert copied["first"]["custom"] == {"copied": True}


def test_nonwire_detachment_preserves_copy_failure():
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("custom copy")

    with pytest.raises(RuntimeError, match="custom copy"):
        detach({"plain": {"nested": []}, "custom": Uncopyable()})


@pytest.mark.parametrize("entry", ["root", "key", "atomic_value", "nested_container"])
@pytest.mark.parametrize("equality", ["spoof", "raise"])
@pytest.mark.parametrize("copy_failure", [False, True])
def test_detachment_type_eligibility_ignores_metaclass_equality(entry, equality, copy_failure):
    spoofed_type = list if entry in ("root", "nested_container") else str
    failure = RuntimeError("custom copy")

    class Meta(type):
        def __eq__(cls, other):
            if equality == "raise":
                raise AssertionError("metaclass equality must not determine eligibility")
            return other is spoofed_type

        __hash__ = type.__hash__

    class Custom(list, metaclass=Meta):
        __hash__ = object.__hash__

        def __deepcopy__(self, memo):
            if copy_failure:
                raise failure
            return Custom([deepcopy(self[0], memo), "copied by hook"])

    original = Custom([{"items": ["original"]}])
    if entry == "root":
        source = original
    elif entry == "key":
        source = {original: {"items": ["value"]}}
    else:
        source = {"custom": original}

    if copy_failure:
        for copier in (deepcopy, detach):
            with pytest.raises(RuntimeError) as caught:
                copier(source)
            assert caught.value is failure
        return

    expected = deepcopy(source)
    copied = detach(source)
    if entry == "root":
        expected_custom, copied_custom = expected, copied
    elif entry == "key":
        expected_custom, copied_custom = next(iter(expected)), next(iter(copied))
        copied[copied_custom]["items"].append("consumer")
        assert source[original]["items"] == ["value"]
    else:
        expected_custom, copied_custom = expected["custom"], copied["custom"]
    assert type(copied_custom) is Custom
    assert copied_custom == expected_custom == [{"items": ["original"]}, "copied by hook"]
    assert copied_custom is not original
    copied_custom[0]["items"].append("consumer")
    assert original == [{"items": ["original"]}]
    assert expected_custom[0]["items"] == ["original"]
