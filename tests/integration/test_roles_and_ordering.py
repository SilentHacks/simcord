import discord


async def test_fetch_role(env):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Mods")
    await env.settle()

    fetched = await guild.fetch_role(role.id)
    assert fetched.id == role.id
    assert fetched.name == "Mods"


async def test_edit_role_positions(env):
    guild = env.bot.get_guild(env.guild.id)
    low = await guild.create_role(name="Low")
    high = await guild.create_role(name="High")
    await env.settle()

    await guild.edit_role_positions({low: 5, high: 6})
    await env.settle()

    assert env.backend.get_role(guild.id, low.id).position == 5
    assert env.backend.get_role(guild.id, high.id).position == 6
    # The cache saw the GUILD_ROLE_UPDATE events.
    assert guild.get_role(low.id).position == 5


async def test_move_channel_position(env):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_text_channel("first")  # a sibling so the move actually reorders
    second = await guild.create_text_channel("second")
    await env.settle()

    # Channel.move routes through the bulk-update endpoint.
    await guild.get_channel(second.id).move(beginning=True)
    await env.settle()

    assert env.backend.get_channel(second.id).position == 0


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
