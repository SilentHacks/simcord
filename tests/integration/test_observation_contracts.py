"""Unconditional 3.0 error-prefix and detached-payload observation contracts."""

import asyncio
from io import BytesIO

import discord
import pytest
from discord.ext import commands

import simcord
from simcord.backend.models import Interaction
from simcord.results import InteractionResult


def _error_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    errors = {name: RuntimeError(name) for name in ("A", "B", "shutdown")}
    tasks = []

    @bot.command()
    async def broken(ctx, name: str):
        raise errors[name]

    @bot.command()
    async def park(ctx):
        async def pending():
            try:
                await bot.wait_for("never")
            finally:
                raise errors["shutdown"]

        tasks.append(asyncio.create_task(pending()))

    return bot, errors


def _actors(env):
    channel = env.create_guild().create_text_channel("general")
    alice = env.guild.add_member(env.create_user("alice"))
    return channel, alice


@pytest.mark.parametrize("source", ["body", "shutdown"])
async def test_acknowledges_only_current_prefix(source):
    bot, errors = _error_bot()
    with pytest.raises(ExceptionGroup) as raised:
        async with simcord.run(bot) as env:
            channel, alice = _actors(env)
            await alice.send(channel, "!broken A")
            saved = env.errors
            expected = saved[0]
            traceback = expected.original.__traceback__
            assert expected.original is errors["A"]
            assert traceback is not None
            saved.clear()
            assert env.errors == [expected]
            assert env.errors[0] is expected
            assert env.errors[0].original.__traceback__ is traceback
            if source == "body":
                await alice.send(channel, "!broken B")
            else:
                await alice.send(channel, "!park")
            # Non-consuming diagnostics must not acknowledge the new error.
            assert env.errors_since(0)[0] is expected
    assert len(raised.value.exceptions) == 1
    unexpected = raised.value.exceptions[0]
    if source == "body":
        assert unexpected.original is errors["B"]
    else:
        assert unexpected is errors["shutdown"]
    assert saved == []


async def test_saved_error_snapshot_stays_fixed():
    bot, errors = _error_bot()
    async with simcord.run(bot) as env:
        channel, alice = _actors(env)
        saved = env.errors
        await alice.send(channel, "!broken A")
        assert saved == []
        assert env.errors[0].original is errors["A"]


async def test_raise_errors_includes_acknowledged_history():
    bot, errors = _error_bot()
    with pytest.raises(ExceptionGroup) as teardown:
        async with simcord.run(bot) as env:
            channel, alice = _actors(env)
            await alice.send(channel, "!broken A")
            expected = env.errors[0]
            with pytest.raises(ExceptionGroup) as explicit:
                env.raise_errors()
            assert explicit.value.exceptions == (expected,)
            await alice.send(channel, "!broken B")
            with pytest.raises(ExceptionGroup) as full_history:
                env.raise_errors()
            assert full_history.value.exceptions[0] is expected
            assert [error.original for error in full_history.value.exceptions] == [
                errors["A"],
                errors["B"],
            ]
            await alice.send(channel, "!park")
    assert len(teardown.value.exceptions) == 1
    assert teardown.value.exceptions[0] is errors["shutdown"]


async def test_empty_raise_errors_does_not_acknowledge_future_errors():
    bot, errors = _error_bot()
    with pytest.raises(ExceptionGroup) as raised:
        async with simcord.run(bot) as env:
            channel, alice = _actors(env)
            env.raise_errors()
            await alice.send(channel, "!broken A")
    assert raised.value.exceptions[0].original is errors["A"]


async def test_preview_error_cursors_are_nonconsuming():
    bot, errors = _error_bot()
    with pytest.raises(ExceptionGroup) as raised:
        async with simcord.run(bot) as env:
            channel, alice = _actors(env)
            cursor = env.error_cursor
            await alice.send(channel, "!broken A")
            observed = env.errors_since(cursor)
            assert observed[0].original is errors["A"]
            assert env.error_cursor == cursor + 1
            assert env.errors_since(cursor) == observed
    assert raised.value.exceptions == observed


async def test_check_errors_opt_out():
    bot, _ = _error_bot()
    async with simcord.run(bot, check_errors=False) as env:
        channel, alice = _actors(env)
        await alice.send(channel, "!broken A")


async def test_does_not_mask_test_body_exception():
    bot, _ = _error_bot()
    body_error = ValueError("test body")
    with pytest.raises(ValueError) as raised:
        async with simcord.run(bot) as env:
            channel, alice = _actors(env)
            await alice.send(channel, "!broken A")
            await alice.send(channel, "!park")
            raise body_error
    assert raised.value is body_error


