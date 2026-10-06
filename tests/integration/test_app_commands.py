import discord
import pytest
from discord import app_commands

import simcord
from simcord import OptionError
from simcord.enums import AppCommandType, MessageType


async def test_bulk_sync_preserves_existing_command_ids_and_permissions(env):
    tree = env.bot.tree
    await tree.sync()
    initial = await tree.fetch_commands()
    assert len(initial) > 1
    retained = initial[0]
    removed = initial[1]
    old_ids = {command.name: command.id for command in initial}

    role = env.guild.create_role("Command moderators")
    env.guild.set_command_permissions(retained, {role: True})
    permission_key = (env.guild.id, retained.id)
    seeded_permissions = env.backend.command_permissions[permission_key]

    tree.remove_command(removed.name)

    async def added_callback(interaction: discord.Interaction) -> None:
        return None

    tree.add_command(
        app_commands.Command(
            name="simcord-added-stable-id-test",
            description="A command added after the initial sync",
            callback=added_callback,
        )
    )
    await tree.sync()
    synced = await tree.fetch_commands()
    synced_by_name = {command.name: command for command in synced}

    assert removed.name not in synced_by_name
    assert synced_by_name["simcord-added-stable-id-test"].id not in old_ids.values()
    for command in initial:
        if command.name != removed.name:
            assert synced_by_name[command.name].id == old_ids[command.name]
    assert env.backend.command_permissions[permission_key] == seeded_permissions


async def test_subcommand_group(env, channel, alice):
    result = await alice.slash(channel, "config set", key="lang", value="en")
    assert result.response.content == "lang=en"
    stored = env.backend.get_message(channel.id, result.response.id)
    assert stored.type == MessageType.CHAT_INPUT_COMMAND
    assert stored.interaction_metadata["command_type"] == AppCommandType.CHAT_INPUT


async def test_unknown_subcommand_is_caught(env, channel, alice):
    with pytest.raises(simcord.SetupError, match="no subcommand path"):
        await alice.slash(channel, "config unset", key="lang")


async def test_user_context_menu(env, channel, alice):
    # The menu name contains a space; it must resolve by full name, not be split
    # into a "Report" command with a "Member" subcommand.
    bob = env.guild.add_member(env.create_user("bob"))
    result = await alice.context_menu(channel, "Report Member", bob)
    assert result.ephemeral
    assert result.response.content == "Reported bob"

    stored = env.backend.get_message(channel.id, result.response.id)
    assert stored.type == MessageType.CONTEXT_MENU_COMMAND
    assert stored.interaction_metadata["target_id"] == bob.id
    assert stored.interaction_metadata["target_type"] == AppCommandType.USER


async def test_autocomplete(env, channel, alice):
    choices = await alice.autocomplete(channel, "tag", "name", "py")
    assert [c["value"] for c in choices] == ["python", "pytest"]

    result = await alice.slash(channel, "tag", name="pytest")
    assert result.response.content == "Tag: pytest"


async def test_autocomplete_choices_default_to_none(env, channel, alice):
    # A non-autocomplete result reports no choices rather than raising
    # AttributeError — actors read the field unconditionally.
    result = await alice.slash(channel, "tag", name="pytest")
    assert result.autocomplete_choices is None


async def test_modal_flow(env, channel, alice):
    shown = await alice.slash(channel, "feedback")
    assert shown.modal is not None

    submitted = await alice.submit_modal(shown, {"name": "Alice"})
    assert submitted.response.content == "Thanks Alice"


async def test_select_menu(env, channel, alice):
    result = await alice.slash(channel, "color")

    picked = await alice.select(result.response.message, ["green"], custom_id="color")
    assert picked.response.content == "Picked green"


async def test_select_invalid_value_rejected(env, channel, alice):
    result = await alice.slash(channel, "color")
    with pytest.raises(simcord.SetupError, match="does not exist"):
        await alice.select(result.response.message, ["purple"], custom_id="color")


async def test_attachment_option_is_resolved_and_fetchable(channel, alice):
    result = await alice.slash(channel, "upload", attachment=("greeting.txt", b"hello from upload"))
    assert result.response.content == "greeting.txt:hello from upload"


async def test_slash_numeric_range_violation_has_code(channel, alice):
    with pytest.raises(OptionError) as exc:
        await alice.slash(channel, "option-check", limit=0, label="okay")
    assert exc.value.code == "option-range"
    assert exc.value.option == "limit"


async def test_slash_string_length_violation_has_code(channel, alice):
    with pytest.raises(OptionError) as exc:
        await alice.slash(channel, "option-check", limit=1, label="x")
    assert exc.value.code == "option-length"
    assert exc.value.option == "label"
