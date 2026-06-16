"""Gateway cosmetic surface (latency/ratelimit/presence) and auto-moderation
exemptions — branches the feature tests skip."""

import discord


async def test_gateway_cosmetic_methods(env):
    # These are inert for an offline bot but part of the discord.py contract.
    await env.bot.change_presence(status=discord.Status.online)
    assert env.bot.latency == 0.0
    assert env.bot.ws.is_ratelimited() is False


def _keyword_rule_kwargs(**extra):
    return dict(
        name="No badwords",
        event_type=discord.AutoModRuleEventType.message_send,
        trigger=discord.AutoModTrigger(
            type=discord.AutoModRuleTriggerType.keyword, keyword_filter=["badword"]
        ),
        actions=[discord.AutoModRuleAction(custom_message="blocked")],
        enabled=True,
        **extra,
    )


async def test_automod_exempt_channel_is_not_filtered(env, alice):
    safe = env.guild.create_text_channel("safe")
    guild = env.bot.get_guild(env.guild.id)
    await guild.create_automod_rule(**_keyword_rule_kwargs(exempt_channels=[guild.get_channel(safe.id)]))
    await env.settle()

    # The keyword would normally be blocked, but this channel is exempt.
    await alice.send(safe, "this contains a badword")
    await env.settle()
    assert len(safe.history()) == 1


async def test_automod_disabled_rule_does_not_filter(env, channel, alice):
    guild = env.bot.get_guild(env.guild.id)
    rule = await guild.create_automod_rule(**_keyword_rule_kwargs())
    await rule.edit(enabled=False)
    await env.settle()

    await alice.send(channel, "this contains a badword")
    await env.settle()
    assert len(channel.history()) == 1  # disabled rule is skipped
