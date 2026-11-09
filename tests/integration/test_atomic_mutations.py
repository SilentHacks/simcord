import discord
import pytest

import simcord
from simcord.http import router


def _history(env):
    return (
        [entry for entry in env.backend.transcript if entry[0] == "GATEWAY"],
        list(env.guild.audit_log()),
    )


@pytest.mark.parametrize("edit", [{"voice_channel": None}, {"mute": True}, {"deafen": True}])
async def test_disconnected_combined_edit_is_atomic(env, alice, edit):
    await env.settle()
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    original = env.backend.get_member(env.guild.id, alice.id).nick
    before = _history(env)
    with pytest.raises(discord.HTTPException) as exc:
        await member.edit(nick="must not stick", **edit)
    assert exc.value.code == 40032
    await env.settle()
    assert env.backend.get_member(env.guild.id, alice.id).nick == original
    assert member.nick == original
    assert _history(env) == before


@pytest.mark.parametrize("destination", ["text", "foreign", "missing"])
async def test_invalid_voice_destination_preserves_combined_edit(env, alice, destination):
    voice = env.guild.create_voice_channel("source")
    await alice.join_voice(voice)
    if destination == "text":
        channel_id = env.guild.create_text_channel("not voice").id
        code = 50024
    elif destination == "foreign":
        channel_id = env.create_guild("other").create_voice_channel("elsewhere").id
        code = 10003
    else:
        channel_id = 123
        code = 10003
    await env.settle()
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    before = _history(env)
    with pytest.raises(discord.HTTPException) as exc:
        await env.bot.http.edit_member(env.guild.id, alice.id, nick="rejected", channel_id=channel_id)
    assert exc.value.code == code
    await env.settle()
    assert member.nick is None
    assert env.backend.get_member(env.guild.id, alice.id).nick is None
    assert env.guild.voice_states()[alice.id].channel_id == voice.id
    assert _history(env) == before


@pytest.mark.parametrize(
    "permission,edit",
    [
        ("mute_members", {"mute": True}),
        ("deafen_members", {"deafen": True}),
        ("move_members", {"voice_channel": None}),
    ],
)
async def test_voice_rights_are_distinct(env, alice, permission, edit):
    voice = env.guild.create_voice_channel("source")
    await alice.join_voice(voice)
    mask = ~discord.Permissions(**{permission: True}).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    before = _history(env)
    with pytest.raises(discord.Forbidden) as exc:
        await member.edit(nick="rejected", **edit)
    assert exc.value.code == 50013
    await env.settle()
    assert member.nick is None
    assert _history(env) == before


async def test_successful_combined_edit_commits_before_ordered_events(env, alice):
    source = env.guild.create_voice_channel("source")
    destination = env.guild.create_voice_channel("destination")
    await alice.join_voice(source)
    guild = env.bot.get_guild(env.guild.id)
    seen = []

    def observe(event, payload):
        if event not in {"GUILD_MEMBER_UPDATE", "VOICE_STATE_UPDATE"}:
            return
        member = env.backend.get_member(env.guild.id, alice.id)
        state = env.guild.voice_states()[alice.id]
        assert member.nick == "committed"
        assert member.mute and member.deaf and state.mute and state.deaf
        assert state.channel_id == destination.id
        seen.append(event)

    env.backend.subscribers.append(observe)
    try:
        await guild.get_member(alice.id).edit(
            nick="committed",
            mute=True,
            deafen=True,
            voice_channel=guild.get_channel(destination.id),
            reason="atomic",
        )
        await env.settle()
    finally:
        env.backend.subscribers.remove(observe)
    assert seen == ["GUILD_MEMBER_UPDATE", "VOICE_STATE_UPDATE"]
    cached = guild.get_member(alice.id)
    assert cached.nick == "committed"
    assert cached.voice.channel.id == destination.id
    assert cached.voice.mute and cached.voice.deaf
    assert all(entry.reason == "atomic" for entry in env.guild.audit_log())


@pytest.mark.parametrize("kind", ["above", "managed", "default"])
async def test_role_delete_rejects_unmanageable_roles_without_effects(env, kind):
    if kind == "default":
        role_id = env.guild.id
    else:
        role_id = env.guild.create_role(kind, position=100 if kind == "above" else 1).id
        if kind == "managed":
            env.backend.get_role(env.guild.id, role_id).managed = True
    await env.settle()
    before = _history(env)
    guild = env.bot.get_guild(env.guild.id)
    with pytest.raises(discord.Forbidden) as exc:
        await guild.get_role(role_id).delete(reason="denied")
    assert exc.value.code == 50013
    assert role_id in env.backend.get_guild(env.guild.id).roles
    assert _history(env) == before


def test_fixture_vanity_keeps_supplied_code_without_gateway_event(env):
    channel = env.guild.create_text_channel("landing")
    before = _history(env)
    env.guild.set_vanity_url("requested-code")
    invite = env.backend.get_invite("requested-code")
    assert invite.code == "requested-code"
    assert invite.guild_id == env.guild.id and invite.channel_id == channel.id
    assert env.backend.get_guild(env.guild.id).vanity_url_code == "requested-code"
    assert _history(env) == before


