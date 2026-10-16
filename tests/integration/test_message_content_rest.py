"""REST content authorization is attachment-local, not the Gateway request bit."""

import discord
import pytest
from discord.ext import commands
from discord.http import Route

import simcord
from simcord.backend import serializers
from simcord.backend.models import Poll, PollAnswer, Reaction
from simcord.http.client import FakeWebhookAdapter


def _bot(message_content=False):
    intents = discord.Intents.default()
    intents.message_content = message_content
    return commands.Bot(command_prefix="!", intents=intents)


def _approval(content):
    if content is None:
        return None
    intents = discord.Intents.default()
    intents.message_content = content
    return intents


async def _request(env, method, path, **kwargs):
    return await env.bot.http.request(Route(method, path), **kwargs)


def _world(env):
    guild = env.create_guild()
    return guild.create_text_channel("general"), guild.add_member(env.create_user("alice"))


def _rich(env, channel, author_id, content="private"):
    return env.backend.create_message(
        channel.id,
        author_id,
        content,
        embeds=[{"description": "private embed"}],
        attachments=[
            env.backend.cdn.store_attachment(
                env.backend.snowflake(), channel.id, "secret.txt", b"secret", None
            )
        ],
        components=[
            {"type": 1, "components": [{"type": 2, "style": 1, "label": "Secret", "custom_id": "secret"}]}
        ],
        poll=Poll("Private question", [PollAnswer(1, "Yes")], env.backend.iso_after(3600)),
    )


def _hidden(payload):
    assert payload["content"] == ""
    for key in ("embeds", "attachments", "components"):
        assert payload[key] == []
    assert "poll" not in payload


@pytest.mark.parametrize(
    "approved,requested,visible",
    [(False, False, False), (True, False, True), (True, True, True), (None, False, True)],
)
async def test_rest_approval_is_independent_of_requested_gateway_content(approved, requested, visible):
    async with simcord.run(_bot(requested), approved_intents=_approval(approved)) as env:
        channel, alice = _world(env)
        message = _rich(env, channel, alice.id)
        message.reactions.append(Reaction("👍", [env.backend.bot_user.id]))
        message.poll.votes[1] = {env.backend.bot_user.id}
        payload = await _request(env, "GET", f"/channels/{channel.id}/messages/{message.id}")
        assert payload["reactions"][0]["me"] is True
        assert payload["id"] == str(message.id)
        assert payload["timestamp"] == message.timestamp
        assert payload["author"]["id"] == str(alice.id)
        if visible:
            assert payload["content"] == "private"
            assert payload["poll"]["results"]["answer_counts"][0]["me_voted"] is True
        else:
            _hidden(payload)
        assert message.content == "private"
        assert message.embeds and message.attachments and message.components and message.poll
        assert (
            serializers.message_payload(env.backend, message)["poll"]["question"]["text"]
            == "Private question"
        )


async def test_history_windows_and_pin_envelopes_project_every_message():
    async with simcord.run(_bot(), approved_intents=_approval(False)) as env:
        channel, alice = _world(env)
        messages = [_rich(env, channel, alice.id, f"private {i}") for i in range(3)]
        for message in messages:
            env.backend.set_pinned(channel.id, message.id, True)
        for params in (
            {},
            {"around": str(messages[1].id)},
            {"before": str(messages[2].id)},
            {"after": str(messages[0].id)},
        ):
            payloads = await _request(env, "GET", f"/channels/{channel.id}/messages", params=params)
            assert payloads
            for payload in payloads:
                if payload["id"] in {str(message.id) for message in messages}:
                    _hidden(payload)
                    assert payload["pinned"] is True
                else:
                    assert payload["author"]["id"] == str(env.backend.bot_user.id)
                    _hidden(payload["referenced_message"])
                assert payload["guild_id"] == str(env.guild.id)
        pins = await _request(env, "GET", f"/channels/{channel.id}/messages/pins")
        assert pins["has_more"] is False
        assert len(pins["items"]) == 3
        for item in pins["items"]:
            assert item["pinned_at"]
            _hidden(item["message"])


