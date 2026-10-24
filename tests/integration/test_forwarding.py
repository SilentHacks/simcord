"""Native actor and SDK forwards share authoritative, immutable snapshots."""

from copy import deepcopy
from datetime import timedelta

import discord
import pytest
from discord.ext import commands
from discord.http import Route

import simcord
from fixtures.sample_bot import create_bot
from simcord.enums import MessageType


async def _post(env, channel, payload):
    return await env.bot.http.request(
        Route("POST", "/channels/{channel_id}/messages", channel_id=channel.id), json=payload
    )


async def test_actor_and_sdk_forward_preserve_snapshot_after_source_changes(env, channel, alice):
    destination = env.guild.create_text_channel("forwarded")
    seen = []

    @env.bot.listen("on_message")
    async def capture(message):
        seen.append(message)

    source = await alice.send(channel, "original", attachments=[("proof.txt", b"evidence")])
    forwarded = await alice.forward(source, destination)
    assert forwarded.type is discord.MessageType.default
    assert forwarded.reference.type is discord.MessageReferenceType.forward
    assert forwarded.reference.channel_id == channel.id
    assert forwarded.reference.message_id == source.id
    assert forwarded.reference.guild_id == env.guild.id
    assert forwarded.reference.resolved is None
    assert forwarded.flags.forwarded
    assert forwarded.content == "" and forwarded.attachments == []
    assert len(forwarded.message_snapshots) == 1
    snapshot = forwarded.message_snapshots[0]
    assert snapshot.content == "original"
    assert snapshot.attachments[0].filename == "proof.txt"
    assert await snapshot.attachments[0].read() == b"evidence"
    assert seen[-1].id == forwarded.id
    assert seen[-1].message_snapshots[0].content == "original"
    stored = env.backend.get_message(destination.id, forwarded.id)
    assert stored.ping_user_ids == [] and stored.mention_user_ids == []
    assert "author" not in stored.snapshots[0]["message"]

    # SDK forwarding must retain reference.type instead of becoming a reply.
    native_destination = env.bot.get_channel(destination.id)
    sdk_forward = await source.forward(native_destination)
    assert sdk_forward.reference.type is discord.MessageReferenceType.forward
    assert sdk_forward.message_snapshots[0].content == "original"
    await alice.edit(source, "changed")
    await alice.delete(source)
    snapshot.attachments[0].filename = "consumer-mutation.txt"
    for message in (forwarded, sdk_forward):
        fetched = await native_destination.fetch_message(message.id)
        assert fetched.message_snapshots[0].content == "original"
        assert fetched.message_snapshots[0].attachments[0].filename == "proof.txt"
        assert await fetched.message_snapshots[0].attachments[0].read() == b"evidence"
    assert destination.history()[0].message_snapshots[0].content == "original"


async def test_forward_snapshots_embeds_components_and_mentions_without_reply_ping(env, channel, alice):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Open", url="https://example.com"))
    source = await env.bot.get_channel(channel.id).send(
        f"hello {alice.mention}", embed=discord.Embed(title="Evidence"), view=view
    )
    destination = env.guild.create_text_channel("forwarded")
    forwarded = await alice.forward(source, destination)
    snapshot = forwarded.message_snapshots[0]
    assert snapshot.embeds[0].title == "Evidence"
    assert snapshot.components[0].children[0].label == "Open"
    assert snapshot.raw_mentions == [alice.id]
    assert forwarded.mentions == []
    assert env.backend.get_message(destination.id, forwarded.id).ping_user_ids == []
    # A reply still has a live resolved message, a reply type, and reply pings.
    reply = await alice.send(channel, "reply", reply_to=source)
    assert reply.type is discord.MessageType.reply
    assert reply.reference.type is discord.MessageReferenceType.default
    assert reply.reference.resolved.id == source.id
    assert reply.message_snapshots == []
    assert env.bot.user.id in env.backend.get_message(channel.id, reply.id).ping_user_ids
    await source.edit(content="changed", embed=discord.Embed(title="Changed"), view=None)
    refreshed = await env.bot.get_channel(destination.id).fetch_message(forwarded.id)
    assert refreshed.message_snapshots[0].embeds[0].title == "Evidence"
    assert refreshed.message_snapshots[0].components[0].children[0].label == "Open"
    assert refreshed.message_snapshots[0].content == f"hello {alice.mention}"


