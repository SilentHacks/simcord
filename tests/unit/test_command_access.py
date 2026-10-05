from dataclasses import dataclass

import discord
import pytest

from simcord.backend import Backend
from simcord.backend.command_access import Access, command_access
from simcord.enums import AppCommandType, ChannelType


@dataclass
class World:
    backend: Backend
    guild_id: int
    user_id: int
    channel_id: int
    dm_channel_id: int


def make_world() -> World:
    backend = Backend()
    owner = backend.make_user("owner")
    guild = backend.create_guild("guild", owner_id=owner.id)
    guild.everyone_role.permissions = discord.Permissions(use_application_commands=True).value
    user = backend.make_user("member")
    backend.add_member(guild.id, user.id)
    channel = backend.create_channel(guild.id, "general", announce=False)
    dm = backend.create_channel(None, None, type=ChannelType.DM, announce=False)
    return World(backend, guild.id, user.id, channel.id, dm.id)


def make_command(**fields: object) -> dict:
    command = {
        "id": "12345",
        "name": "example",
        "type": AppCommandType.CHAT_INPUT,
        "default_member_permissions": None,
    }
    command.update(fields)
    return command


def result(world: World, command: dict, *, channel_id: int | None = None) -> Access:
    return command_access(
        world.backend,
        command,
        user_id=world.user_id,
        channel_id=world.channel_id if channel_id is None else channel_id,
    )


def set_command_overrides(world: World, command: dict, entries: list[dict]) -> None:
    world.backend.set_command_permissions(world.guild_id, int(command["id"]), entries)


def set_application_overrides(world: World, entries: list[dict]) -> None:
    world.backend.set_command_permissions(world.guild_id, world.backend.application_id, entries)


def override(target_id: int, target_type: int, permission: bool) -> dict:
    return {"id": str(target_id), "type": target_type, "permission": permission}


@pytest.mark.parametrize(
    ("case", "allowed", "reason"),
    [
        ("guild-command-other-guild", False, "scope"),
        ("guild-context-default", True, None),
        ("guild-context-excludes-guild", False, "context"),
        ("dm-context-bot-dm", True, None),
        ("dm-context-private-only", False, "context"),
        ("dm-legacy-permission-false", False, "context"),
        ("guild-command-in-dm", False, "scope"),
    ],
)
def test_scope_and_context(case: str, allowed: bool, reason: str | None) -> None:
    world = make_world()
    channel_id = world.channel_id
    fields: dict[str, object] = {}
    if case == "guild-command-other-guild":
        other_guild = world.backend.create_guild("other")
        fields["guild_id"] = str(other_guild.id)
    elif case == "guild-context-excludes-guild":
        fields["contexts"] = [1]
    elif case == "dm-context-bot-dm":
        channel_id = world.dm_channel_id
        fields["contexts"] = [1]
    elif case == "dm-context-private-only":
        channel_id = world.dm_channel_id
        fields["contexts"] = [2]
    elif case == "dm-legacy-permission-false":
        channel_id = world.dm_channel_id
        fields["dm_permission"] = False
    elif case == "guild-command-in-dm":
        channel_id = world.dm_channel_id
        fields["guild_id"] = str(world.guild_id)

    access = result(world, make_command(**fields), channel_id=channel_id)
    assert access == Access(allowed, reason)


@pytest.mark.parametrize(
    ("case", "allowed", "reason"),
    [
        ("nsfw-ordinary-channel", False, "nsfw"),
        ("nsfw-age-restricted-channel", True, None),
        ("nsfw-dm", False, "nsfw"),
        ("thread-parent-nsfw", True, None),
        ("thread-parent-not-nsfw", False, "nsfw"),
    ],
)
def test_nsfw_and_thread_parent(case: str, allowed: bool, reason: str | None) -> None:
    world = make_world()
    command = make_command(nsfw=True)
    channel_id = world.channel_id
    if case == "nsfw-age-restricted-channel":
        world.backend.get_channel(world.channel_id).nsfw = True
    elif case == "nsfw-dm":
        channel_id = world.dm_channel_id
    elif case.startswith("thread-"):
        parent = world.backend.get_channel(world.channel_id)
        parent.nsfw = case == "thread-parent-nsfw"
        thread = world.backend.create_channel(
            world.guild_id,
            "thread",
            type=ChannelType.PUBLIC_THREAD,
            parent_id=parent.id,
            announce=False,
        )
        channel_id = thread.id

    assert result(world, command, channel_id=channel_id) == Access(allowed, reason)


def test_thread_channel_override_uses_parent_channel() -> None:
    world = make_world()
    command = make_command()
    parent = world.backend.get_channel(world.channel_id)
    thread = world.backend.create_channel(
        world.guild_id,
        "thread",
        type=ChannelType.PUBLIC_THREAD,
        parent_id=parent.id,
        announce=False,
    )
    set_command_overrides(world, command, [override(parent.id, 3, False)])

    assert result(world, command, channel_id=thread.id) == Access(False, "channel-denied")


@pytest.mark.parametrize(
    ("case", "allowed", "reason"),
    [
        ("administrator-overrides-zero", True, None),
        ("administrator-overrides-channel-deny", True, None),
        ("missing-use-application-commands", False, "use-application-commands"),
    ],
)
def test_guild_permission_gate(case: str, allowed: bool, reason: str | None) -> None:
    world = make_world()
    command = make_command(
        default_member_permissions="0" if case != "missing-use-application-commands" else None
    )
    if case == "administrator-overrides-zero":
        admin = world.backend.create_role(
            world.guild_id, "admin", permissions=discord.Permissions(administrator=True).value
        )
        world.backend.add_member(world.guild_id, world.user_id, roles=[admin.id])
    elif case == "administrator-overrides-channel-deny":
        admin = world.backend.create_role(
            world.guild_id, "admin", permissions=discord.Permissions(administrator=True).value
        )
        world.backend.add_member(world.guild_id, world.user_id, roles=[admin.id])
        set_command_overrides(world, command, [override(world.channel_id, 3, False)])
    else:
        world.backend.get_guild(world.guild_id).everyone_role.permissions = 0

    assert result(world, command) == Access(allowed, reason)


