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
    assert result.response.content == "Done after a while"
    assert result.followups == []


async def test_settle_waits_through_async_sleep(env, channel, alice):
    # A handler that pauses (asyncio.sleep) before replying must still be fully
    # settled: assertions should see the completed original, not stale pre-sleep state.
    result = await alice.slash(channel, "paced")
    assert result.response.content == "paced reply"
    assert result.followups == []


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


async def test_user_select_resolves_members(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    result = await alice.slash(channel, "assign")

    picked = await alice.select(result.response.message, [alice, bob], custom_id="who")

    assert picked.response.content == "Picked alice, bob"


async def test_role_select_resolves_role(env, channel, alice):
    role = env.guild.create_role("Helper")
    result = await alice.slash(channel, "assign")

    picked = await alice.select(result.response.message, [role], custom_id="role")

    assert picked.response.content == "Role Helper"


async def test_channel_select_resolves_channel(env, channel, alice):
    other = env.guild.create_text_channel("other")
    result = await alice.slash(channel, "assign")

    picked = await alice.select(result.response.message, [other], custom_id="chan")

    assert picked.response.content == "Channel other"


async def test_select_wrong_handle_type_rejected(env, channel, alice):
    role = env.guild.create_role("Helper")
    result = await alice.slash(channel, "assign")

    with pytest.raises(simcord.SetupError, match="expects"):
        await alice.select(result.response.message, [role], custom_id="who")


async def test_entity_select_respects_max_values(env, channel, alice):
    extras = [env.guild.add_member(env.create_user(f"u{i}")) for i in range(3)]
    result = await alice.slash(channel, "assign")

    with pytest.raises(simcord.SetupError, match="between 1 and 2"):
        await alice.select(result.response.message, [alice, *extras], custom_id="who")


async def test_slash_wrong_handle_type_rejected(env, channel, alice):
    role = env.guild.create_role("Helper")

    # `ban`'s `user` option is a USER; a role handle can't fill it. The mismatch
    # is caught at the call site, not deep inside discord.py's resolution.
    with pytest.raises(simcord.SetupError, match="expects"):
        await alice.slash(channel, "ban", user=role)


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


async def test_editing_deferred_response_completes_message(env, channel, alice):
    @env.bot.tree.command(description="defers then edits")
    async def patient(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="All done")

    await env.bot.tree.sync()
    result = await alice.slash(channel, "patient")
    assert result.response.content == "All done"
    assert result.ephemeral


async def test_opened_modal_is_bound_to_opener_and_guild_and_dispatches_once(env, channel, alice):
    submissions = []

    class Form(discord.ui.Modal, title="Identity"):
        answer = discord.ui.TextInput(custom_id="answer")

        async def on_submit(self, interaction: discord.Interaction) -> None:
            submissions.append((interaction.user.id, interaction.guild_id, self.answer.value))
            await interaction.response.send_message("accepted")

    @env.bot.tree.command(description="Open an identity-bound modal")
    async def identity_form(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(Form())

    await env.bot.tree.sync()
    shown = await alice.slash(channel, "identity_form")
    bob = env.guild.add_member(env.create_user("bob"))
    elsewhere = env.create_guild("Other").add_member(alice.user)
    before = len(env.backend.interactions)

    with pytest.raises(simcord.SetupError):
        await bob.submit_modal(shown, {"answer": "stolen"})
    with pytest.raises(simcord.SetupError):
        await elsewhere.submit_modal(shown, {"answer": "wrong context"})
    assert submissions == []
    assert len(env.backend.interactions) == before

    accepted = await alice.submit_modal(shown, {"answer": "original"})
    assert accepted.response.content == "accepted"
    assert submissions == [(alice.id, env.guild.id, "original")]

    with pytest.raises(simcord.SetupError):
        await alice.submit_modal(shown, {"answer": "replayed"})
    assert submissions == [(alice.id, env.guild.id, "original")]


async def test_component_modal_rechecks_channel_access_without_consuming_on_denial(env, channel, alice):
    submissions = []

    class Form(discord.ui.Modal, title="Channel access"):
        answer = discord.ui.TextInput(custom_id="answer")

        async def on_submit(self, interaction: discord.Interaction) -> None:
            submissions.append(self.answer.value)
            await interaction.response.edit_message(content=self.answer.value, view=None)

    async def open_form(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(Form())

    view = discord.ui.View()
    button = discord.ui.Button(label="Open", custom_id="access-form")
    button.callback = open_form
    view.add_item(button)
    native_channel = env.bot.get_channel(channel.id)
    source = await native_channel.send("original", view=view)
    shown = await alice.click(source, custom_id="access-form")
    before = len(env.backend.interactions)

    await native_channel.set_permissions(
        alice.member, overwrite=discord.PermissionOverwrite(view_channel=False)
    )
    with pytest.raises(simcord.SetupError):
        await alice.submit_modal(shown, {"answer": "denied"})
    assert submissions == []
    assert len(env.backend.interactions) == before
    assert env.backend.get_message(channel.id, source.id).content == "original"

    await native_channel.set_permissions(alice.member, overwrite=None)
    accepted = await alice.submit_modal(shown, {"answer": "restored"})
    assert accepted.response.id == source.id
    assert accepted.response.content == "restored"
    assert submissions == ["restored"]
    assert len(env.backend.interactions) == before + 1
