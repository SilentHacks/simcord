import asyncio

import discord
import pytest
from preview_helpers import action_body

import simcord
from simcord.backend.models import Poll, PollAnswer


def _poll(env, *, multiselect=False):
    return Poll(
        question="Lunch?",
        answers=[PollAnswer(1, "Pizza"), PollAnswer(2, "Sushi")],
        expiry=env.backend.iso_after(3600),
        allow_multiselect=multiselect,
    )


async def _action(preview, page, kind, sequence, target_id, **fields):
    body = action_body(
        page,
        kind,
        sequence,
        target_id=str(target_id),
        published_revision=page.revision,
        **fields,
    )
    return await preview._action(page.id, body)


@pytest.mark.asyncio
async def test_preview_send_timeout_records_mutation_and_replay_is_safe(env, channel, alice):
    started, release = asyncio.Event(), asyncio.Event()
    env.settle_timeout = 0.05

    @env.bot.listen("on_message")
    async def hold_send(message):
        if message.content == "held send":
            started.set()
            await release.wait()

    async with env.preview(channel, layout="channel", viewers=[alice]) as preview:
        page = preview._python
        body = action_body(page, "send_message", 1, content="held send")
        receipt = await preview._action(page.id, body)
        assert started.is_set()
        assert receipt["settlement"] == "timeout"
        assert receipt["dispatch"] == "dispatched"
        assert receipt["uncertain"] is True
        assert page.status == "stale"

        assert [message.content for message in env.backend.messages[channel.id].values()].count(
            "held send"
        ) == 1
        assert await preview._action(page.id, body) == receipt
        assert [message.content for message in env.backend.messages[channel.id].values()].count(
            "held send"
        ) == 1
        release.set()
        await env.settle()


