import discord


async def test_emoji_crud(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    emoji = await guild.create_custom_emoji(name="party", image=b"\x89PNG\r\n\x1a\n")
    await env.settle()

    assert "GUILD_EMOJIS_UPDATE" in env.transcript()
    assert emoji.id in env.backend.get_guild(env.guild.id).emojis

    fetched = await guild.fetch_emojis()
    assert [e.name for e in fetched] == ["party"]

    await emoji.delete()
    await env.settle()
    assert emoji.id not in env.backend.get_guild(env.guild.id).emojis


async def test_sticker_listing_and_delete(env, channel):
    # Stickers upload via multipart; create through the omnipotent builder, then
    # exercise the read/delete routes the bot uses.
    sticker = env.guild.create_sticker("wave", tags="wave")
    guild = env.bot.get_guild(env.guild.id)

    fetched = await guild.fetch_stickers()
    assert [s.name for s in fetched] == ["wave"]

    full = await guild.fetch_sticker(sticker.id)
    await full.delete()
    await env.settle()
    assert sticker.id not in env.backend.get_guild(env.guild.id).stickers


async def test_emoji_fetch_single_and_edit(env):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Nitro")
    emoji = await guild.create_custom_emoji(name="party", image=b"\x89PNG\r\n\x1a\n")
    await env.settle()

    fetched = await guild.fetch_emoji(emoji.id)
    assert fetched.id == emoji.id

    await emoji.edit(name="celebrate", roles=[role])
    await env.settle()

    backend_emoji = env.backend.get_emoji(env.guild.id, emoji.id)
    assert backend_emoji.name == "celebrate"
    assert role.id in backend_emoji.role_ids


async def test_sticker_edit(env):
    sticker = env.guild.create_sticker("wave", tags="wave")
    guild = env.bot.get_guild(env.guild.id)

    full = await guild.fetch_sticker(sticker.id)
    # GuildSticker.edit converts a unicode emoji to its unicode *name* and sends
    # that as the sticker's tags (unlike create, which sends the raw char).
    await full.edit(name="hello", description="a greeting", emoji="👋")
    await env.settle()

    backend_sticker = env.backend.get_guild(env.guild.id).stickers[sticker.id]
    assert backend_sticker.name == "hello"
    assert backend_sticker.description == "a greeting"
    assert backend_sticker.tags == "WAVING HAND SIGN"


async def test_emoji_requires_permission(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    mask = ~discord.Permissions(manage_expressions=True).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask
    try:
        await guild.create_custom_emoji(name="x", image=b"\x89PNG\r\n\x1a\n")
    except discord.Forbidden:
        return
    raise AssertionError("expected Forbidden")
