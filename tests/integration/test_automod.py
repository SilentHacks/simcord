import discord


def _keyword_rule_kwargs():
    return dict(
        name="No badwords",
        event_type=discord.AutoModRuleEventType.message_send,
        trigger=discord.AutoModTrigger(
            type=discord.AutoModRuleTriggerType.keyword, keyword_filter=["badword"]
        ),
        actions=[discord.AutoModRuleAction(custom_message="blocked")],
        enabled=True,
    )


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
