"""Observable permission diagnostics and canonical sweep projection."""

from __future__ import annotations

import datetime
import json

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

from simcord._diagnostics import PermissionRun
from simcord.backend import permissions
from simcord.backend.errors import BackendError
from simcord.backend.models import Channel, Guild, Member, Overwrite, Role
from simcord.enums import OverwriteType
from simcord.http.router import RequestContext, dispatch

NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
BITS = st.integers(min_value=0, max_value=discord.Permissions.all().value)


@given(BITS, BITS, BITS, BITS, BITS, BITS, st.sampled_from([1, 2, 999]), st.booleans())
def test_explanation_agrees_with_compute(
    everyone, role, allow, deny, member_allow, member_deny, actor, timeout
):
    guild = Guild(id=100, name="trace", owner_id=1)
    guild.roles[100] = Role(id=100, name="@everyone", permissions=everyone)
    guild.roles[200] = Role(id=200, name="role", permissions=role)
    guild.roles[201] = Role(id=201, name="second role", permissions=0)
    guild.members[2] = Member(
        user_id=2,
        role_ids=[200, 201],
        timed_out_until="2099-01-01T00:00:00+00:00" if timeout else None,
    )
    channel = Channel(
        id=300,
        type=0,
        guild_id=100,
        overwrites=[
            Overwrite(target_id=100, type=OverwriteType.ROLE, allow=deny, deny=allow),
            Overwrite(target_id=200, type=OverwriteType.ROLE, allow=allow, deny=deny),
            Overwrite(target_id=201, type=OverwriteType.ROLE, allow=member_deny, deny=member_allow),
            Overwrite(target_id=2, type=OverwriteType.MEMBER, allow=member_allow, deny=member_deny),
        ],
    )
    for context in (None, channel):
        result = permissions.explain(guild, actor, context, now=NOW)
        assert result["effective"] == permissions.compute(guild, actor, context, now=NOW)
        json.dumps(result)
        previous = 0
        for step in result["steps"]:
            assert step["before"] == previous
            assert step["after"] == (previous & ~step["deny"]) | step["allow"]
            previous = step["after"]


async def test_denials_preserve_discord_body_and_attribute_request(env, channel, alice):
    backend = env.backend
    bot_id = backend.bot_user.id
    backend.set_overwrite(
        channel.id,
        Overwrite(
            target_id=bot_id,
            type=OverwriteType.MEMBER,
            deny=discord.Permissions.send_messages.flag,
        ),
    )
    with pytest.raises(BackendError) as denied:
        dispatch(backend, "POST", f"/channels/{channel.id}/messages", json={"content": "no"})
    assert denied.value.to_json() == {"code": 50013, "message": "Missing Permissions"}
    entry = env.http_requests[-1]
    assert entry.denial["reason"] == "missing_permissions"
    assert entry.denial["missing"] == ["send_messages"]
    assert entry.denial["channel_id"] == channel.id
    assert "DENIAL" in env.transcript()
    assert env.observed_permissions().send_messages
    checks = len(env.permission_checks)
    backend.require_permissions(env.guild.id, alice.id, channel.id, "send_messages")
    assert len(env.permission_checks) == checks  # actor checks are not bot observations
    ctx = RequestContext(backend, {})
    with pytest.raises(BackendError) as access:
        ctx.require_channel_permissions(channel.id, "send_messages", denied_code=50001)
    assert access.value.to_json() == {"code": 50001, "message": "Missing Access"}
    assert env.permission_checks[-1]["missing"] == ["send_messages"]
    explanation = env.explain_permissions(bot_id, channel)
    assert explanation["effective"] == backend.compute_permissions(env.guild.id, bot_id, channel.id)
    assert explanation["steps"][-1]["source"] == "member_overwrite"
    json.dumps(env.permission_checks)


async def test_missing_access_and_hierarchy_are_not_permission_flags(env, channel):
    backend = env.backend
    backend.set_overwrite(
        channel.id,
        Overwrite(
            target_id=backend.bot_user.id,
            type=OverwriteType.MEMBER,
            deny=discord.Permissions.view_channel.flag,
        ),
    )
    with pytest.raises(BackendError) as denied:
        dispatch(backend, "GET", f"/channels/{channel.id}")
    assert denied.value.code == 50001
    assert env.http_requests[-1].denial["reason"] == "missing_access"
    owner_id = backend.get_guild(env.guild.id).owner_id
    with pytest.raises(BackendError) as hierarchy:
        dispatch(backend, "DELETE", f"/guilds/{env.guild.id}/members/{owner_id}")
    assert hierarchy.value.to_json() == {"code": 50013, "message": "Missing Permissions"}
    assert env.http_requests[-1].denial["reason"] == "hierarchy"
    assert env.http_requests[-1].denial["target_id"] == owner_id