@pytest.mark.asyncio
async def test_preview_reaction_actions_are_desired_idempotent_and_permission_guarded(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    message = await alice.send(channel, "React here")
    await bob.react(message, "👍")
    adds_before = env.transcript().count("MESSAGE_REACTION_ADD")

    async with env.preview(channel, layout="channel", viewers=[alice, bob]) as preview:
        page = preview._python
        other_page = preview._open_page(bob.id, target_id=message.id)
        old_revision = page.revision
        body = action_body(
            page,
            "set_reaction",
            1,
            request_id="reaction-add",
            target_id=str(message.id),
            published_revision=old_revision,
            emoji="👍",
            reacted=True,
        )
        added = await preview._action(page.id, body)
        assert added["settlement"] == "settled"
        assert added["acknowledgement"] == "not_applicable"
        assert await preview._action(page.id, body) == added
        stored = env.backend.get_message(channel.id, message.id)
        assert stored.reaction_for("👍").user_ids.count(alice.id) == 1
        assert env.transcript().count("MESSAGE_REACTION_ADD") == adds_before + 1

        stale = await preview._action(
            page.id,
            action_body(
                page,
                "set_reaction",
                2,
                request_id="stale-reaction-remove",
                target_id=str(message.id),
                published_revision=old_revision,
                emoji="👍",
                reacted=False,
            ),
        )
        assert stale["rejected"] is True
        assert stale["diagnostics"][0]["code"] == "stale-revision"
        assert alice.id in stored.reaction_for("👍").user_ids

        removes_before = env.transcript().count("MESSAGE_REACTION_REMOVE")
        removed = await _action(preview, page, "set_reaction", 2, message.id, emoji="👍", reacted=False)
        assert removed["settlement"] == "settled"
        assert env.transcript().count("MESSAGE_REACTION_REMOVE") == removes_before + 1
        assert alice.id not in env.backend.get_message(channel.id, message.id).reaction_for("👍").user_ids
        projected = preview._page_payload(other_page)["messages"][str(message.id)]["reactions"][0]
        assert projected["count"] == 1
        assert projected["viewer_reacted"] is True
        assert "user_ids" not in projected

    locked = env.guild.create_text_channel(
        "reaction-locked",
        overwrites={
            env.guild.default_role: discord.PermissionOverwrite(add_reactions=False),
            bob: discord.PermissionOverwrite(add_reactions=True),
        },
    )
    denied_message = await bob.send(locked, "No reaction permission for Alice")
    await bob.react(denied_message, "🔥")
    async with env.preview(locked, layout="channel", viewers=[alice]) as preview:
        page = preview._python
        projection = preview._page_payload(page)["messages"][str(denied_message.id)]
        assert "set_reaction" not in projection["allowed_actions"]
        assert projection["reactions"][0]["can_toggle"] is False
        denied = await _action(preview, page, "set_reaction", 1, denied_message.id, emoji="🔥", reacted=True)
        assert denied["settlement"] == "failed"
        assert denied["dispatch"] == "not_dispatched"
        assert denied["uncertain"] is False
        assert denied["acknowledgement"] == "not_applicable"
        assert (
            alice.id not in env.backend.get_message(locked.id, denied_message.id).reaction_for("🔥").user_ids
        )


@pytest.mark.asyncio
async def test_preview_poll_desired_sets_are_atomic_and_expire(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    single = env.backend.create_message(channel.id, env.backend.bot_user.id, "Single choice", poll=_poll(env))
    multi = env.backend.create_message(
        channel.id, env.backend.bot_user.id, "Multiple choices", poll=_poll(env, multiselect=True)
    )
    await env.settle()

    async with env.preview(channel, layout="channel", viewers=[alice, bob]) as preview:
        page = preview._python
        other_page = preview._open_page(bob.id, target_id=single.id)
        initial = preview._page_payload(page)["messages"][str(single.id)]["poll"]
        assert "votes" not in initial
        assert all("user_ids" not in answer for answer in initial["answers"])
        assert "set_poll_votes" in preview._page_payload(page)["messages"][str(single.id)]["allowed_actions"]

        vote_adds_before = env.transcript().count("MESSAGE_POLL_VOTE_ADD")
        first = await _action(preview, page, "set_poll_votes", 1, single.id, answer_ids=["1"])
        assert first["settlement"] == "settled"
        assert env.transcript().count("MESSAGE_POLL_VOTE_ADD") == vote_adds_before + 1
        assert first["acknowledgement"] == "not_applicable"
        single_poll = preview._page_payload(other_page)["messages"][str(single.id)]["poll"]
        assert single_poll["answers"][0]["count"] == 1
        assert single_poll["answers"][0]["percentage"] == 100
        assert single_poll["answers"][1]["percentage"] == 0

        await _action(preview, page, "set_poll_votes", 2, single.id, answer_ids=["2"])
        stored_single = env.backend.get_message(channel.id, single.id).poll
        assert alice.id not in stored_single.votes.get(1, set())
        assert alice.id in stored_single.votes[2]
        assert "MESSAGE_POLL_VOTE_REMOVE" in env.transcript()
        await _action(preview, page, "set_poll_votes", 3, single.id, answer_ids=[])
        assert alice.id not in env.backend.get_message(channel.id, single.id).poll.votes.get(2, set())

        await _action(preview, page, "set_poll_votes", 4, multi.id, answer_ids=["1", "2"])
        multi_poll = preview._page_payload(page)["messages"][str(multi.id)]["poll"]
        assert [answer["percentage"] for answer in multi_poll["answers"]] == [50, 50]
        duplicate_answers = await _action(preview, page, "set_poll_votes", 5, multi.id, answer_ids=["1", "1"])
        assert duplicate_answers["rejected"] is True
        assert page.last_sequence == 4

        invalid = await _action(preview, page, "set_poll_votes", 5, multi.id, answer_ids=["1", "999"])
        assert invalid["settlement"] == "failed"
        stored_multi = env.backend.get_message(channel.id, multi.id).poll
        assert alice.id in stored_multi.votes[1]
        assert alice.id in stored_multi.votes[2]
        await preview.refresh()

        await _action(preview, page, "set_poll_votes", 6, multi.id, answer_ids=[])
        assert alice.id not in env.backend.get_message(channel.id, multi.id).poll.votes.get(1, set())
        assert alice.id not in env.backend.get_message(channel.id, multi.id).poll.votes.get(2, set())

        await env.advance_time(3700)
        await preview.refresh()
        expired = preview._page_payload(page)["messages"][str(single.id)]
        assert expired["poll"]["finalized"] is True
        assert expired["poll"]["expired"] is True
        assert "set_poll_votes" not in expired["allowed_actions"]
        rejected_vote = await _action(preview, page, "set_poll_votes", 7, single.id, answer_ids=["1"])
        assert rejected_vote["settlement"] == "failed"
        assert alice.id not in env.backend.get_message(channel.id, single.id).poll.votes.get(1, set())


@pytest.mark.asyncio
async def test_preview_message_layout_allows_poll_votes(env, channel, alice):
    message = env.backend.create_message(channel.id, env.backend.bot_user.id, "Vote here", poll=_poll(env))
    await env.settle()

    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python
        projection = preview._page_payload(page)["messages"][str(message.id)]
        assert "set_poll_votes" in projection["allowed_actions"]
        result = await _action(preview, page, "set_poll_votes", 1, message.id, answer_ids=["1"])
        assert result["settlement"] == "settled"
        assert alice.id in env.backend.get_message(channel.id, message.id).poll.votes[1]


@pytest.mark.asyncio
async def test_preview_edit_delete_and_pin_publish_to_bot_and_other_pages(env, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    channel = env.guild.create_text_channel(
        "preview-actions",
        overwrites={
            env.guild.default_role: discord.PermissionOverwrite(manage_messages=False),
            alice: discord.PermissionOverwrite(manage_messages=True),
        },
    )
    message = await alice.send(channel, "Before")
    bot_channel = env.bot.get_channel(channel.id)

    async with env.preview(channel, layout="channel", viewers=[alice, bob]) as preview:
        page = preview._python
        other_page = preview._open_page(bob.id, target_id=message.id)
        projection = preview._page_payload(page)["messages"][str(message.id)]
        assert {"edit_message", "delete_message", "set_pinned"} <= set(projection["allowed_actions"])

        edited = await _action(preview, page, "edit_message", 1, message.id, content="After")
        assert edited["settlement"] == "settled"
        assert (await bot_channel.fetch_message(message.id)).content == "After"
        assert preview._page_payload(other_page)["messages"][str(message.id)]["content"] == "After"

        pinned = await _action(preview, page, "set_pinned", 2, message.id, pinned=True)
        assert pinned["settlement"] == "settled"
        assert env.backend.get_message(channel.id, message.id).pinned is True
        pins = [item async for item in bot_channel.pins()]
        assert any(item.id == message.id for item in pins)
        assert preview._page_payload(other_page)["messages"][str(message.id)]["pinned"] is True

        deleted = await _action(preview, page, "delete_message", 3, message.id, confirmed=True)
        assert deleted["settlement"] == "settled"
        with pytest.raises(discord.NotFound):
            await bot_channel.fetch_message(message.id)
        assert str(message.id) not in preview._page_payload(other_page)["messages"]


@pytest.mark.asyncio
async def test_user_handle_message_actions_work_in_direct_messages(env, alice):
    own = await alice.send_dm("Before")
    dm = alice.user.dm_channel
    await alice.user.edit(own, "After")
    assert env.backend.get_message(dm.id, own.id).content == "After"
    await alice.user.delete(own)
    with pytest.raises(simcord.BackendError):
        env.backend.get_message(dm.id, own.id)

    prompt = env.backend.create_message(
        dm.id,
        env.backend.bot_user.id,
        "Vote here",
        poll=_poll(env),
    )
    await env.settle()
    prompt_message = dm.last_message
    assert prompt_message is not None and prompt_message.id == prompt.id
    await alice.user.set_reaction(prompt_message, "👍", reacted=True)
    await alice.user.set_reaction(prompt_message, "👍", reacted=False)
    await alice.user.set_poll_votes(prompt_message, answers=[1])
    stored = env.backend.get_message(dm.id, prompt.id)
    assert stored.reaction_for("👍") is None
    assert alice.id in stored.poll.votes[1]

    async with env.preview(dm, layout="channel", viewers=[alice.user]) as preview:
        page = preview._python
        projection = preview._page_payload(page)["messages"][str(prompt.id)]
        assert "set_poll_votes" in projection["allowed_actions"]
        voted = await _action(preview, page, "set_poll_votes", 1, prompt.id, answer_ids=["1"])
        assert voted["settlement"] == "settled"
        assert voted["acknowledgement"] == "not_applicable"


@pytest.mark.asyncio
async def test_poll_choices_have_accessible_text_and_submit_the_named_answer(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    message = env.backend.create_message(channel.id, env.backend.bot_user.id, "Choose lunch", poll=_poll(env))
    await env.settle()
    async with env.preview(channel, viewers=[alice]) as preview:
        async with async_playwright() as api:
            browser = await api.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                poll = page.locator(".message-poll")
                assert await poll.get_by_role("button", name="Pizza", exact=True).count() == 1
                await poll.get_by_role("button", name="Sushi", exact=True).click()
                await poll.get_by_role("button", name="Vote", exact=True).click()
                await page.wait_for_function("() => window.simcordPreview?.ready === true")
                assert env.backend.get_message(channel.id, message.id).poll.votes == {2: {alice.id}}
            finally:
                await browser.close()