@pytest.mark.parametrize(
    ("case", "allowed", "reason"),
    [
        ("command-specific-deny", False, "channel-denied"),
        ("command-all-channels-deny", False, "channel-denied"),
        ("application-specific-deny", False, "channel-denied"),
        ("application-all-channels-deny", False, "channel-denied"),
        ("command-channel-allow-beats-application-all-deny", True, None),
    ],
)
def test_channel_overrides(case: str, allowed: bool, reason: str | None) -> None:
    world = make_world()
    command = make_command()
    all_channels = world.guild_id - 1
    if case == "command-specific-deny":
        set_command_overrides(world, command, [override(world.channel_id, 3, False)])
    elif case == "command-all-channels-deny":
        set_command_overrides(world, command, [override(all_channels, 3, False)])
    elif case == "application-specific-deny":
        set_application_overrides(world, [override(world.channel_id, 3, False)])
    elif case == "application-all-channels-deny":
        set_application_overrides(world, [override(all_channels, 3, False)])
    else:
        set_command_overrides(world, command, [override(world.channel_id, 3, True)])
        set_application_overrides(world, [override(all_channels, 3, False)])

    assert result(world, command) == Access(allowed, reason)


@pytest.mark.parametrize(
    ("case", "allowed", "reason"),
    [
        ("command-user-allow-bypasses-default", True, None),
        ("command-user-allow-over-zero", True, None),
        ("command-user-deny", False, "override-denied"),
        ("command-role-allow-beats-role-deny", True, None),
        ("command-everyone-deny", False, "override-denied"),
        ("application-user-allow-keeps-default", False, "default-member-permissions"),
        ("application-role-allow-keeps-default", False, "default-member-permissions"),
        ("application-user-deny", False, "override-denied"),
        ("default-member-permissions-zero", False, "default-member-permissions"),
        ("required-bits-satisfied", True, None),
        ("required-bits-unsatisfied", False, "default-member-permissions"),
    ],
)
def test_member_overrides_and_default_permissions(case: str, allowed: bool, reason: str | None) -> None:
    world = make_world()
    manage_channels = discord.Permissions.manage_channels.flag
    command = make_command(
        default_member_permissions="0"
        if case in {"default-member-permissions-zero", "command-user-allow-over-zero"}
        else None
    )
    if case in {"command-user-allow-bypasses-default", "command-user-allow-over-zero"}:
        if case == "command-user-allow-bypasses-default":
            command["default_member_permissions"] = str(manage_channels)
        set_command_overrides(world, command, [override(world.user_id, 2, True)])
    elif case == "command-user-deny":
        set_command_overrides(world, command, [override(world.user_id, 2, False)])
    elif case == "command-role-allow-beats-role-deny":
        command["default_member_permissions"] = str(manage_channels)
        denied = world.backend.create_role(world.guild_id, "denied")
        allowed_role = world.backend.create_role(world.guild_id, "allowed")
        world.backend.add_member(world.guild_id, world.user_id, roles=[denied.id, allowed_role.id])
        set_command_overrides(
            world,
            command,
            [override(denied.id, 1, False), override(allowed_role.id, 1, True)],
        )
    elif case == "command-everyone-deny":
        set_command_overrides(world, command, [override(world.guild_id, 1, False)])
    elif case == "application-user-allow-keeps-default":
        command["default_member_permissions"] = str(manage_channels)
        set_application_overrides(world, [override(world.user_id, 2, True)])
    elif case == "application-role-allow-keeps-default":
        command["default_member_permissions"] = str(manage_channels)
        role = world.backend.create_role(world.guild_id, "student-leads")
        world.backend.add_member(world.guild_id, world.user_id, roles=[role.id])
        set_application_overrides(world, [override(role.id, 1, True)])
    elif case == "application-user-deny":
        set_application_overrides(world, [override(world.user_id, 2, False)])
    elif case == "required-bits-satisfied":
        role = world.backend.create_role(world.guild_id, "manager", permissions=manage_channels)
        world.backend.add_member(world.guild_id, world.user_id, roles=[role.id])
        command["default_member_permissions"] = str(manage_channels)
    elif case == "required-bits-unsatisfied":
        command["default_member_permissions"] = str(manage_channels)

    assert result(world, command) == Access(allowed, reason)


def test_visible_commands_guild_global_union_and_sort_order() -> None:
    world = make_world()
    backend = world.backend
    backend.register_commands(
        None,
        [
            {"name": "zeta"},
            {"name": "shared"},
            {"name": "alpha"},
            {"name": "other-type", "type": AppCommandType.USER},
        ],
    )
    backend.register_commands(world.guild_id, [{"name": "shared"}, {"name": "beta"}])

    visible = backend.visible_commands(user_id=world.user_id, channel_id=world.channel_id)
    assert [(command["name"], int(command["id"])) for command in visible] == sorted(
        (command["name"], int(command["id"]))
        for commands in backend.commands.values()
        for command in commands.values()
        if command["type"] == AppCommandType.CHAT_INPUT
    )
    assert [command["name"] for command in visible] == ["alpha", "beta", "shared", "shared", "zeta"]
    dm_visible = backend.visible_commands(user_id=world.user_id, channel_id=world.dm_channel_id)
    assert [command["name"] for command in dm_visible] == ["alpha", "shared", "zeta"]
