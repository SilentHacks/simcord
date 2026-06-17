"""Example test suite using the bundled pytest plugin's `simcord_env` fixture.

Each test follows the same shape: build a world (synchronous builders), act as a
user (async actors), then assert on what the real bot did (queries).
"""

import discord
import pytest

from simcord import assert_sent

from .bot import create_bot


async def test_ping(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!ping")

    assert channel.last_message.content == "Pong!"


async def test_daily_cooldown(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!daily")
    assert_sent(channel, embed_title="Daily Reward")

    await alice.send(channel, "!daily")  # too soon — the cooldown blocks it
    assert_sent(channel, contains="wait")

    await simcord_env.advance_time(60 * 60 * 24)  # a day, instantly
    await alice.send(channel, "!daily")
    assert_sent(channel, embed_title="Daily Reward")


async def test_ban_requires_permission(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("mod")
    mods = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
    mod = guild.add_member(simcord_env.create_user("mod"), roles=[mods])
    rando = guild.add_member(simcord_env.create_user("rando"))
    target = guild.add_member(simcord_env.create_user("spammer"))

    denied = await rando.slash(channel, "ban", user=target)
    assert denied.response.content == "You can't do that."
    assert guild.get_ban(target) is None

    allowed = await mod.slash(channel, "ban", user=target, reason="spam")
    assert allowed.ephemeral
    assert allowed.response.content == f"Banned {target.mention}: spam"
    assert guild.get_ban(target) is not None


async def test_feedback_modal(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    shown = await alice.slash(channel, "feedback")
    submitted = await alice.submit_modal(shown, {"name": "Alice", "comment": "Great bot!"})

    assert submitted.response.content == "Thanks Alice!"
    assert submitted.response.ephemeral


async def test_purge_confirm_button(simcord_env):
    channel = simcord_env.create_guild().create_text_channel("general")
    alice = simcord_env.guild.add_member(simcord_env.create_user("alice"))

    started = await alice.slash(channel, "purge")
    prompt = started.response.message

    confirmed = await alice.click(prompt, label="Confirm")
    assert confirmed.response.content == "Purged."


async def test_role_panel_survives_restart(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("roles")
    guild.create_role("Gamer")
    alice = guild.add_member(simcord_env.create_user("alice"))

    await alice.send(channel, "!panel")
    panel = channel.last_message

    await simcord_env.restart_bot(create_bot())  # a fresh bot re-attaches the view

    await alice.select(panel, ["Gamer"], custom_id="panel:roles")
    assert any(role.name == "Gamer" for role in alice.member.roles)


async def test_env_is_strict_by_default(simcord_env):
    assert simcord_env.strict_sync is True


@pytest.mark.simcord(strict_sync=False)
async def test_marker_forwards_options_to_run(simcord_env):
    # The marker's kwargs reach simcord.run(), so per-test config needs no
    # custom fixture.
    assert simcord_env.strict_sync is False
