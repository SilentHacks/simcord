_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # header is all discord.py inspects


async def test_application_emoji_crud(env):
    created = await env.bot.create_application_emoji(name="party", image=_PNG)
    assert created.name == "party"

    fetched = await env.bot.fetch_application_emojis()
    assert created.id in {e.id for e in fetched}

    one = await env.bot.fetch_application_emoji(created.id)
    assert one.name == "party"

    await one.edit(name="party2")
    assert (await env.bot.fetch_application_emoji(created.id)).name == "party2"

    await one.delete()
    assert created.id not in {e.id for e in await env.bot.fetch_application_emojis()}


async def test_edit_without_name_keeps_name(env):
    # discord.py omits unchanged fields, so an edit with no `name` must not crash
    # (previously a KeyError -> 500); the name is left untouched.
    created = await env.bot.create_application_emoji(name="keep", image=_PNG)
    unchanged = env.backend.edit_application_emoji(created.id, None)
    assert unchanged.name == "keep"
