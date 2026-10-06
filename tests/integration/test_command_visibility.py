import asyncio
import copy

import discord
import pytest
from discord import app_commands
from discord.ext import commands

import simcord
from simcord.backend.models import Overwrite
from simcord.enums import AppCommandType
from simcord.http import router
from simcord.interactions import command_leaves


def _seed_denial(env, command, channel, user, reason):
    command_id = int(command["id"])
    env.backend.command_permissions.pop((env.guild.id, command_id), None)
    command.pop("guild_id", None)
    command.pop("contexts", None)
    command["default_member_permissions"] = None

    if reason == "scope":
        other_guild = env.create_guild()
        other_channel = other_guild.create_text_channel("elsewhere")
        command["guild_id"] = str(env.guild.id)
        return other_channel
    if reason == "context":
        command["contexts"] = [1]
    elif reason == "use-application-commands":
        channel._env.backend.set_overwrite(
            channel.id,
            Overwrite(
                target_id=env.guild.id,
                type=0,
                allow=0,
                deny=discord.Permissions(use_application_commands=True).value,
            ),
        )
    elif reason == "channel-denied":
        env.guild.set_command_permissions(int(command["id"]), {channel: False})
    elif reason == "override-denied":
        env.guild.set_command_permissions(int(command["id"]), {user: False})
    elif reason == "default-member-permissions":
        command["default_member_permissions"] = str(discord.Permissions(manage_guild=True).value)
    return channel


@pytest.mark.parametrize(
    "reason",
    [
        "scope",
        "context",
        "use-application-commands",
        "channel-denied",
        "override-denied",
        "default-member-permissions",
    ],
)
async def test_slash_and_autocomplete_refuse_invisible_commands(env, channel, alice, reason):
    command = env.backend.find_command("tag", None)
    assert command is not None
    invoke_channel = _seed_denial(env, command, channel, alice, reason)

    with pytest.raises(simcord.SetupError, match=f"reason: {reason}"):
        await alice.slash(invoke_channel, "tag", name="python")
    with pytest.raises(simcord.SetupError, match=f"reason: {reason}"):
        await alice.autocomplete(invoke_channel, "tag", "name", "py")


@pytest.mark.parametrize(
    "reason",
    [
        "scope",
        "context",
        "use-application-commands",
        "channel-denied",
        "override-denied",
        "default-member-permissions",
    ],
)
async def test_context_menu_refuses_invisible_commands(env, channel, alice, reason):
    command = env.backend.find_command("Report Member", env.guild.id, type=AppCommandType.USER)
    assert command is not None
    invoke_channel = _seed_denial(env, command, channel, alice, reason)
    target = env.guild.add_member(env.create_user("target"))

    with pytest.raises(simcord.SetupError, match=f"reason: {reason}"):
        await alice.context_menu(invoke_channel, "Report Member", target)


async def test_command_sync_discards_default_field_at_http_boundary(env):
    await env.bot.tree.sync()
    sync_request = next(
        request
        for request in reversed(env.backend.http_requests)
        if request.method == "PUT" and request.path.endswith("/commands")
    )
    assert sync_request.json
    assert all(command.get("nsfw") is False for command in sync_request.json)

    response = router.dispatch(
        env.backend,
        sync_request.method,
        sync_request.path,
        json=sync_request.json,
    )
    assert all("nsfw" not in command for command in response)
    assert all("nsfw" not in command for command in env.backend.commands[None].values())

    fetched = router.dispatch(
        env.backend,
        "GET",
        f"/applications/{env.backend.application_id}/commands",
    )
    assert all("nsfw" not in command for command in fetched)


