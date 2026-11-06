"""Command REST storage and shared sync validation fidelity."""

import discord
import pytest
from discord import app_commands

from simcord.backend.errors import BackendError
from simcord.http.router import dispatch


async def test_guild_command_lifecycle_preserves_identity(env):
    scope = f"/applications/{env.backend.application_id}/guilds/{env.guild.id}/commands"
    payload = {"name": "fidelity-command", "description": "original"}
    created = dispatch(env.backend, "POST", scope, json=payload)
    command_id = created["id"]
    assert created["application_id"] == str(env.backend.application_id)
    assert created["guild_id"] == str(env.guild.id)
    assert created == {
        **payload,
        "id": command_id,
        "version": created["version"],
        "application_id": str(env.backend.application_id),
        "guild_id": str(env.guild.id),
        "type": 1,
        "default_member_permissions": None,
        "nsfw": False,
        "name_localizations": None,
        "description_localizations": None,
    }
    assert created["version"] != command_id
    assert dispatch(env.backend, "POST", scope, json=payload)["version"] == created["version"]
    assert dispatch(env.backend, "GET", f"{scope}/{command_id}") == created
    edited = dispatch(env.backend, "PATCH", f"{scope}/{command_id}", json={"description": "edited"})
    assert edited["id"] == command_id and edited["description"] == "edited"
    assert edited["version"] != created["version"]
    assert dispatch(env.backend, "PATCH", f"{scope}/{command_id}", json={"nsfw": True})["nsfw"] is True
    assert dispatch(env.backend, "PATCH", f"{scope}/{command_id}", json={"nsfw": False})["nsfw"] is False
    upserted = dispatch(env.backend, "POST", scope, json=payload)
    assert upserted["id"] == command_id
    fetched = await env.bot.tree.fetch_command(int(command_id), guild=discord.Object(env.guild.id))
    assert fetched.name == "fidelity-command"
    await fetched.delete()
    assert not any(command["id"] == command_id for command in dispatch(env.backend, "GET", scope))
    with pytest.raises(discord.NotFound) as caught:
        await env.bot.tree.fetch_command(int(command_id), guild=discord.Object(env.guild.id))
    assert caught.value.code == 10063


async def test_bulk_command_validation_is_atomic_after_sdk_sync(env):
    guild = discord.Object(env.guild.id)

    async def callback(interaction: discord.Interaction):
        pass

    command = app_commands.Command(name="valid-first", description="valid", callback=callback)
    env.bot.tree.add_command(command, guild=guild)
    await env.bot.tree.sync(guild=guild)
    before = dict(env.backend.commands[env.guild.id])
    scope = f"/applications/{env.backend.application_id}/guilds/{env.guild.id}/commands"
    options = [{"type": 3, "name": f"option-{index}", "description": "option"} for index in range(26)]
    invalid = {"name": "invalid-second", "description": "invalid", "type": 1, "options": options}
    with pytest.raises(BackendError) as caught:
        dispatch(
            env.backend,
            "PUT",
            scope,
            json=[
                {"name": "valid-first", "description": "valid", "type": 1},
                invalid,
            ],
        )
    assert caught.value.code == 50035
    assert caught.value.errors["1"]["options"]["_errors"][0]["code"] == "BASE_TYPE_MAX_LENGTH"
    assert env.backend.commands[env.guild.id] == before


@pytest.mark.parametrize("invalid", ["count", "order"])
async def test_command_option_validation_matches_live_error_tree(env, invalid):
    scope = f"/applications/{env.backend.application_id}/guilds/{env.guild.id}/commands"
    options = [{"type": 3, "name": f"opt{i}", "description": "option"} for i in range(25)]
    valid = dispatch(
        env.backend,
        "POST",
        scope,
        json={"name": "option-boundary", "description": "command", "options": options},
    )
    assert len(valid["options"]) == 25
    assert valid["options"][0] == {
        **options[0],
        "name_localizations": None,
        "description_localizations": None,
    }
    if invalid == "count":
        options.append({"type": 3, "name": "over", "description": "option"})
        expected = {
            "_errors": [{"code": "BASE_TYPE_MAX_LENGTH", "message": "Must be 25 or fewer in length."}]
        }
    else:
        options = [{**options[0], "required": False}, {**options[1], "required": True}]
        expected = {
            "1": {
                "_errors": [
                    {
                        "code": "APPLICATION_COMMAND_OPTIONS_REQUIRED_INVALID",
                        "message": "Required options must be placed before non-required options",
                    }
                ]
            }
        }
    with pytest.raises(BackendError) as caught:
        dispatch(
            env.backend,
            "POST",
            scope,
            json={"name": "option-boundary", "description": "command", "options": options},
        )
    assert caught.value.status == 400 and caught.value.code == 50035
    assert caught.value.errors == {"options": expected}
    assert dispatch(env.backend, "GET", f"{scope}/{valid['id']}") == valid
    ordered = [{**options[1], "required": True}, {**options[0], "required": False}]
    stored = dispatch(env.backend, "PATCH", f"{scope}/{valid['id']}", json={"options": ordered})
    assert stored["options"][0]["required"] is True
    assert "required" not in stored["options"][1]
