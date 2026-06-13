import discord


async def test_create_forum_post(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    cached = env.bot.get_channel(forum.id)
    twm = await cached.create_thread(name="How do I X?", content="please help")
    await env.settle()

    assert isinstance(twm.thread, discord.Thread)
    assert twm.thread.name == "How do I X?"
    assert twm.message.content == "please help"
    # The post shows up as a thread of the forum, carrying its starter message.
    posts = forum.threads
    assert [p.name for p in posts] == ["How do I X?"]
    assert posts[0].history()[-1].content == "please help"


async def test_configure_forum_tags(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    cached = await env.bot.fetch_channel(forum.id)
    await cached.edit(available_tags=[discord.ForumTag(name="bug"), discord.ForumTag(name="feature")])
    await env.settle()

    refetched = await env.bot.fetch_channel(forum.id)
    assert [t.name for t in refetched.available_tags] == ["bug", "feature"]


async def test_forum_post_with_applied_tag(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    cached = await env.bot.fetch_channel(forum.id)
    await cached.edit(available_tags=[discord.ForumTag(name="bug")])
    await env.settle()

    cached = await env.bot.fetch_channel(forum.id)
    tag = cached.available_tags[0]
    twm = await cached.create_thread(name="It crashes", content="stack trace inside", applied_tags=[tag])
    await env.settle()

    assert env.backend.get_channel(twm.thread.id).applied_tags == [tag.id]
    assert tag.id in {t.id for t in twm.thread.applied_tags}
