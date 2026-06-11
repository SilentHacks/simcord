"""Example test suite using the bundled pytest plugin's `dpt_env` fixture."""

import discord


async def test_ping(dpt_env):
    channel = dpt_env.create_guild().create_text_channel("general")
    alice = dpt_env.guild.add_member(dpt_env.create_user("alice"))

    await alice.send(channel, "!ping")

    assert channel.last_message.content == "Pong!"


async def test_ban_requires_permission(dpt_env):
    guild = dpt_env.create_guild()
    channel = guild.create_text_channel("mod")
    mods = guild.create_role("Mods", permissions=discord.Permissions(ban_members=True))
    mod = guild.add_member(dpt_env.create_user("mod"), roles=[mods])
    rando = guild.add_member(dpt_env.create_user("rando"))
    target = guild.add_member(dpt_env.create_user("spammer"))

    denied = await rando.slash(channel, "ban", user=target)
    assert denied.response.content == "You can't do that."
    assert guild.get_ban(target) is None

    allowed = await mod.slash(channel, "ban", user=target, reason="spam")
    assert allowed.ephemeral
    assert allowed.response.content == f"Banned {target.mention}: spam"
    assert guild.get_ban(target) is not None
