import discord


def _rule_kwargs(keywords):
    return dict(
        name="No badwords",
        event_type=discord.AutoModRuleEventType.message_send,
        trigger=discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.keyword, keyword_filter=keywords),
        actions=[discord.AutoModRuleAction(custom_message="blocked")],
        enabled=True,
    )


def _keyword_rule_kwargs():
    return _rule_kwargs(["badword"])


async def test_rule_crud(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    rule = await guild.create_automod_rule(**_keyword_rule_kwargs())
    await env.settle()

    assert "AUTO_MODERATION_RULE_CREATE" in env.transcript()
    assert rule.id in env.backend.get_guild(env.guild.id).auto_mod_rules

    fetched = await guild.fetch_automod_rules()
    assert [r.name for r in fetched] == ["No badwords"]

    await rule.delete()
    await env.settle()
    assert rule.id not in env.backend.get_guild(env.guild.id).auto_mod_rules


async def test_rule_fetch_single_and_edit(env, channel):
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Exempt")
    rule = await guild.create_automod_rule(**_keyword_rule_kwargs())
    await env.settle()

    # GET a single rule by id.
    fetched = await guild.fetch_automod_rule(rule.id)
    assert fetched.id == rule.id
    assert fetched.name == "No badwords"

    # PATCH it, including the exempt role/channel lists (coerced to int ids).
    await rule.edit(
        name="Renamed",
        enabled=False,
        exempt_roles=[role],
        exempt_channels=[guild.get_channel(channel.id)],
    )
    await env.settle()

    backend_rule = env.backend.get_auto_mod_rule(env.guild.id, rule.id)
    assert backend_rule.name == "Renamed"
    assert backend_rule.enabled is False
    assert role.id in backend_rule.exempt_roles
    assert channel.id in backend_rule.exempt_channels


async def test_keyword_rule_blocks_message(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_keyword_rule_kwargs())
    await env.settle()

    await alice.send(channel, "this contains a badword!")
    await env.settle()

    # The message was blocked: it appears nowhere, and an execution event fired.
    assert channel.history() == []
    assert "AUTO_MODERATION_ACTION_EXECUTION" in env.transcript()


async def test_clean_message_passes(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_keyword_rule_kwargs())
    await env.settle()

    await alice.send(channel, "a perfectly fine message")
    await env.settle()
    assert len(channel.history()) == 1


async def test_bare_keyword_matches_whole_word_only(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_rule_kwargs(["spam"]))
    await env.settle()

    await alice.send(channel, "delicious spammer recipe")  # substring, not a whole word
    await env.settle()
    assert len(channel.history()) == 1  # not blocked

    await alice.send(channel, "this is spam")  # whole word: blocked
    await env.settle()
    assert len(channel.history()) == 1  # history unchanged — second message dropped


async def test_wildcard_keyword_matches_substring(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_rule_kwargs(["*spam*"]))
    await env.settle()

    await alice.send(channel, "delicious spammer recipe")  # substring match via wildcard
    await env.settle()
    assert channel.history() == []


def _mention_spam_rule_kwargs(limit):
    return dict(
        name="No mention spam",
        event_type=discord.AutoModRuleEventType.message_send,
        trigger=discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.mention_spam, mention_limit=limit),
        actions=[discord.AutoModRuleAction(custom_message="blocked")],
        enabled=True,
    )


async def test_mention_spam_blocks_over_limit(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_mention_spam_rule_kwargs(2))
    await env.settle()

    bob = env.guild.add_member(env.create_user("bob"))
    carol = env.guild.add_member(env.create_user("carol"))
    await alice.send(channel, f"{alice.mention} {bob.mention} {carol.mention} look here")
    await env.settle()

    assert channel.history() == []
    assert "AUTO_MODERATION_ACTION_EXECUTION" in env.transcript()


async def test_mention_spam_allows_under_limit(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_mention_spam_rule_kwargs(3))
    await env.settle()

    bob = env.guild.add_member(env.create_user("bob"))
    await alice.send(channel, f"hi {alice.mention} {bob.mention}")
    await env.settle()

    assert len(channel.history()) == 1


async def test_mention_spam_counts_unique_mentions(env, channel, alice):
    # Discord's mention_total_limit counts unique mentions, so spamming the same
    # user repeatedly stays under a limit of 2.
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_mention_spam_rule_kwargs(2))
    await env.settle()

    await alice.send(channel, f"{alice.mention} {alice.mention} {alice.mention}")
    await env.settle()

    assert len(channel.history()) == 1