def _payload_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    interactions = []

    @bot.tree.command(name="payload")
    async def payload(interaction: discord.Interaction):
        interactions.append(interaction)
        embed = discord.Embed(title="initial")
        embed.add_field(name="field", value="initial")
        view = discord.ui.View()
        view.add_item(
            discord.ui.Select(custom_id="pick", options=[discord.SelectOption(label="Initial", value="one")])
        )
        await interaction.response.send_message(
            "initial", embed=embed, view=view, file=discord.File(BytesIO(b"payload"), filename="data.txt")
        )

    return bot, interactions


@pytest.mark.parametrize("property_name", ["components", "embeds", "message"])
async def test_nested_observations_are_detached(property_name):
    bot, _ = _payload_bot()
    async with simcord.run(bot, strict_sync=False) as env:
        channel, alice = _actors(env)
        result = await alice.slash(channel, "payload")
        response = result.response
        observed = getattr(response, property_name)
        stored = env.backend.get_message(channel.id, response.id)
        if property_name == "components":
            observed[0]["components"][0]["options"][0]["label"] = "tampered"
            actual = stored.components[0]["components"][0]["options"][0]["label"]
            assert actual == "Initial"
        else:
            embed = observed[0] if property_name == "embeds" else observed.embeds[0]
            embed.to_dict()["fields"][0]["value"] = "tampered"
            assert stored.embeds[0]["fields"][0]["value"] == "initial"


async def test_result_handles_remain_live_with_fixed_payload_snapshots():
    bot, interactions = _payload_bot()
    async with simcord.run(bot, strict_sync=False) as env:
        channel, alice = _actors(env)
        result = await alice.slash(channel, "payload")
        response = result.response
        components = response.components
        embeds = response.embeds
        message = response.message
        attachments = response.attachments
        followups = result.followups
        assert attachments[0].filename == "data.txt"
        attachments[0].filename = "tampered.txt"
        assert response.attachments[0].filename == "data.txt"
        changed = discord.Embed(title="changed")
        changed.add_field(name="field", value="changed")
        await interactions[0].edit_original_response(content="changed", embed=changed, view=None)
        followup = await interactions[0].followup.send("later", embed=changed, wait=True)
        assert response.content == "changed"
        assert response.components == []
        assert response.embeds[0].title == "changed"
        assert response.message.content == "changed"
        assert components[0]["components"][0]["options"][0]["label"] == "Initial"
        assert embeds[0].to_dict()["fields"][0]["value"] == "initial"
        assert message.content == "initial"
        assert followups == []
        assert result.followups[0].content == "later"
        handle = result.followups[0]
        saved = handle.embeds
        await followup.edit(content="edited", embed=discord.Embed(title="edited"))
        assert handle.content == "edited"
        assert handle.embeds[0].title == "edited"
        assert saved[0].title == "changed"
        await followup.delete()
        assert result.followups == []


async def test_modal_submission_ignores_snapshot_edits():
    from fixtures.sample_bot import create_bot

    async with simcord.run(create_bot()) as env:
        channel, alice = _actors(env)
        shown = await alice.slash(channel, "feedback")
        modal = shown.modal
        modal["custom_id"] = "tampered"
        modal["components"][0]["components"][0]["custom_id"] = "tampered"
        assert shown.modal["custom_id"] != "tampered"
        submitted = await alice.submit_modal(shown, {"name": "Alice"})
        assert submitted.response.content == "Thanks Alice"
        assert modal["components"][0]["components"][0]["custom_id"] == "tampered"
        choices = await alice.autocomplete(channel, "tag", "name", "py")
        choices[0]["name"] = "tampered"
        stored = next(reversed(env.backend.interactions.values()))
        assert stored.autocomplete_choices[0]["name"] == "python"


async def test_modal_and_autocomplete_detach_nested_payloads():
    bot, _ = _error_bot()
    env = simcord.Env(bot)
    interaction = Interaction(id=1, token="token", type=2, channel_id=1, guild_id=None, user_id=1)
    result = InteractionResult(env, interaction)
    assert result.modal is None
    assert result.autocomplete_choices is None
    interaction.show_modal({"custom_id": "modal", "components": [{"nested": ["original"]}]})
    interaction.complete_autocomplete([{"name": "choice", "name_localizations": {"de": "Original"}}])
    modal, choices = result.modal, result.autocomplete_choices
    modal["components"][0]["nested"][0] = "tampered"
    choices[0]["name_localizations"]["de"] = "tampered"
    assert interaction.modal["components"][0]["nested"][0] == "original"
    assert interaction.autocomplete_choices[0]["name_localizations"]["de"] == "Original"
    interaction.modal["components"][0]["nested"].append("later")
    interaction.complete_autocomplete([])
    assert result.autocomplete_choices == []
    assert result.modal["components"][0]["nested"][-1] == "later"
    assert modal["components"][0]["nested"] == ["tampered"]
    assert choices != []