@pytest.mark.parametrize("permission", ["view_channel", "read_message_history"])
async def test_forward_requires_source_visibility_and_history(env, channel, alice, permission):
    hidden = env.guild.create_text_channel(
        "restricted", overwrites={alice: discord.PermissionOverwrite(**{permission: False})}
    )
    source = await env.bot.get_channel(hidden.id).send("source")
    before = tuple(env.backend.messages[channel.id])
    with pytest.raises(simcord.BackendError) as error:
        await alice.forward(source, channel)
    assert error.value.code == (50001 if permission == "view_channel" else 50013)
    assert tuple(env.backend.messages[channel.id]) == before


@pytest.mark.parametrize(
    "permission,attachment", [("view_channel", False), ("send_messages", False), ("attach_files", True)]
)
async def test_forward_requires_destination_permissions(env, channel, alice, permission, attachment):
    source = await alice.send(channel, "source", attachments=[("proof.txt", b"proof")] if attachment else [])
    destination = env.guild.create_text_channel(
        "restricted", overwrites={alice: discord.PermissionOverwrite(**{permission: False})}
    )
    with pytest.raises(simcord.BackendError) as error:
        await alice.forward(source, destination)
    assert error.value.code == (50001 if permission == "view_channel" else 50013)
    assert destination.history() == []


@pytest.mark.parametrize(
    "reference",
    [
        [],
        {"type": 1},
        {"type": 1, "message_id": "123"},
        {"type": 2, "message_id": "123"},
        {"type": True, "message_id": "123"},
        {"type": "1", "message_id": "123"},
        {"message_id": True},
        {"message_id": 1.5},
        {"message_id": "0"},
        {"message_id": "-1"},
        {"message_id": "18446744073709551616"},
        {"message_id": "\u0661"},
        {"message_id": "123", "extra": "ignored?"},
        {"message_id": "123", "fail_if_not_exists": 1},
    ],
)
async def test_malformed_reference_rejected_without_mutation(env, channel, reference):
    before = tuple(env.backend.messages[channel.id])
    with pytest.raises(discord.HTTPException) as error:
        await _post(env, channel, {"content": "not stored", "message_reference": reference})
    assert error.value.code == 50035
    assert tuple(env.backend.messages[channel.id]) == before


async def test_deleted_unknown_and_unsupported_forward_sources_fail_loudly(env, channel, alice):
    source = await alice.send(channel, "gone")
    await alice.delete(source)
    with pytest.raises(simcord.BackendError) as error:
        await alice.forward(source, channel)
    assert error.value.code == 10008
    system = env.guild.create_system_message(channel, MessageType.NEW_MEMBER, author=alice)
    with pytest.raises(simcord.BackendError) as error:
        await alice.forward(system, channel)
    assert error.value.code == 50035
    poll = discord.Poll(question="Choose", duration=timedelta(hours=1))
    poll.add_answer(text="One")
    poll.add_answer(text="Two")
    polling = await env.bot.get_channel(channel.id).send(poll=poll)
    with pytest.raises(simcord.BackendError) as error:
        await alice.forward(polling, channel)
    assert error.value.code == 50035
    with pytest.raises(discord.NotFound) as error:
        await source.forward(env.bot.get_channel(channel.id), fail_if_not_exists=False)
    assert error.value.code == 10008


async def test_actor_forward_uses_authoritative_source_and_gateway_policy():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    seen = []

    @bot.listen("on_message")
    async def capture(message):
        seen.append(message)

    async with simcord.run(bot) as env:
        guild = env.create_guild()
        source_channel = guild.create_text_channel("source")
        destination = guild.create_text_channel("destination")
        alice = guild.add_member(env.create_user("alice"))
        await alice.send(source_channel, "secret", attachments=[("proof.txt", b"proof")])
        assert seen[-1].content == ""
        # Even a censored SDK object identifies the authoritative actor source.
        forwarded = await alice.forward(seen[-1], destination)
        assert forwarded.message_snapshots[0].content == "secret"
        assert seen[-1].id == forwarded.id
        assert seen[-1].message_snapshots[0].content == ""
        assert seen[-1].message_snapshots[0].created_at == forwarded.message_snapshots[0].created_at
        assert seen[-1].message_snapshots[0].attachments == []
        assert forwarded.message_snapshots[0].attachments[0].filename == "proof.txt"
        assert destination.history(viewer=alice)[0].message_snapshots[0].content == "secret"


