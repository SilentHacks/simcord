"""Public actor provenance and bot REST authority remain separate from token authority."""

import datetime

import discord
import pytest
from discord import app_commands
from discord.http import Route

import simcord
from fixtures.sample_bot import create_bot
from simcord import _dpy_internals


@pytest.mark.parametrize("operation", ["send", "slash", "autocomplete", "typing", "voice"])
async def test_actor_rejects_foreign_channel_before_lookup(env, alice, monkeypatch, operation):
    foreign = simcord.Env(create_bot())
    guild = foreign.create_guild()
    channel = guild.create_text_channel("foreign")

    def unexpected_lookup(*args, **kwargs):
        raise AssertionError("Foreign channel reached the local backend")

    with monkeypatch.context() as patch:
        patch.setattr(env.backend, "get_channel", unexpected_lookup)
        with pytest.raises(simcord.SetupError, match="environment"):
            if operation == "send":
                await alice.send(channel, "denied", attachments=[("secret.txt", b"secret")])
            elif operation == "slash":
                await alice.slash(channel, "panel")
            elif operation == "autocomplete":
                await alice.autocomplete(channel, "tag", "name", "py")
            elif operation == "typing":
                await alice.typing(channel)
            else:
                await alice.join_voice(channel)


async def test_actor_send_rejects_other_guild_and_unjoined_private_thread(env, channel, alice):
    other = env.create_guild("other")
    destination = other.create_text_channel("destination")
    with pytest.raises(simcord.SetupError, match="guild"):
        await alice.send(destination, "denied", attachments=[("secret.txt", b"secret")])
    assert destination.history() == []

    thread = await env.bot.get_channel(channel.id).create_thread(
        name="private", type=discord.ChannelType.private_thread
    )
    private = simcord.ChannelHandle(env, channel.guild, env.backend.get_channel(thread.id))
    with pytest.raises(simcord.BackendError) as caught:
        await alice.send(private, "denied")
    assert caught.value.code == 50001
    assert private.history() == []
    await thread.add_user(alice.member)
    assert (await alice.send(private, "joined")).content == "joined"


@pytest.mark.parametrize("kind", ["category", "role", "member", "webhook", "viewer"])
def test_builder_rejects_foreign_handles_without_mutation(env, channel, alice, kind):
    foreign = simcord.Env(create_bot())
    guild = foreign.create_guild()
    foreign_user = foreign.create_user("foreign")
    member = guild.add_member(foreign_user)
    foreign_channel = guild.create_text_channel("foreign")
    role = guild.create_role("foreign")
    before_channels = tuple(env.backend.channels)
    before_members = env.guild.member_ids()
    before_webhooks = tuple(env.backend.webhooks)
    with pytest.raises(simcord.SetupError, match="environment"):
        if kind == "category":
            env.guild.create_text_channel("denied", category=foreign_channel)
        elif kind == "role":
            env.guild.add_member(alice.user, roles=[role])
        elif kind == "member":
            env.guild.add_member(foreign_user)
        elif kind == "webhook":
            env.guild.create_webhook(foreign_channel)
        else:
            channel.history(viewer=member)
    assert tuple(env.backend.channels) == before_channels
    assert env.guild.member_ids() == before_members
    assert tuple(env.backend.webhooks) == before_webhooks


async def test_context_menu_rejects_hidden_and_other_viewer_ephemeral_targets(env, channel, alice):
    @app_commands.context_menu(name="Inspect")
    async def inspect_message(interaction: discord.Interaction, message: discord.Message):
        await interaction.response.send_message(message.content)

    env.bot.tree.add_command(inspect_message)
    await env.bot.tree.sync()
    bob = env.guild.add_member(env.create_user("bob"))
    private = env.guild.create_text_channel(
        "hidden", overwrites={alice: discord.PermissionOverwrite(view_channel=False)}
    )
    hidden = await env.bot.get_channel(private.id).send("hidden target")
    ephemeral = await bob.slash(channel, "ban", user=alice)
    before = len(env.backend.interactions)
    for target in (hidden, ephemeral.response.message):
        with pytest.raises(simcord.SetupError):
            await alice.context_menu(channel, "Inspect", target)
    assert len(env.backend.interactions) == before
    public = await alice.send(channel, "visible target")
    assert (await alice.context_menu(channel, "Inspect", public)).response.content == "visible target"


