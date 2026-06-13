import discord
import pytest


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


async def test_forum_post_rejects_unknown_tag(env):
    # A tag that belongs to a different forum is not valid here.
    other = env.guild.create_forum_channel("other")
    target = env.guild.create_forum_channel("help")
    await env.settle()

    cached_other = await env.bot.fetch_channel(other.id)
    await cached_other.edit(available_tags=[discord.ForumTag(name="bug")])
    await env.settle()
    foreign_tag = (await env.bot.fetch_channel(other.id)).available_tags[0]

    cached_target = await env.bot.fetch_channel(target.id)
    with pytest.raises(discord.HTTPException) as exc_info:
        await cached_target.create_thread(name="oops", content="body", applied_tags=[foreign_tag])
    assert exc_info.value.code == 50035


async def test_forum_post_requires_send_messages(env):
    forum = env.guild.create_forum_channel("help")
    await env.settle()

    mask = ~discord.Permissions(send_messages=True).value
    for role in env.backend.get_guild(env.guild.id).roles.values():
        role.permissions &= mask

    cached = env.bot.get_channel(forum.id)
    with pytest.raises(discord.Forbidden) as exc_info:
        await cached.create_thread(name="nope", content="blocked")
    assert exc_info.value.code == 50013