async def test_degradations_are_only_an_explicit_assertion(env, channel):
    env.assert_no_degradations()
    env.backend.record_degradation("embed_suppressed", channel_id=channel.id, missing=["embed_links"])
    saved = env.degradations
    saved[0]["missing"].append("unrelated")
    assert env.degradations == [
        {"kind": "embed_suppressed", "channel_id": channel.id, "missing": ["embed_links"]}
    ]
    json.dumps(env.degradations)
    with pytest.raises(AssertionError, match="embed_suppressed"):
        env.assert_no_degradations()
    env.raise_errors()  # degradation records are not bot exceptions


@pytest.mark.parametrize(
    "flag,guild_scope", [("send_messages", False), ("view_channel", False), ("manage_channels", True)]
)
async def test_sweep_backend_rest_interaction_and_cache_agree(env, channel, alice, flag, guild_scope):
    backend = env.backend
    before = backend.compute_permissions(env.guild.id, alice.id, channel.id)
    run = PermissionRun(flag=flag, guild_scope=guild_scope)
    run.register(env)
    bit = permissions.flag(flag)
    assert not backend.compute_permissions(env.guild.id, backend.bot_user.id, channel.id) & bit
    if guild_scope:
        assert not backend.compute_permissions(env.guild.id, backend.bot_user.id) & bit
    assert backend.compute_permissions(env.guild.id, alice.id, channel.id) == before
    await env.settle()
    cached = env.bot.get_channel(channel.id)
    assert not getattr(cached.permissions_for(cached.guild.me), flag)
    if guild_scope:
        assert not getattr(cached.guild.me.guild_permissions, flag)
    with pytest.raises(discord.Forbidden) as denied:
        if flag == "send_messages":
            await cached.send("denied")
        elif flag == "view_channel":
            await env.bot.fetch_channel(channel.id)
        else:
            await cached.edit(name="denied")
    assert denied.value.code == (50001 if flag == "view_channel" else 50013)
    seen = []

    @env.bot.tree.command(name="trace-sweep", description="Observe application permissions")
    async def trace_sweep(interaction: discord.Interaction):
        seen.append(getattr(interaction.app_permissions, flag))
        await interaction.response.defer(ephemeral=True)

    await env.bot.tree.sync()
    await alice.slash(channel, "trace-sweep")
    assert seen == [False]
    # New channels and subsequent grants cannot escape the canonical projection.
    other = env.guild.create_text_channel("dynamic")
    creation_events = [
        event
        for kind, event, payload in backend.transcript
        if kind == "GATEWAY" and payload.get("id") == str(other.id)
    ]
    assert creation_events == ["CHANNEL_CREATE"]
    bot_role = backend.get_guild(env.guild.id).members[backend.bot_user.id].role_ids[0]
    grants = discord.Permissions.all().value & ~discord.Permissions.administrator.flag
    backend.edit_role(env.guild.id, bot_role, {"permissions": grants})
    await env.settle()
    dynamic = env.bot.get_channel(other.id)
    assert not getattr(dynamic.permissions_for(dynamic.guild.me), flag)
    assert not backend.compute_permissions(env.guild.id, backend.bot_user.id, other.id) & bit
    assert not run.unsupported
    backend.permission_projection = None


async def test_administrator_revocation_is_unsupported_without_losing_unrelated_grants(env, channel, alice):
    backend = env.backend
    guild = backend.get_guild(env.guild.id)
    bot_role = guild.members[backend.bot_user.id].role_ids[0]
    backend.edit_role(guild.id, bot_role, {"permissions": discord.Permissions.administrator.flag})
    await env.settle()
    before = backend.compute_permissions(guild.id, backend.bot_user.id, channel.id)
    actor_before = backend.compute_permissions(guild.id, alice.id, channel.id)
    roles_before = {ident: role.permissions for ident, role in guild.roles.items()}
    overwrites_before = list(backend.channels[channel.id].overwrites)
    run = PermissionRun(flag="embed_links")
    run.register(env)
    assert backend.compute_permissions(guild.id, backend.bot_user.id, channel.id) == before
    assert run.unsupported
    assert {ident: role.permissions for ident, role in guild.roles.items()} == roles_before
    assert backend.channels[channel.id].overwrites == overwrites_before
    assert backend.compute_permissions(guild.id, alice.id, channel.id) == actor_before
    await env.settle()
    cached = env.bot.get_channel(channel.id)
    assert cached.permissions_for(cached.guild.me).administrator
    backend.permission_projection = None