async def test_sdk_forward_content_authorization_is_independent_of_gateway_intents():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    async with simcord.run(bot, approved_intents=discord.Intents.default()) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("source")
        alice = guild.add_member(env.create_user("alice"))
        source = await alice.send(channel, "secret")
        native_channel = bot.get_channel(channel.id)
        with pytest.raises(discord.HTTPException) as error:
            await source.forward(native_channel)
        assert error.value.code == 160014
        authoritative = await alice.forward(source, channel)
        observed = await native_channel.fetch_message(authoritative.id)
        assert observed.message_snapshots[0].content == ""
        assert authoritative.message_snapshots[0].content == "secret"
        observed_history = {message.id: message async for message in native_channel.history()}
        assert observed_history[authoritative.id].message_snapshots[0].content == ""
        own = await native_channel.send("bot authored")
        assert (await own.forward(native_channel)).message_snapshots[0].content == "bot authored"
        mentioned = await alice.send(channel, f"hello {bot.user.mention}")
        assert (await mentioned.forward(native_channel)).message_snapshots[0].content == mentioned.content
        reply = await alice.send(channel, "reply exemption", reply_to=own)
        await own.delete()
        await alice.edit(reply, "edited reply exemption")
        assert (await native_channel.fetch_message(reply.id)).content == "edited reply exemption"

    approved = discord.Intents.default()
    approved.message_content = True
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    async with simcord.run(bot, approved_intents=approved) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("source")
        alice = guild.add_member(env.create_user("alice"))
        source = await alice.send(channel, "API-visible, gateway-censored")
        forwarded = await source.forward(bot.get_channel(channel.id))
        assert forwarded.message_snapshots[0].content == source.content


async def test_native_forward_with_annotation_preserves_outer_fields_and_rejects_unmodelled_nesting(
    env, channel, alice
):
    source = await alice.send(channel, "original")
    destination = env.guild.create_text_channel("forwarded")
    native_destination = env.bot.get_channel(destination.id)
    forwarded = await native_destination.send(
        "annotation",
        reference=discord.MessageReference(
            message_id=source.id, channel_id=channel.id, type=discord.MessageReferenceType.forward
        ),
    )
    assert forwarded.content == "annotation"
    assert forwarded.message_snapshots[0].content == "original"
    before = tuple(env.backend.messages[destination.id])
    with pytest.raises(NotImplementedError, match="already-forwarded"):
        await alice.forward(forwarded, destination)
    with pytest.raises(NotImplementedError, match="already-forwarded"):
        await forwarded.forward(native_destination)
    assert tuple(env.backend.messages[destination.id]) == before


async def test_reply_cannot_reference_another_channel_or_inject_snapshots(env, channel, alice):
    source = await alice.send(channel, "original")
    destination = env.guild.create_text_channel("destination")
    with pytest.raises(discord.HTTPException) as error:
        await _post(
            env,
            destination,
            {
                "content": "reply",
                "message_reference": {"type": 0, "channel_id": str(channel.id), "message_id": str(source.id)},
            },
        )
    assert error.value.code == 50035
    with pytest.raises(simcord.BackendError) as actor_error:
        await alice.send(destination, "reply", reply_to=source)
    assert actor_error.value.code == 50035
    with pytest.raises(simcord.UnsupportedField):
        await _post(env, destination, {"message_snapshots": [{"message": {"content": "spoofed"}}]})
    assert destination.history() == []


@pytest.mark.parametrize("permission", ["view_channel", "read_message_history"])
async def test_sdk_forward_uses_bot_source_permissions(env, channel, alice, permission):
    source_channel = env.guild.create_text_channel(
        "restricted",
        overwrites={
            env.guild.default_role: discord.PermissionOverwrite(**{permission: False}),
            alice: discord.PermissionOverwrite(**{permission: True}),
        },
    )
    source = await alice.send(source_channel, "actor can read, bot cannot")
    with pytest.raises(discord.Forbidden) as error:
        await source.forward(env.bot.get_channel(channel.id))
    assert error.value.code == (50001 if permission == "view_channel" else 50013)
    assert channel.history() == []