def test_unban_failure_preserves_state_and_history(env):
    before = _history(env)
    with pytest.raises(simcord.BackendError) as exc:
        router.dispatch(env.backend, "DELETE", f"/guilds/{env.guild.id}/bans/123")
    assert exc.value.code == 10026
    assert _history(env) == before


@pytest.mark.parametrize(
    "removed,edit,flag",
    [
        ("moderate_members", {"deafen": True}, "deaf"),
        ("deafen_members", {"mute": True}, "mute"),
        ("mute_members", {"deafen": True}, "deaf"),
    ],
)
async def test_voice_edit_does_not_require_unrelated_right(env, alice, removed, edit, flag):
    voice = env.guild.create_voice_channel("source")
    await alice.join_voice(voice)
    mask = ~discord.Permissions(**{removed: True}).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask
    guild = env.bot.get_guild(env.guild.id)
    await guild.get_member(alice.id).edit(**edit)
    await env.settle()
    assert getattr(env.guild.voice_states()[alice.id], flag) is True


@pytest.mark.parametrize("edit", [{"mute": True}, {"deafen": True}, {"voice_channel": None}])
async def test_voice_moderation_requires_target_hierarchy(env, edit):
    role = env.guild.create_role("higher", position=100)
    target = env.guild.add_member(env.create_user("higher"), roles=[role])
    voice = env.guild.create_voice_channel("source")
    await target.join_voice(voice)
    before = _history(env)
    guild = env.bot.get_guild(env.guild.id)
    with pytest.raises(discord.Forbidden) as exc:
        await guild.get_member(target.id).edit(**edit)
    assert exc.value.code == 50013
    assert _history(env) == before


async def test_lower_role_delete_commits_then_audits(env, alice):
    role = env.guild.create_role("lower")
    env.backend.add_member_role(env.guild.id, alice.id, role.id)
    await env.settle()
    guild = env.bot.get_guild(env.guild.id)
    await guild.get_role(role.id).delete(reason="allowed")
    await env.settle()
    assert role.id not in env.backend.get_guild(env.guild.id).roles
    assert role.id not in env.backend.get_member(env.guild.id, alice.id).role_ids
    assert guild.get_role(role.id) is None
    entry = env.guild.audit_log()[-1]
    assert entry.action_type == 32 and entry.reason == "allowed"


async def test_profile_update_is_backend_owned_and_updates_sdk(env):
    seen = []

    def observe(event, payload):
        if event == "USER_UPDATE":
            assert env.backend.bot_user.name == "Backend-owned"
            assert payload["username"] == "Backend-owned"
            seen.append(event)

    env.backend.subscribers.append(observe)
    try:
        edited = await env.bot.user.edit(username="Backend-owned")
        await env.settle()
    finally:
        env.backend.subscribers.remove(observe)
    assert edited.name == env.bot.user.name == "Backend-owned"
    assert seen == ["USER_UPDATE"]


async def test_unban_commits_before_event_and_records_api_reason(env, alice):
    guild = env.bot.get_guild(env.guild.id)
    await guild.ban(discord.Object(id=alice.id))
    await env.settle()
    seen = []

    def observe(event, payload):
        if event == "GUILD_BAN_REMOVE":
            assert alice.id not in env.backend.get_guild(env.guild.id).bans
            assert int(payload["user"]["id"]) == alice.id
            seen.append(event)

    env.backend.subscribers.append(observe)
    try:
        await guild.unban(discord.Object(id=alice.id), reason="appeal")
        await env.settle()
    finally:
        env.backend.subscribers.remove(observe)
    assert seen == ["GUILD_BAN_REMOVE"]
    assert env.guild.audit_log()[-1].reason == "appeal"


def test_prepared_message_is_detached_until_backend_commit(env):
    channel = env.guild.create_text_channel("prepared")
    before = _history(env)
    message = env.backend.prepare_message(channel.id, env.backend.bot_user.id, "prepared")
    assert message.id not in env.backend.messages[channel.id]
    assert env.backend.get_channel(channel.id).last_message_id is None
    assert _history(env) == before
    committed = env.backend.commit_message(message)
    assert committed is env.backend.get_message(channel.id, message.id)
    assert env.backend.get_channel(channel.id).last_message_id == message.id


async def test_invalid_voice_flag_does_not_commit_nickname(env, alice):
    voice = env.guild.create_voice_channel("source")
    await alice.join_voice(voice)
    before = _history(env)
    with pytest.raises(discord.HTTPException) as exc:
        await env.bot.http.edit_member(env.guild.id, alice.id, nick="rejected", mute="invalid")
    assert exc.value.code == 50035
    await env.settle()
    assert env.backend.get_member(env.guild.id, alice.id).nick is None
    assert env.bot.get_guild(env.guild.id).get_member(alice.id).nick is None
    assert _history(env) == before
