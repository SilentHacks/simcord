import discord


async def test_dm_round_trip(env, channel, alice):
    await alice.send(channel, "!dmme")

    dm_history = alice.user.dm_channel.history()
    assert dm_history[-1].content == "Here's your DM"
    assert dm_history[-1].author == env.bot.user


async def test_user_can_dm_the_bot(env, alice):
    received = []

    @env.bot.listen()
    async def on_message(message: discord.Message) -> None:
        if message.guild is None and not message.author.bot:
            received.append(message.content)

    await alice.send_dm("hello bot")
    assert received == ["hello bot"]


async def test_pins(env, channel, alice):
    await alice.send(channel, "!pinit")

    pinned = channel.pinned_messages()
    assert len(pinned) == 1 and pinned[0].content == "pin me"
    cached = env.bot.get_channel(channel.id)
    fetched = [m async for m in cached.pins()]
    assert [m.content for m in fetched] == ["pin me"]


async def test_reactions(env, channel, alice):
    message = await alice.send(channel, "hello")
    await alice.react(message, "👋")

    assert channel.last_message.content == "alice waved!"

    cached = await env.bot.get_channel(channel.id).fetch_message(message.id)
    assert [str(r.emoji) for r in cached.reactions] == ["👋"]


async def test_threads(env, channel, alice):
    await alice.send(channel, "!thread")

    threads = channel.threads
    assert len(threads) == 1 and threads[0].name == "discussion"
    assert threads[0].history()[-1].content == "Let's talk here"

    cached_thread = env.bot.get_channel(threads[0].id)
    assert isinstance(cached_thread, discord.Thread)
    assert cached_thread.parent_id == channel.id


async def test_channel_edit_and_delete(env, channel):
    cached = env.bot.get_channel(channel.id)
    await cached.edit(topic="new topic")
    await env.settle()
    assert env.bot.get_channel(channel.id).topic == "new topic"

    await cached.delete()
    await env.settle()
    assert env.bot.get_channel(channel.id) is None


async def test_attachments(env, channel, alice):
    message = await alice.send(channel, "see file", attachments=[("notes.txt", b"hello world")])

    cached = await env.bot.get_channel(channel.id).fetch_message(message.id)
    assert cached.attachments[0].filename == "notes.txt"
    assert await cached.attachments[0].read() == b"hello world"