async def test_foreign_slash_and_select_options_never_dispatch_or_upload(env, channel, alice, monkeypatch):
    foreign = simcord.Env(create_bot())
    guild = foreign.create_guild()
    target = guild.add_member(foreign.create_user("foreign"))

    @app_commands.command(name="access-upload")
    async def access_upload(
        interaction: discord.Interaction, attachment: discord.Attachment, user: discord.User
    ):
        await interaction.response.send_message(user.name)

    env.bot.tree.add_command(access_upload)
    await env.bot.tree.sync()
    before = len(env.backend.interactions)

    def unexpected_upload(*args, **kwargs):
        raise AssertionError("Invalid handle reached CDN storage")

    monkeypatch.setattr(env.backend.cdn, "store_attachment", unexpected_upload)
    with pytest.raises(simcord.SetupError, match="environment"):
        await alice.slash(channel, "access-upload", attachment=("secret.txt", b"secret"), user=target)
    assert len(env.backend.interactions) == before
    panel = await alice.slash(channel, "assign")
    before = len(env.backend.interactions)
    with pytest.raises(simcord.SetupError, match="environment"):
        await alice.select(panel.response.message, [target], custom_id="who")
    assert len(env.backend.interactions) == before


async def test_bot_denied_reads_and_webhook_enumeration_preserve_token_authority(env, channel, alice):
    native = env.bot.get_channel(channel.id)
    message = await native.send("secret")
    await message.pin()
    hook = await native.create_webhook(name="authorized token")
    await native.set_permissions(
        env.guild.me,
        overwrite=discord.PermissionOverwrite(
            view_channel=False, read_message_history=False, manage_webhooks=False
        ),
    )
    with pytest.raises(discord.Forbidden) as caught:
        await native.fetch_message(message.id)
    assert caught.value.code == 50001
    with pytest.raises(discord.Forbidden):
        await native.webhooks()
    with pytest.raises(discord.Forbidden):
        _ = [item async for item in native.history()]
    with pytest.raises(discord.Forbidden):
        _ = [item async for item in native.pins()]

    # Incoming-webhook tokens do not inherit the bot's channel permissions.
    token_message = await hook.send("token still authorized", wait=True)
    assert (await hook.fetch_message(token_message.id)).content == "token still authorized"
    await hook.edit(name="token edit", prefer_auth=False)
    assert (await hook.fetch(prefer_auth=False)).name == "token edit"

    # Human authority and omnipotent fixture reads are not bot authority.
    assert (await alice.send(channel, "human still authorized")).content == "human still authorized"
    assert message.id in {item.id for item in channel.history()}


async def test_missing_history_returns_empty_list_but_known_message_fetch_is_denied(env, channel):
    native = env.bot.get_channel(channel.id)
    message = await native.send("not readable")
    await native.set_permissions(
        env.guild.me, overwrite=discord.PermissionOverwrite(read_message_history=False)
    )
    assert [item async for item in native.history()] == []
    with pytest.raises(discord.Forbidden) as caught:
        await native.fetch_message(message.id)
    assert caught.value.code == 50013


async def test_dm_and_interaction_token_reads_survive_bot_guild_denial(env, channel, alice):
    dm = await alice.send_dm("legitimate DM")
    native_dm = await alice.member.create_dm()
    assert (await native_dm.fetch_message(dm.id)).content == "legitimate DM"
    response = await alice.slash(channel, "ban", user=alice)
    native = env.bot.get_channel(channel.id)
    await native.set_permissions(env.guild.me, overwrite=discord.PermissionOverwrite(view_channel=False))
    token = response._interaction.token
    payload = await env.bot.http.request(
        Route("GET", f"/webhooks/{env.backend.application_id}/{token}/messages/@original")
    )
    assert payload["id"] == str(response.response.id)


async def test_private_thread_moderator_can_send_without_joining(env, channel, alice):
    native = env.bot.get_channel(channel.id)
    await native.set_permissions(alice.member, overwrite=discord.PermissionOverwrite(manage_threads=True))
    thread = await native.create_thread(name="private", type=discord.ChannelType.private_thread)
    private = simcord.ChannelHandle(env, channel.guild, env.backend.get_channel(thread.id))
    assert alice.id not in env.backend.get_channel(thread.id).thread_members
    assert (await alice.send(private, "moderator access")).content == "moderator access"