@pytest.mark.parametrize("scope", ["global", "guild"])
@pytest.mark.parametrize("field_value", [True, 0, None])
async def test_marked_command_batch_rejected_atomically(env, scope, field_value):
    guild_id = None if scope == "global" else env.guild.id
    path = (
        f"/applications/{env.backend.application_id}/commands"
        if guild_id is None
        else f"/applications/{env.backend.application_id}/guilds/{guild_id}/commands"
    )
    env.backend.register_commands(guild_id, [{"name": "preserved"}])
    previous = copy.deepcopy(env.backend.commands[guild_id])

    with pytest.raises(router.UnsupportedField) as exc:
        router.dispatch(
            env.backend,
            "PUT",
            path,
            json=[{"name": "new-before-marked"}, {"name": "marked", "nsfw": field_value}],
        )

    assert exc.value.fields == ["nsfw"]
    assert env.backend.commands[guild_id] == previous


async def test_guild_command_scope_reason_for_slash_and_autocomplete(env, channel, alice):
    scoped = env.backend.register_commands(
        env.guild.id,
        [
            {
                "name": "scoped-probe",
                "type": AppCommandType.CHAT_INPUT,
                "options": [{"name": "query", "type": 3, "autocomplete": True}],
            }
        ],
    )[0]
    other_guild = env.create_guild()
    other_channel = other_guild.create_text_channel("elsewhere")

    with pytest.raises(simcord.SetupError, match="reason: scope"):
        await alice.slash(other_channel, "scoped-probe", query="x")
    with pytest.raises(simcord.SetupError, match="reason: scope"):
        await alice.autocomplete(other_channel, "scoped-probe", "query", "x")
    assert scoped["guild_id"] == str(env.guild.id)


async def test_admin_bypasses_command_defaults_and_overrides(env, channel, alice):
    admin_role = env.guild.create_role("Administrators", permissions=discord.Permissions(administrator=True))
    admin = env.guild.add_member(env.create_user("admin"), roles=[admin_role])
    command = env.backend.find_command("manage_settings", None)
    assert command is not None
    env.guild.set_command_permissions(int(command["id"]), {alice: False})

    result = await admin.slash(channel, "manage_settings")
    assert result.response.content == "Settings updated"


async def test_available_commands_deduplicates_shadowed_names(env, channel, alice):
    global_command = env.backend.register_commands(None, [{"name": "shadowed", "type": 1}])[0]
    guild_command = env.backend.register_commands(env.guild.id, [{"name": "shadowed", "type": 1}])[0]
    assert global_command["id"] != guild_command["id"]
    assert alice.available_commands(channel).count("shadowed") == 1

    user = env.create_user("command-picker")
    assert user.available_commands().count("shadowed") == 1


async def test_available_commands_respects_view_channel_permission(env, channel, alice):
    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    await env.bot.get_channel(channel.id).set_permissions(member, view_channel=False)

    assert alice.available_commands(channel) == ()
    with pytest.raises(simcord.backend.errors.BackendError):
        await alice.slash(channel, "tag", name="python")


async def test_picker_commands_match_successful_slash_visibility(env, channel, alice):
    from fixtures.sample_bot import picker

    picker.picker_action_release.set()
    admin_role = env.guild.create_role("Administrators", permissions=discord.Permissions(administrator=True))
    admin = env.guild.add_member(env.create_user("admin"), roles=[admin_role])
    dm_user = env.create_user("dm-user")
    actors_and_channels = [(alice, channel), (admin, channel), (dm_user, dm_user.dm_channel)]

    for actor, command_channel in actors_and_channels:
        expected = (
            set(actor.available_commands(command_channel))
            if isinstance(actor, simcord.MemberActor)
            else set(actor.available_commands())
        )
        scopes = (command_channel.guild.id, None) if command_channel.guild is not None else (None,)
        roots = [
            root
            for scope in scopes
            for root in env.backend.commands.get(scope, {}).values()
            if root.get("type", AppCommandType.CHAT_INPUT) == AppCommandType.CHAT_INPUT
        ]
        invocation_paths = [" ".join(path) for root in roots for path, _leaf in command_leaves(root)]
        callable_without_visibility_error = set()
        for path in invocation_paths:
            try:
                if isinstance(actor, simcord.MemberActor):
                    await actor.slash(command_channel, path)
                else:
                    await actor.slash(path)
            except simcord.SetupError as error:
                if "is not visible to this user here" in str(error):
                    continue
            callable_without_visibility_error.add(path)
        assert expected == callable_without_visibility_error