async def test_ordinary_send_and_edits_project_references_without_mutating_world():
    async with simcord.run(_bot(), approved_intents=_approval(False)) as env:
        channel, alice = _world(env)
        target = _rich(env, channel, alice.id)
        path = f"/channels/{channel.id}/messages"
        sent = await _request(
            env,
            "POST",
            path,
            json={
                "content": "bot reply",
                "message_reference": {"channel_id": str(channel.id), "message_id": str(target.id)},
            },
        )
        assert sent["content"] == "bot reply"
        _hidden(sent["referenced_message"])
        edited = await _request(env, "PATCH", f"{path}/{sent['id']}", json={"content": "edited"})
        assert edited["content"] == "edited"
        _hidden(edited["referenced_message"])
        assert serializers.message_payload(env.backend, target)["content"] == "private"
        await alice.edit(next(message for message in channel.history() if message.id == target.id), "changed")
        _hidden(await _request(env, "GET", f"{path}/{target.id}"))


async def test_token_owned_webhook_roots_do_not_authorize_nested_or_unowned_messages():
    async with simcord.run(_bot(), approved_intents=_approval(False)) as env:
        channel, alice = _world(env)
        target = _rich(env, channel, alice.id)
        hook = env.backend.create_webhook(channel.id, "hook", env.backend.bot_user.id)
        adapter = FakeWebhookAdapter(env.backend, message_content_access=False)
        hook_path = f"/webhooks/{hook.id}/{hook.token}"

        async def token_request(method, path, **kwargs):
            return await adapter.request(Route(method, path), None, **kwargs)

        sent = await token_request(
            "POST",
            hook_path,
            payload={
                "content": "owned",
                "message_reference": {"channel_id": str(channel.id), "message_id": str(target.id)},
            },
        )
        assert sent["content"] == "owned"
        _hidden(sent["referenced_message"])
        owned_path = f"{hook_path}/messages/{sent['id']}"
        fetched = await token_request("GET", owned_path)
        assert fetched["content"] == "owned"
        _hidden(fetched["referenced_message"])
        edited = await token_request("PATCH", owned_path, payload={"content": "owned edit"})
        assert edited["content"] == "owned edit"
        _hidden(edited["referenced_message"])
        _hidden(await _request(env, "GET", f"/channels/{channel.id}/messages/{sent['id']}"))
        with pytest.raises(discord.NotFound):
            await token_request("GET", f"{hook_path}/messages/{target.id}")
        with pytest.raises(discord.NotFound):
            await token_request("GET", f"/webhooks/{hook.id}/wrong/messages/{sent['id']}")
        assert env.backend.get_message(channel.id, int(sent["id"])).author_id == hook.webhook_user_id


async def test_http_content_edit_reparses_mentions_but_field_only_edit_retains_reply_ping():
    async with simcord.run(_bot(), approved_intents=_approval(False)) as env:
        channel, alice = _world(env)
        target = env.backend.create_message(channel.id, alice.id, "target")
        path = f"/channels/{channel.id}/messages"
        sent = await _request(
            env,
            "POST",
            path,
            json={
                "content": alice.mention,
                "message_reference": {"channel_id": str(channel.id), "message_id": str(target.id)},
                "allowed_mentions": {"parse": [], "replied_user": False},
            },
        )
        stored = env.backend.get_message(channel.id, int(sent["id"]))
        assert stored.ping_user_ids == []
        await _request(env, "PATCH", f"{path}/{stored.id}", json={"embeds": [{"description": "edit"}]})
        assert stored.allowed_mentions.replied_user is False
        assert stored.ping_user_ids == []
        await _request(env, "PATCH", f"{path}/{stored.id}", json={"content": alice.mention})
        assert stored.allowed_mentions.users is None
        assert alice.id in stored.ping_user_ids