async def test_modal_entity_provenance_is_checked_before_upload_and_consumption(
    env, channel, alice, monkeypatch
):
    class Form(discord.ui.Modal, title="Identity"):
        upload = discord.ui.Label(text="Upload", component=discord.ui.FileUpload(custom_id="upload"))
        person = discord.ui.Label(text="Person", component=discord.ui.UserSelect(custom_id="person"))

        async def on_submit(self, interaction: discord.Interaction):
            await interaction.response.send_message("accepted")

    @app_commands.command(name="access-modal")
    async def access_modal(interaction: discord.Interaction):
        await interaction.response.send_modal(Form())

    env.bot.tree.add_command(access_modal)
    await env.bot.tree.sync()
    shown = await alice.slash(channel, "access-modal")
    foreign = simcord.Env(create_bot())
    target = foreign.create_guild().add_member(foreign.create_user("foreign"))

    def unexpected_upload(*args, **kwargs):
        raise AssertionError("Foreign modal selection reached CDN storage")

    with monkeypatch.context() as patch:
        patch.setattr(env.backend.cdn, "store_attachment", unexpected_upload)
        with pytest.raises(simcord.SetupError, match="environment"):
            await alice.submit_modal(shown, {"upload": ("secret.txt", b"secret"), "person": [target]})
    assert not shown._interaction.modal_consumed
    result = await alice.submit_modal(shown, {"upload": ("legitimate.txt", b"public"), "person": [alice]})
    assert result.response.content == "accepted"


async def test_reaction_policies_keep_existing_emoji_and_deny_missing_history(env, channel, alice):
    message = await alice.send(channel, "react here")
    await alice.react(message, "✅")
    native = env.bot.get_channel(channel.id)
    await native.set_permissions(env.guild.me, overwrite=discord.PermissionOverwrite(add_reactions=False))
    fetched = await native.fetch_message(message.id)
    await fetched.add_reaction("✅")
    with pytest.raises(discord.Forbidden) as caught:
        await fetched.add_reaction("❌")
    assert caught.value.code == 50013
    await native.set_permissions(
        env.guild.me, overwrite=discord.PermissionOverwrite(read_message_history=False)
    )
    with pytest.raises(discord.Forbidden):
        _ = [user async for user in fetched.reactions[0].users()]
    with pytest.raises(discord.Forbidden):
        await fetched.remove_reaction("✅", env.bot.user)
    stored = env.backend.get_message(channel.id, message.id)
    assert env.bot.user.id in stored.reaction_for("✅").user_ids


@pytest.mark.parametrize("kind", ["user", "role", "channel"])
async def test_entity_select_rejects_other_guild_context(env, channel, alice, kind):
    other = env.create_guild("other")
    if kind == "user":
        target, custom_id = other.add_member(env.create_user("outsider")), "who"
    elif kind == "role":
        target, custom_id = other.create_role("foreign role"), "role"
    else:
        target, custom_id = other.create_text_channel("foreign channel"), "chan"
    panel = await alice.slash(channel, "assign")
    before = len(env.backend.interactions)
    with pytest.raises(simcord.SetupError, match="guild"):
        await alice.select(panel.response.message, [target], custom_id=custom_id)
    assert len(env.backend.interactions) == before


async def test_webhook_enumeration_requires_manage_webhooks_even_with_view_access(env, channel):
    native = env.bot.get_channel(channel.id)
    hook = await native.create_webhook(name="private token")
    await native.set_permissions(env.guild.me, overwrite=discord.PermissionOverwrite(manage_webhooks=False))
    with pytest.raises(discord.Forbidden) as caught:
        await native.webhooks()
    assert caught.value.code == 50013
    assert (await hook.fetch(prefer_auth=False)).id == hook.id


async def test_bot_channel_api_never_exposes_ephemeral_messages(env, channel, alice):
    response = await alice.slash(channel, "ban", user=alice)
    native = env.bot.get_channel(channel.id)
    with pytest.raises(discord.NotFound) as caught:
        await native.fetch_message(response.response.id)
    assert caught.value.code == 10008
    assert response.response.id not in {item.id async for item in native.history()}