async def test_sdk_forward_requires_destination_attachment_permission(env, channel, alice):
    source = await alice.send(channel, "proof", attachments=[("proof.txt", b"proof")])
    destination = env.guild.create_text_channel(
        "no-files", overwrites={env.guild.default_role: discord.PermissionOverwrite(attach_files=False)}
    )
    with pytest.raises(discord.Forbidden) as error:
        await source.forward(env.bot.get_channel(destination.id))
    assert error.value.code == 50013
    assert destination.history() == []


@pytest.mark.parametrize("handle_type", ["sdk", "response"])
async def test_actor_forward_across_guilds_uses_memberships_and_source_metadata(
    env, channel, alice, handle_type
):
    source = await alice.send(channel, "cross-guild")
    if handle_type == "response":
        source = simcord.ResponseMessage(env, env.backend.get_message(channel.id, source.id))
    other = env.create_guild("other")
    other.add_member(alice.user)
    destination = other.create_text_channel("destination")
    forwarded = await alice.forward(source, destination)
    assert forwarded.guild.id == other.id
    assert forwarded.reference.guild_id == channel.guild.id
    assert forwarded.message_snapshots[0].content == "cross-guild"
    outsider = other.add_member(env.create_user("outsider"))
    with pytest.raises(simcord.BackendError):
        await outsider.forward(source, destination)


async def test_private_thread_forwarding_honors_moderator_access_without_membership(env, channel, alice):
    parent = env.guild.create_text_channel(
        "private-parent", overwrites={alice: discord.PermissionOverwrite(manage_threads=True)}
    )
    thread = await env.bot.get_channel(parent.id).create_thread(
        name="private", type=discord.ChannelType.private_thread
    )
    private = simcord.ChannelHandle(env, env.guild, env.backend.get_channel(thread.id))
    source = await thread.send("private evidence")
    assert alice.id not in env.backend.get_channel(thread.id).thread_members
    await alice.react(source, "✅")
    forwarded = await alice.forward(source, channel)
    assert forwarded.message_snapshots[0].content == "private evidence"
    public = await alice.send(channel, "public evidence")
    forwarded_private = await alice.forward(public, private)
    assert forwarded_private.message_snapshots[0].content == "public evidence"
    outsider = env.guild.add_member(env.create_user("outsider"))
    with pytest.raises(simcord.BackendError):
        await outsider.forward(source, channel)


