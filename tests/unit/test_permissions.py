"""Property-based and table tests for the permissions engine."""

import discord
from hypothesis import given
from hypothesis import strategies as st

from simcord.backend import permissions
from simcord.backend.models import Channel, Guild, Member, Overwrite, Role

ALL = discord.Permissions.all().value
ADMIN = discord.Permissions.administrator.flag
SEND = discord.Permissions.send_messages.flag
VIEW = discord.Permissions.view_channel.flag

perm_values = st.integers(min_value=0, max_value=ALL)


def make_guild(everyone_permissions: int = 0) -> Guild:
    guild = Guild(id=100, name="g", owner_id=1)
    guild.roles[100] = Role(id=100, name="@everyone", permissions=everyone_permissions)
    return guild


@given(perm_values, perm_values)
def test_owner_always_has_everything(everyone, role_perms):
    guild = make_guild(everyone)
    guild.roles[200] = Role(id=200, name="r", permissions=role_perms)
    guild.members[1] = Member(user_id=1, role_ids=[200])
    assert permissions.compute(guild, 1) == ALL


@given(perm_values, perm_values, perm_values)
def test_administrator_bypasses_overwrites(everyone, allow, deny):
    guild = make_guild(everyone)
    guild.roles[200] = Role(id=200, name="admin", permissions=ADMIN)
    guild.members[2] = Member(user_id=2, role_ids=[200])
    channel = Channel(
        id=300, type=0, guild_id=100, overwrites=[Overwrite(target_id=100, type=0, allow=allow, deny=deny)]
    )
    assert permissions.compute(guild, 2, channel) == ALL


@given(perm_values, perm_values)
def test_base_is_union_of_roles(everyone, role_perms):
    guild = make_guild(everyone)
    guild.roles[200] = Role(id=200, name="r", permissions=role_perms)
    guild.members[2] = Member(user_id=2, role_ids=[200])
    computed = permissions.compute(guild, 2)
    if not (everyone | role_perms) & ADMIN:
        assert computed == everyone | role_perms


@given(perm_values)
def test_member_overwrite_wins_over_role_overwrite(everyone):
    guild = make_guild(everyone | SEND)
    guild.members[2] = Member(user_id=2, role_ids=[])
    channel = Channel(
        id=300,
        type=0,
        guild_id=100,
        overwrites=[
            Overwrite(target_id=100, type=0, deny=SEND),  # @everyone: no sending
            Overwrite(target_id=2, type=1, allow=SEND),  # but this member may
        ],
    )
    assert permissions.compute(guild, 2, channel) & SEND


def test_non_member_has_no_permissions():
    guild = make_guild(ALL & ~ADMIN)
    assert permissions.compute(guild, 999) == 0


def test_timeout_masks_to_read_only():
    guild = make_guild(VIEW | SEND)
    guild.members[2] = Member(user_id=2, role_ids=[], timed_out_until="9999-01-01T00:00:00+00:00")
    computed = permissions.compute(guild, 2)
    assert computed & VIEW
    assert not computed & SEND
