import discord


async def test_bulk_ban(env):
    alice = env.guild.add_member(env.create_user("alice"))
    bob = env.guild.add_member(env.create_user("bob"))
    await env.settle()

    guild = env.bot.get_guild(env.guild.id)
    result = await guild.bulk_ban([discord.Object(id=alice.id), discord.Object(id=bob.id)])
    await env.settle()

    assert {u.id for u in result.banned} == {alice.id, bob.id}
    assert env.guild.get_ban(alice) is not None
    assert alice.id not in env.backend.get_guild(env.guild.id).members


async def test_estimate_and_prune_members(env):
    # Roleless members are prunable; a member with a role is retained.
    env.guild.add_member(env.create_user("idle"))
    role = env.guild.create_role("active")
    env.guild.add_member(env.create_user("keeper"), roles=[role])
    await env.settle()

    guild = env.bot.get_guild(env.guild.id)
    assert await guild.estimate_pruned_members(days=7) == 1

    pruned = await guild.prune_members(days=7, reason="cleanup")
    await env.settle()
    assert pruned == 1
    assert "idle" not in {m.name for m in guild.members}


async def test_publish_announcement(env):
    news = env.guild.create_news_channel("announcements")
    await env.settle()

    cached = env.bot.get_channel(news.id)
    message = await cached.send("ship it")
    await message.publish()
    await env.settle()

    stored = env.backend.get_message(news.id, message.id)
    assert stored.flags & env.backend.CROSSPOSTED_FLAG


async def test_publish_requires_news_channel(env):
    channel = env.guild.create_text_channel("general")
    await env.settle()

    cached = env.bot.get_channel(channel.id)
    message = await cached.send("not announceable")
    try:
        await message.publish()
    except discord.HTTPException as exc:
        assert exc.code == 50024
    else:
        raise AssertionError("publish in a non-news channel should fail")