async def test_sdk_command_and_event_handles_retain_world_provenance(env, alice, monkeypatch):
    command = (await env.bot.tree.fetch_commands())[0]
    local = env.guild.create_scheduled_event(
        "Local",
        start_time=env.backend.iso_after(3600),
        entity_type=3,
        location="Online",
        end_time=env.backend.iso_after(7200),
    )
    local_event = await env.bot.get_guild(env.guild.id).fetch_scheduled_event(local.id)
    foreign = simcord.Env(create_bot())
    foreign_state = _dpy_internals.get_state(foreign.bot)
    foreign_command = app_commands.AppCommand(data=command.to_dict(), state=foreign_state)
    event_payload = await env.bot.http.request(
        Route("GET", f"/guilds/{env.guild.id}/scheduled-events/{local.id}")
    )
    foreign_event = discord.ScheduledEvent(state=foreign_state, data=event_payload)

    def unexpected_event_lookup(*args, **kwargs):
        raise AssertionError("Foreign event reached local event lookup")

    with monkeypatch.context() as patch:
        patch.setattr(env.backend, "get_scheduled_event", unexpected_event_lookup)
        with pytest.raises(simcord.SetupError, match="environment"):
            await alice.subscribe_event(foreign_event)
    with pytest.raises(simcord.SetupError, match="environment"):
        env.guild.set_command_permissions(foreign_command, {alice: True})

    env.guild.set_command_permissions(command, {alice: True})
    await alice.subscribe_event(local_event)
    assert alice.id in {user.id async for user in local_event.users()}


async def test_unbound_same_world_user_option_is_valid_in_guild_and_dm(env, channel, alice):
    @app_commands.command(name="access-user")
    async def access_user(interaction: discord.Interaction, user: discord.User):
        await interaction.response.send_message(user.name)

    env.bot.tree.add_command(access_user)
    await env.bot.tree.sync()
    user = env.create_user("not a guild member")
    assert user.id not in env.guild.member_ids()
    result = await alice.slash(channel, "access-user", user=user)
    assert result.response.content == user.name
    dm_result = await alice.user.slash("access-user", user=user)
    assert dm_result.response.content == user.name


async def test_bot_denied_sibling_message_routes_do_not_expose_or_mutate(env, channel, alice):
    native = env.bot.get_channel(channel.id)
    poll = discord.Poll(question="Private vote", duration=datetime.timedelta(hours=1))
    poll.add_answer(text="Yes")
    message = await native.send("unchanged", poll=poll)
    await message.pin()
    await alice.react(message, "✅")
    stored = env.backend.get_message(channel.id, message.id)
    root = f"/channels/{channel.id}"
    await native.set_permissions(env.guild.me, overwrite=discord.PermissionOverwrite(view_channel=False))
    requests = [
        ("PATCH", f"{root}/messages/{message.id}", {"content": "changed"}),
        ("DELETE", f"{root}/messages/{message.id}", None),
        ("PUT", f"{root}/messages/pins/{message.id}", None),
        ("DELETE", f"{root}/messages/pins/{message.id}", None),
        ("GET", f"{root}/polls/{message.id}/answers/1", None),
        ("POST", f"{root}/polls/{message.id}/expire", None),
        ("GET", f"{root}/messages/{message.id}/reactions/✅", None),
        ("PUT", f"{root}/messages/{message.id}/reactions/✅/@me", None),
        ("DELETE", f"{root}/messages/{message.id}/reactions/✅/@me", None),
        ("DELETE", f"{root}/messages/{message.id}/reactions/✅/{alice.id}", None),
        ("DELETE", f"{root}/messages/{message.id}/reactions/✅", None),
        ("DELETE", f"{root}/messages/{message.id}/reactions", None),
    ]
    for method, path, body in requests:
        with pytest.raises(discord.Forbidden) as caught:
            await env.bot.http.request(Route(method, path), json=body)
        assert caught.value.code == 50001
        assert env.backend.get_message(channel.id, message.id) is stored
        assert stored.content == "unchanged" and stored.pinned
        assert not stored.poll.finalized
        assert stored.reaction_for("✅").user_ids == [alice.id]


async def test_actor_can_join_existing_reaction_without_add_reactions(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    message = await bob.send(channel, "existing reaction")
    await bob.react(message, "✅")
    await env.bot.get_channel(channel.id).set_permissions(
        alice.member, overwrite=discord.PermissionOverwrite(add_reactions=False)
    )
    await alice.react(message, "✅")
    await alice.set_reaction(message, "✅", reacted=False)
    await alice.set_reaction(message, "✅", reacted=True)
    with pytest.raises(simcord.BackendError) as caught:
        await alice.react(message, "❌")
    assert caught.value.code == 50013
