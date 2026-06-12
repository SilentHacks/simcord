"""Intent simulation end-to-end: gating, content censoring, chunking, 4014."""

import discord
import pytest
from discord.ext import commands

import simcord as dpt


def make_bot(**intent_overrides):
    intents = discord.Intents.default()  # privileged intents all off, like real life
    for name, value in intent_overrides.items():
        setattr(intents, name, value)
    return commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------- message_content


async def test_prefix_commands_fail_without_message_content():
    """The classic production bug: prefix bot with Intents.default() is deaf."""
    bot = make_bot()
    seen: list[discord.Message] = []

    @bot.listen("on_message")
    async def capture(message):
        seen.append(message)

    @bot.command()
    async def ping(ctx):
        await ctx.send("Pong!")

    async with dpt.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        await alice.send(channel, "!ping")

        # The event arrived, but with censored content — so the command never ran.
        from_alice = [m for m in seen if m.author.id == alice.id]
        assert from_alice and from_alice[0].content == ""
        contents = [m.content for m in channel.history()]
        assert contents == ["!ping"]  # no "Pong!" reply
        assert "CENSORED" in env.transcript()


async def test_message_content_intent_reveals_content():
    bot = make_bot(message_content=True)

    @bot.command()
    async def ping(ctx):
        await ctx.send("Pong!")

    async with dpt.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        await alice.send(channel, "!ping")
        assert channel.last_message.content == "Pong!"


async def test_censoring_exemptions_dm_and_mention():
    bot = make_bot()
    seen: list[discord.Message] = []

    @bot.listen("on_message")
    async def capture(message):
        seen.append(message)

    async with dpt.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))

        await alice.user.send_dm("dm secret")
        assert seen[-1].content == "dm secret"

        await alice.send(channel, f"hey <@{env.bot.user.id}> look")
        assert env.bot.user.mentioned_in(seen[-1])
        assert seen[-1].content == f"hey <@{env.bot.user.id}> look"


# ------------------------------------------------------------ event gating


async def test_member_events_dropped_without_members_intent():
    bot = make_bot()
    joins: list[discord.Member] = []

    @bot.listen("on_member_join")
    async def capture(member):
        joins.append(member)

    async with dpt.run(bot) as env:
        guild = env.create_guild()
        guild.add_member(env.create_user("alice"))
        await env.settle()
        assert joins == []
        cached = env.bot.get_guild(guild.id)
        assert len(cached.members) == 1 and cached.me is not None  # only the bot
        assert "DROPPED  GUILD_MEMBER_ADD" in env.transcript()


async def test_member_events_delivered_with_members_intent():
    bot = make_bot(members=True)
    joins: list[discord.Member] = []

    @bot.listen("on_member_join")
    async def capture(member):
        joins.append(member)

    async with dpt.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        await env.settle()
        assert [m.id for m in joins] == [alice.id]
        assert env.bot.get_guild(guild.id).get_member(alice.id) is not None


async def test_typing_dropped_without_typing_intent():
    bot = make_bot(typing=False)
    typed = []

    @bot.listen("on_typing")
    async def capture(channel, user, when):
        typed.append(user)

    async with dpt.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        await alice.typing(channel)
        await env.settle()
        assert typed == []
        assert "DROPPED  TYPING_START" in env.transcript()


# ------------------------------------------------------- privileged intents


async def test_unapproved_privileged_intents_raise_like_a_real_connect():
    bot = make_bot(members=True)
    with pytest.raises(discord.PrivilegedIntentsRequired):
        async with dpt.run(bot, approved_intents=discord.Intents.default()):
            pass  # pragma: no cover — start() must raise


async def test_approved_privileged_intents_connect_fine():
    bot = make_bot(members=True)
    approved = discord.Intents.default()
    approved.members = True
    async with dpt.run(bot, approved_intents=approved) as env:
        assert env.bot.user is not None


# --------------------------------------------------------- member chunking


def _readd_populated_guild(env, guild, *names):
    """Re-announce ``guild`` with extra members the client has never seen.

    Members are added without announcing (no GUILD_MEMBER_ADD), then a fresh
    GUILD_CREATE is emitted — the shape of joining an already-populated guild,
    which is what forces discord.py to chunk.
    """
    from simcord.backend import serializers

    users = [env.create_user(name) for name in names]
    for user in users:
        env.backend.add_member(guild.id, user.id)
    raw = env.backend.guilds[guild.id]
    env.backend.emit("GUILD_CREATE", serializers.guild_create_payload(env.backend, raw))
    return users


async def test_populated_guild_create_is_chunked_with_members_intent():
    bot = make_bot(members=True)
    async with dpt.run(bot) as env:
        guild = env.create_guild()
        (alice,) = _readd_populated_guild(env, guild, "alice")
        await env.settle()
        cached = env.bot.get_guild(guild.id)
        assert cached.chunked
        assert cached.get_member(alice.id) is not None  # arrived via the chunk
        assert "GUILD_MEMBERS_CHUNK" in env.transcript()


async def test_presences_intent_inlines_members_instead_of_chunking():
    bot = make_bot(presences=True, members=True)
    async with dpt.run(bot) as env:
        guild = env.create_guild()
        (alice,) = _readd_populated_guild(env, guild, "alice")
        await env.settle()
        cached = env.bot.get_guild(guild.id)
        assert cached.chunked
        assert cached.get_member(alice.id) is not None  # inlined in GUILD_CREATE
        assert "GUILD_MEMBERS_CHUNK" not in env.transcript()


async def test_query_members_by_prefix_and_ids():
    bot = make_bot(members=True)
    async with dpt.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        bob = guild.add_member(env.create_user("bob"), nick="albert")
        guild.add_member(env.create_user("carol"))
        await env.settle()
        cached = env.bot.get_guild(guild.id)

        by_prefix = await cached.query_members(query="al")
        assert {m.id for m in by_prefix} == {alice.id, bob.id}  # nick matches too

        by_ids = await cached.query_members(user_ids=[alice.id])
        assert [m.id for m in by_ids] == [alice.id]


async def test_explicit_guild_chunk():
    bot = make_bot(members=True)
    async with dpt.run(bot) as env:
        guild = env.create_guild()
        alice = guild.add_member(env.create_user("alice"))
        await env.settle()
        members = await env.bot.get_guild(guild.id).chunk()
        assert alice.id in {m.id for m in members}


async def test_chunk_apis_keep_their_real_client_side_guards():
    """Without the members intent, discord.py's own guard fires — same as prod."""
    bot = make_bot()
    async with dpt.run(bot) as env:
        guild = env.create_guild()
        await env.settle()
        with pytest.raises(discord.ClientException):
            await env.bot.get_guild(guild.id).chunk()
