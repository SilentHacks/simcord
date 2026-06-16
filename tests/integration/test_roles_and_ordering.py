import discord


async def test_fetch_role(env):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Mods")
    await env.settle()

    fetched = await guild.fetch_role(role.id)
    assert fetched.id == role.id
    assert fetched.name == "Mods"


async def test_role_edit_applies_and_audits(env):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Helper", colour=discord.Colour(0x111111))
    await env.settle()

    await role.edit(
        name="Senior Helper",
        colour=discord.Colour(0x00FF00),
        hoist=True,
        mentionable=True,
        permissions=discord.Permissions(manage_messages=True),
    )
    await env.settle()

    backend_role = env.backend.get_role(env.guild.id, role.id)
    assert backend_role.name == "Senior Helper"
    assert backend_role.color == 0x00FF00
    assert backend_role.hoist is True
    assert backend_role.mentionable is True
    assert backend_role.permissions == discord.Permissions(manage_messages=True).value
    # The edit was journalled to the audit log (ROLE_UPDATE = 31).
    assert 31 in {e.action_type for e in env.guild.audit_log()}


async def test_edit_role_positions(env):
    guild = env.bot.get_guild(env.guild.id)
    low = await guild.create_role(name="Low")
    high = await guild.create_role(name="High")
    await env.settle()

    # Both roles sit below the bot's managed role; swapping them is a legal move
    # (neither crosses the bot's top role, which Discord would forbid).
    low_pos = guild.get_role(low.id).position
    high_pos = guild.get_role(high.id).position
    await guild.edit_role_positions({low: high_pos, high: low_pos})
    await env.settle()

    assert env.backend.get_role(guild.id, low.id).position == high_pos
    assert env.backend.get_role(guild.id, high.id).position == low_pos
    # The cache saw the GUILD_ROLE_UPDATE events.
    assert guild.get_role(low.id).position == high_pos


async def test_reorder_cannot_lift_role_above_bot(env):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Climber")
    await env.settle()

    # The bot's managed role stays on top; lifting another role to/above it is
    # rejected, exactly as real Discord rejects elevating a role past your own.
    try:
        await guild.edit_role_positions({role: 99})
    except discord.Forbidden as exc:
        assert exc.code == 50013
    else:
        raise AssertionError("expected Forbidden when lifting a role above the bot")


async def test_move_channel_position(env):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_text_channel("first")  # a sibling so the move actually reorders
    second = await guild.create_text_channel("second")
    await env.settle()

    # Channel.move routes through the bulk-update endpoint.
    await guild.get_channel(second.id).move(beginning=True)
    await env.settle()

    assert env.backend.get_channel(second.id).position == 0


async def test_create_channel_honours_explicit_position(env):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_text_channel("first")
    pinned = await guild.create_text_channel("pinned", position=0)
    await env.settle()

    # An explicit position is wired through rather than overwritten with the
    # append-to-end default (it is a modelled field, so it must not be dropped).
    assert env.backend.get_channel(pinned.id).position == 0


async def test_move_channel_into_category(env):
    guild = env.bot.get_guild(env.guild.id)
    category = await guild.create_category("Cat")
    channel = await guild.create_text_channel("topic")
    await env.settle()

    await guild.get_channel(channel.id).move(beginning=True, category=category)
    await env.settle()

    assert env.backend.get_channel(channel.id).parent_id == category.id


async def test_reorder_requires_manage_roles(env):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Plain")
    await env.settle()

    mask = ~discord.Permissions(manage_roles=True).value
    for backend_role in env.backend.get_guild(env.guild.id).roles.values():
        backend_role.permissions &= mask

    try:
        await guild.edit_role_positions({role: 3})
    except discord.Forbidden as exc:
        assert exc.code == 50013
    else:
        raise AssertionError("expected Forbidden when lacking manage_roles")