async def test_forwarding_cannot_read_or_write_another_members_dm(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    private_source = await alice.send_dm("private evidence")
    public_source = await alice.send(channel, "public evidence")
    private = alice.user.dm_channel
    outward = await alice.forward(private_source, channel)
    inward = await alice.forward(public_source, private)
    assert outward.message_snapshots[0].content == "private evidence"
    assert inward.message_snapshots[0].content == "public evidence"
    before = {target.id: deepcopy(env.backend.messages[target.id]) for target in (channel, private)}
    seen = []

    @env.bot.listen("on_message")
    async def capture(message):
        seen.append(message.id)

    with pytest.raises(simcord.BackendError) as error:
        await bob.forward(private_source, channel)
    assert error.value.code == 50001
    with pytest.raises(simcord.BackendError) as error:
        await bob.forward(public_source, private)
    assert error.value.code == 50001
    await env.settle()
    assert seen == []
    for channel_id, messages in before.items():
        assert env.backend.messages[channel_id] == messages


async def test_sdk_forward_only_reads_registered_bot_dm_conversations(env, channel, alice):
    own_dm_source = await alice.send_dm("bot conversation")
    native_destination = env.bot.get_channel(channel.id)
    legitimate = await own_dm_source.forward(native_destination)
    assert legitimate.message_snapshots[0].content == "bot conversation"
    # A valid imported DM channel is not automatically a conversation with this bot.
    unregistered = env.backend.create_channel(
        None, None, type=discord.ChannelType.private.value, recipient_ids=[alice.id]
    )
    source = env.backend.create_message(unregistered.id, alice.id, "unrelated conversation", broadcast=False)
    before = deepcopy(env.backend.messages[channel.id])
    conversations = dict(env.backend.dm_channels)
    await env.settle()
    seen = []

    @env.bot.listen("on_message")
    async def capture(message):
        seen.append(message.id)

    with pytest.raises(discord.Forbidden) as error:
        await _post(
            env,
            channel,
            {
                "message_reference": {
                    "type": 1,
                    "channel_id": str(unregistered.id),
                    "message_id": str(source.id),
                }
            },
        )
    assert error.value.code == 50001
    await env.settle()
    assert env.backend.messages[channel.id] == before
    assert env.backend.dm_channels == conversations
    assert seen == []


async def test_private_thread_forwarding_requires_membership_in_both_directions(env, channel, alice):
    parent = env.guild.create_text_channel("private-parent")
    thread = await env.bot.get_channel(parent.id).create_thread(
        name="private", type=discord.ChannelType.private_thread
    )
    private = simcord.ChannelHandle(env, env.guild, env.backend.get_channel(thread.id))
    private_source = await thread.send("private evidence")
    public_source = await alice.send(channel, "public evidence")
    ordinary = discord.Permissions(env.backend.compute_permissions(env.guild.id, alice.id, thread.id))
    assert ordinary.view_channel and ordinary.read_message_history and ordinary.send_messages_in_threads
    assert not ordinary.manage_threads and not ordinary.administrator
    before = {target.id: deepcopy(env.backend.messages[target.id]) for target in (channel, private)}
    seen = []

    @env.bot.listen("on_message")
    async def capture(message):
        seen.append(message.id)

    with pytest.raises(simcord.BackendError) as error:
        await alice.forward(private_source, channel)
    assert error.value.code == 50001
    with pytest.raises(simcord.BackendError) as error:
        await alice.forward(public_source, private)
    assert error.value.code == 50001
    await env.settle()
    for channel_id, messages in before.items():
        assert env.backend.messages[channel_id] == messages
    assert seen == []

    await thread.add_user(discord.Object(id=alice.id))
    outward = await alice.forward(private_source, channel)
    inward = await alice.forward(public_source, private)
    assert outward.message_snapshots[0].content == "private evidence"
    assert inward.message_snapshots[0].content == "public evidence"
    assert seen == [outward.id, inward.id]


async def test_forward_reference_rejects_inconsistent_source_guild_without_orphan(env, channel, alice):
    source = await alice.send(channel, "source evidence")
    other = env.create_guild("destination guild")
    destination = other.create_text_channel("forwarded")
    native_destination = env.bot.get_channel(destination.id)
    legitimate = await native_destination.send(
        reference=discord.MessageReference(
            message_id=source.id,
            channel_id=channel.id,
            guild_id=env.guild.id,
            type=discord.MessageReferenceType.forward,
        )
    )
    assert legitimate.message_snapshots[0].content == "source evidence"
    assert legitimate.reference.guild_id == env.guild.id
    await env.settle()
    before = deepcopy(env.backend.messages[destination.id])
    seen = []

    @env.bot.listen("on_message")
    async def capture(message):
        seen.append(message.id)

    with pytest.raises(discord.HTTPException) as error:
        await native_destination.send(
            "must not be stored",
            reference=discord.MessageReference(
                message_id=source.id,
                channel_id=channel.id,
                guild_id=other.id,
                type=discord.MessageReferenceType.forward,
            ),
        )
    assert error.value.code == 50035
    await env.settle()
    assert env.backend.messages[destination.id] == before
    assert seen == []


@pytest.mark.parametrize("handle_type", ["sdk", "response"])
@pytest.mark.parametrize(
    "operation",
    [
        "forward",
        "reply",
        "edit",
        "delete",
        "react",
        "unreact",
        "set_reaction",
        "set_pinned",
        "set_poll_votes",
        "click",
        "select",
        "context_menu",
        "system_reference",
    ],
)
async def test_actor_message_operations_reject_foreign_colliding_handles(monkeypatch, handle_type, operation):
    async with simcord.run(create_bot()) as foreign:
        guild = foreign.create_guild()
        foreign_channel = guild.create_text_channel("general")
        foreign_alice = guild.add_member(foreign.create_user("alice"))
        source = await foreign_alice.send(foreign_channel, "foreign source")
        if handle_type == "response":
            source = simcord.ResponseMessage(
                foreign, foreign.backend.get_message(foreign_channel.id, source.id)
            )

    async with simcord.run(create_bot()) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        local = await alice.send(channel, "local source")
        assert source.id == local.id
        assert foreign_channel.id == local.channel.id

        def unexpected_lookup(*args, **kwargs):
            raise AssertionError("Foreign message reached the local message lookup")

        before = [message.id for message in channel.history()]
        with monkeypatch.context() as patch:
            patch.setattr(env.backend, "get_message", unexpected_lookup)
            with pytest.raises(simcord.SetupError, match="must belong to this environment"):
                if operation == "system_reference":
                    env.guild.create_system_message(
                        channel, MessageType.PINS_ADD, author=alice, referenced_message=source
                    )
                else:
                    actions = {
                        "forward": lambda: alice.forward(source, channel),
                        "reply": lambda: alice.send(channel, "reply", reply_to=source),
                        "edit": lambda: alice.edit(source, "changed"),
                        "delete": lambda: alice.delete(source),
                        "react": lambda: alice.react(source, "✅"),
                        "unreact": lambda: alice.unreact(source, "✅"),
                        "set_reaction": lambda: alice.set_reaction(source, "✅", reacted=True),
                        "set_pinned": lambda: alice.set_pinned(source, True),
                        "set_poll_votes": lambda: alice.set_poll_votes(source, answers=[1]),
                        "click": lambda: alice.click(source, custom_id="button"),
                        "select": lambda: alice.select(source, ["value"], custom_id="select"),
                        "context_menu": lambda: alice.context_menu(channel, "Inspect", source),
                    }
                    await actions[operation]()
        assert [message.id for message in channel.history()] == before
        assert channel.history()[0].content == "local source"


@pytest.mark.parametrize("handle_type", ["sdk", "response"])
async def test_dm_actor_rejects_foreign_colliding_message(handle_type, monkeypatch):
    async with simcord.run(create_bot()) as foreign:
        guild = foreign.create_guild()
        foreign_alice = guild.add_member(foreign.create_user("alice"))
        source = await foreign_alice.send_dm("foreign DM")
        if handle_type == "response":
            source = simcord.ResponseMessage(
                foreign, foreign.backend.get_message(foreign_alice.user.dm_channel.id, source.id)
            )

    async with simcord.run(create_bot()) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        local = await alice.send_dm("local DM")
        assert source.id == local.id
        assert foreign_alice.user.dm_channel.id == alice.user.dm_channel.id

        def unexpected_lookup(*args, **kwargs):
            raise AssertionError("Foreign DM reached the local message lookup")

        with monkeypatch.context() as patch:
            patch.setattr(env.backend, "get_message", unexpected_lookup)
            with pytest.raises(simcord.SetupError, match="must belong to this environment"):
                await alice.user.edit(source, "changed")
        if handle_type == "response":
            local = simcord.ResponseMessage(env, env.backend.get_message(alice.user.dm_channel.id, local.id))
        await alice.user.edit(local, "local edit")
        assert alice.user.dm_channel.history()[0].content == "local edit"


@pytest.mark.parametrize("handle_type", ["callback", "original", "followup"])
async def test_actor_accepts_owned_interaction_and_webhook_handles_but_not_foreign_ones(handle_type):
    def make_bot():
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
        handles = {}

        @bot.tree.command(name="handles")
        async def handles_command(interaction: discord.Interaction):
            callback = await interaction.response.send_message("original")
            handles["callback"] = callback.resource
            handles["original"] = await interaction.original_response()
            handles["followup"] = await interaction.followup.send("followup", wait=True)

        return bot, handles

    foreign_bot, foreign_handles = make_bot()
    async with simcord.run(foreign_bot, strict_sync=False) as foreign:
        guild = foreign.create_guild()
        foreign_channel = guild.create_text_channel("general")
        foreign_alice = guild.add_member(foreign.create_user("alice"))
        await foreign_bot.tree.sync()
        await foreign_alice.slash(foreign_channel, "handles")

    bot, handles = make_bot()
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        await bot.tree.sync()
        await alice.slash(channel, "handles")
        source = handles[handle_type]
        assert source.id == foreign_handles[handle_type].id
        assert source.channel.id == foreign_handles[handle_type].channel.id
        await alice.react(source, "✅")
        forwarded = await alice.forward(source, channel)
        assert forwarded.message_snapshots[0].content == source.content
        assert env.backend.get_message(channel.id, source.id).reactions[0].user_ids == [alice.id]
        with pytest.raises(simcord.SetupError, match="must belong to this environment"):
            await alice.forward(foreign_handles[handle_type], channel)