async def test_bot_dm_slash_visibility_and_context_payload(env):
    user = env.create_user("dm-user")
    payloads = []
    emit = env.backend.emit

    def capture(event, payload):
        if event == "INTERACTION_CREATE":
            payloads.append(payload)
        emit(event, payload)

    env.backend.emit = capture
    result = await user.slash("dm_greeting")
    assert result.ephemeral
    assert result.response.content == "Hello from the bot"
    assert payloads[-1]["context"] == 1
    assert payloads[-1].get("guild_id") is None

    with pytest.raises(simcord.SetupError, match="reason: context"):
        await user.slash("guild_greeting")


async def test_dm_autocomplete_and_autocomplete_option_validation(env, alice, channel):
    user = env.create_user("dm-user")
    choices = await user.autocomplete("tag", "name", "py")
    assert [choice["value"] for choice in choices] == ["python", "pytest"]

    with pytest.raises(simcord.SetupError, match="does not enable autocomplete"):
        await alice.autocomplete(channel, "option-check", "label", "x", limit=1)


async def test_unknown_autocomplete_option_has_option_error(env, channel, alice):
    from simcord.interactions import OptionError

    with pytest.raises(OptionError) as exc:
        await alice.autocomplete(channel, "tag", "missing", "py")
    assert exc.value.code == "option-unknown"


async def test_picker_option_types_choices_and_constraints(env, channel, alice):
    target = env.guild.add_member(env.create_user("target"))
    role = env.guild.create_role("Helpers")
    result = await alice.slash(
        channel,
        "picker_options",
        text="hello",
        count=3,
        ratio=0.5,
        enabled=True,
        user=target,
        channel=channel,
        role=role,
        mentionable=target,
        color="red",
    )
    assert result.response.content == "hello:3:0.5:True:target:general:Helpers:target:red"


async def test_real_discord_py_command_metadata_matches_visibility(env):
    commands_by_name = {
        command["name"]: command
        for scope in (None, env.guild.id)
        for command in env.backend.commands.get(scope, {}).values()
    }
    assert commands_by_name["manage_settings"]["default_member_permissions"] == (
        discord.Permissions(manage_guild=True).value
    )
    assert 0 in commands_by_name["dm_greeting"]["contexts"]
    assert 1 in commands_by_name["dm_greeting"]["contexts"]
    assert commands_by_name["guild_greeting"]["dm_permission"] is False
    assert 1 in commands_by_name["dm_whisper"]["contexts"]
    assert 0 not in commands_by_name["dm_whisper"]["contexts"]


async def test_strict_sync_false_fallback_still_checks_visibility():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

    @app_commands.command(description="unsynced but visible in guilds")
    @app_commands.allowed_contexts(guilds=True, dms=False)
    async def fallback(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("fallback")

    bot.tree.add_command(fallback)
    async with simcord.run(bot, strict_sync=False) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        member = guild.add_member(env.create_user("member"))
        user = env.create_user("dm-user")

        result = await member.slash(channel, "fallback")
        assert result.response.content == "fallback"
        with pytest.raises(simcord.SetupError, match="reason: context"):
            await user.slash("fallback")


async def test_dm_slash_rejects_overlap_with_running_operation(env):
    user = env.create_user("dm-user")
    release = asyncio.Event()

    async def hold(message: discord.Message) -> None:
        if message.content == "hold":
            await release.wait()

    env.bot.add_listener(hold, "on_message")
    first = asyncio.create_task(user.send_dm("hold"))
    await asyncio.sleep(0)
    with pytest.raises(simcord.SetupError):
        await user.slash("dm_greeting")
    release.set()
    await first
    env.bot.remove_listener(hold, "on_message")