async def test_restarts_recompute_access_and_other_environments_keep_their_own_access():
    async with simcord.run(_bot(), approved_intents=_approval(False)) as env:
        channel, alice = _world(env)
        message = _rich(env, channel, alice.id)
        path = f"/channels/{channel.id}/messages/{message.id}"
        _hidden(await _request(env, "GET", path))
        await env.restart_bot(_bot())
        _hidden(await _request(env, "GET", path))
        env.approved_intents = _approval(True)
        await env.restart_bot(_bot())
        assert (await _request(env, "GET", path))["content"] == "private"
    async with simcord.run(_bot(), approved_intents=_approval(False)) as other:
        other_channel, other_alice = _world(other)
        other_message = _rich(other, other_channel, other_alice.id)
        _hidden(await _request(other, "GET", f"/channels/{other_channel.id}/messages/{other_message.id}"))

    env.bot = _bot()
    await env.start()
    try:
        assert (await _request(env, "GET", path))["content"] == "private"
    finally:
        await env.shutdown()


async def test_selected_context_target_and_interaction_token_responses_keep_contextual_access():
    bot = _bot()
    observed = {}

    @bot.tree.context_menu(name="Inspect")
    async def inspect(interaction: discord.Interaction, message: discord.Message) -> None:
        observed["selected"] = message
        callback = await interaction.client.http.request(
            Route("POST", f"/interactions/{interaction.id}/{interaction.token}/callback"),
            json={
                "type": 4,
                "data": {
                    "content": "context response",
                    "message_reference": {
                        "channel_id": str(message.channel.id),
                        "message_id": str(message.id),
                    },
                },
            },
        )
        observed["callback"] = callback["resource"]["message"]
        # Exercise the installed webhook adapter, not just the HTTP client.
        original = await interaction.original_response()
        observed["original"] = original
        followup = await interaction.followup.send("followup", wait=True)
        observed["followup"] = await interaction.followup.fetch_message(followup.id)
        observed["interaction"] = interaction

    async with simcord.run(bot, approved_intents=_approval(False), strict_sync=False) as env:
        channel, alice = _world(env)
        target = _rich(env, channel, alice.id)
        actor_message = next(message for message in channel.history() if message.id == target.id)
        await alice.context_menu(channel, "Inspect", actor_message)
        selected = observed["selected"]
        assert selected.content == "private"
        assert selected.embeds and selected.attachments and selected.components and selected.poll
        assert observed["callback"]["content"] == "context response"
        _hidden(observed["callback"]["referenced_message"])
        assert observed["original"].content == "context response"
        assert observed["original"].reference.resolved.content == ""
        assert observed["followup"].content == "followup"
        interaction = observed["interaction"]
        with pytest.raises(discord.NotFound):
            await _request(
                env,
                "GET",
                f"/webhooks/{env.backend.application_id}/{interaction.token}/messages/{target.id}",
            )
        _hidden(await _request(env, "GET", f"/channels/{channel.id}/messages/{target.id}"))


async def test_poll_expiry_and_forum_starter_project_nested_sources():
    async with simcord.run(_bot(), approved_intents=_approval(False)) as env:
        channel, alice = _world(env)
        target = _rich(env, channel, alice.id)
        reference = {"channel_id": str(channel.id), "message_id": str(target.id)}
        poll_message = _rich(env, channel, env.backend.bot_user.id, "bot poll")
        poll_message.reference = reference
        expired = await _request(env, "POST", f"/channels/{channel.id}/polls/{poll_message.id}/expire")
        assert expired["content"] == "bot poll"
        assert expired["poll"]["results"]["is_finalized"] is True
        _hidden(expired["referenced_message"])
        forum = env.guild.create_forum_channel("posts")
        with pytest.raises(discord.HTTPException) as error:
            await _request(
                env,
                "POST",
                f"/channels/{forum.id}/threads",
                json={"name": "Invalid", "message": {"content": "starter", "message_reference": reference}},
            )
        assert error.value.code == 50035
        post = await _request(
            env,
            "POST",
            f"/channels/{forum.id}/threads",
            json={"name": "Post", "message": {"content": "starter"}},
        )
        assert post["message"]["content"] == "starter"
