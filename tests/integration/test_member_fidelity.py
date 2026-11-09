"""Member REST boundary checks; timeout policy also covers non-bot actors."""

from datetime import timedelta

import discord
import pytest

from simcord.backend import errors
from simcord.http.router import dispatch


@pytest.mark.parametrize("self_route", [False, True])
async def test_nickname_boundary_and_clear_are_atomic(env, alice, self_route):
    guild_id = env.guild.id
    user_id = env.bot.user.id if self_route else alice.id
    path = f"/guilds/{guild_id}/members/{'@me' if self_route else user_id}"
    dispatch(env.backend, "PATCH", path, json={"nick": "n" * 32})
    member = env.backend.get_member(guild_id, user_id)
    with pytest.raises(errors.BackendError) as caught:
        dispatch(env.backend, "PATCH", path, json={"nick": "n" * 33})
    assert caught.value.status == 400 and caught.value.code == 50035
    assert caught.value.errors == {
        "nick": {"_errors": [{"code": "BASE_TYPE_MAX_LENGTH", "message": "Must be 32 or fewer in length."}]}
    }
    assert member.nick == "n" * 32
    dispatch(env.backend, "PATCH", path, json={"nick": None})
    await env.settle()
    assert member.nick is None
    assert env.bot.get_guild(guild_id).get_member(user_id).nick is None


async def test_timeout_user_policy_and_atomic_rejection(env, alice):
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    now = env.backend.clock.now()
    await member.timeout(now + timedelta(days=28))
    before = env.backend.get_member(env.guild.id, alice.id).timed_out_until
    with pytest.raises(discord.HTTPException) as caught:
        await member.edit(nick="not committed", timed_out_until=now + timedelta(days=29))
    assert caught.value.status == 400 and caught.value.code == 50035
    assert env.backend.get_member(env.guild.id, alice.id).nick is None
    assert env.backend.get_member(env.guild.id, alice.id).timed_out_until == before
    with pytest.raises(errors.BackendError) as raw:
        dispatch(
            env.backend,
            "PATCH",
            f"/guilds/{env.guild.id}/members/{alice.id}",
            json={"communication_disabled_until": (now + timedelta(days=29)).isoformat()},
        )
    assert raw.value.errors == {
        "communication_disabled_until": {
            "_errors": [
                {
                    "code": "INVALID_COMMUNICATION_DISABLED_TIMESTAMP",
                    "message": "Invalid communication disabled timestamp",
                }
            ]
        }
    }
    await member.timeout(now - timedelta(minutes=1))
    await env.settle()
    assert not member.is_timed_out()
    await member.timeout(None)
    await env.settle()
    assert not member.is_timed_out()


async def test_timeout_administrator_rejected_without_nickname_mutation(env, alice):
    role = env.guild.create_role("admin", permissions=discord.Permissions(administrator=True))
    env.backend.add_member_role(env.guild.id, alice.id, role.id)
    await env.settle()
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    with pytest.raises(discord.Forbidden) as caught:
        await member.edit(
            nick="not committed", timed_out_until=env.backend.clock.now() + timedelta(minutes=5)
        )
    assert caught.value.code == 50013
    assert env.backend.get_member(env.guild.id, alice.id).nick is None
    assert env.backend.get_member(env.guild.id, alice.id).timed_out_until is None


async def test_non_administrator_bot_target_can_be_timed_out(env):
    target = env.guild.add_member(env.create_user("timeout bot", bot=True))
    await env.settle()
    member = env.bot.get_guild(env.guild.id).get_member(target.id)
    await member.timeout(env.backend.clock.now() + timedelta(minutes=1))
    await env.settle()
    assert member.is_timed_out()
