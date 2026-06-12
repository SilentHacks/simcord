import discord
import pytest
from discord.ext import commands

import simcord


async def test_slash_command_with_permissions(env, channel):
    mod_role = env.guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
    mod = env.guild.add_member(env.create_user("mod"), roles=[mod_role])
    target = env.guild.add_member(env.create_user("spammer"))

    result = await mod.slash(channel, "ban", user=target, reason="spam")

    assert result.acknowledged
    assert result.ephemeral
    assert result.response.content == f"Banned {target.mention}: spam"
    assert env.guild.get_ban(target) is not None
    assert target.id not in env.guild.member_ids()


async def test_slash_command_permission_denied_branch(env, channel, alice):
    target = env.guild.add_member(env.create_user("victim"))

    result = await alice.slash(channel, "ban", user=target)

    assert result.response.content == "You can't do that."
    assert env.guild.get_ban(target) is None


async def test_deferred_response_and_followup(env, channel, alice):
    result = await alice.slash(channel, "slow")
    assert result.followups[0].content == "Done after a while"


async def test_settle_waits_through_async_sleep(env, channel, alice):
    # A handler that pauses (asyncio.sleep) before replying must still be fully
    # settled: assertions should see the followup, not stale pre-sleep state.
    result = await alice.slash(channel, "paced")
    assert result.followups[0].content == "paced reply"


async def test_deferred_component_update_edits_in_place(env, channel, alice):
    result = await alice.slash(channel, "defer-edit")
    original = result.response
    assert original.content == "click me"

    clicked = await alice.click(original, custom_id="slow_edit")

    # The clicked message is edited in place — not replaced by a new message.
    assert clicked.response is not None
    assert clicked.response.id == original.id
    assert clicked.response.content == "edited in place"
    history = channel.history()
    assert len(history) == 1
    assert history[0].content == "edited in place"


async def test_ephemeral_hidden_from_other_users(env, channel):
    mod_role = env.guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
    mod = env.guild.add_member(env.create_user("mod"), roles=[mod_role])
    target = env.guild.add_member(env.create_user("spammer"))
    bystander = env.guild.add_member(env.create_user("bystander"))

    await mod.slash(channel, "ban", user=target)

    # Only the invoking moderator sees the ephemeral response.
    assert len(channel.history(viewer=mod)) == len(channel.history(viewer=bystander)) + 1


async def test_button_click(env, channel, alice):
    result = await alice.slash(channel, "delete-data")
    assert result.response.content == "Are you sure?"

    click = await alice.click(result.response.message, label="Confirm")

    assert click.acknowledged
    assert result.response.content == "Deleted all data."
    assert result.response.components == []


async def test_cannot_click_missing_button(env, channel, alice):
    result = await alice.slash(channel, "delete-data")
    with pytest.raises(simcord.SetupError, match="could not interact"):
        await alice.click(result.response.message, label="Nope")


async def test_unsynced_command_is_caught():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

    @bot.tree.command(description="never synced")
    async def ghost(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("boo")

    async with simcord.run(bot) as env:  # no sync in setup_hook
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        with pytest.raises(simcord.SetupError, match="never synced"):
            await alice.slash(channel, "ghost")


async def test_double_acknowledge_raises_real_error(env, channel, alice):
    @env.bot.tree.command(description="buggy")
    async def buggy(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("one")
        await interaction.response.send_message("two")

    await env.bot.tree.sync()
    result = await alice.slash(channel, "buggy")
    assert result.response.content == "one"
    assert env.errors and isinstance(env.errors[-1].__cause__ or env.errors[-1], Exception)


async def test_strict_sync_opt_out():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

    @bot.tree.command(description="never synced")
    async def ghost(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("boo")

    async with simcord.run(bot, strict_sync=False) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        result = await alice.slash(channel, "ghost")
        assert result.response.content == "boo"


async def test_editing_deferred_response_materialises_message(env, channel, alice):
    @env.bot.tree.command(description="defers then edits")
    async def patient(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="All done")

    await env.bot.tree.sync()
    result = await alice.slash(channel, "patient")
    assert result.response.content == "All done"
    assert result.ephemeral
