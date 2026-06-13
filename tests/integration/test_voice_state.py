async def test_actor_joins_voice(env, channel, alice):
    voice = env.guild.create_voice_channel("General Voice")
    await env.settle()

    await alice.join_voice(voice, self_mute=True)

    guild = env.bot.get_guild(env.guild.id)
    member = guild.get_member(alice.id)
    assert member.voice is not None
    assert member.voice.channel.id == voice.id
    assert member.voice.self_mute is True
    assert "VOICE_STATE_UPDATE" in env.transcript()


async def test_actor_leaves_voice(env, channel, alice):
    voice = env.guild.create_voice_channel("General Voice")
    await env.settle()
    await alice.join_voice(voice)
    await alice.leave_voice()

    member = env.bot.get_guild(env.guild.id).get_member(alice.id)
    assert member.voice is None
    assert alice.id not in env.guild.voice_states()


async def test_server_mute_via_edit(env, channel, alice):
    voice = env.guild.create_voice_channel("General Voice")
    await env.settle()
    await alice.join_voice(voice)

    guild = env.bot.get_guild(env.guild.id)
    await guild.get_member(alice.id).edit(mute=True)
    await env.settle()

    assert env.guild.voice_states()[alice.id].mute is True


async def test_move_member_between_channels(env, channel, alice):
    first = env.guild.create_voice_channel("First")
    second = env.guild.create_voice_channel("Second")
    await env.settle()
    await alice.join_voice(first)

    guild = env.bot.get_guild(env.guild.id)
    await guild.get_member(alice.id).move_to(guild.get_channel(second.id))
    await env.settle()

    assert env.guild.voice_states()[alice.id].channel_id == second.id
    # The move was recorded in the audit log.
    moves = [e for e in env.guild.audit_log() if e.action_type == 26]
    assert moves


async def test_sample_bot_voice_log_listener(env, alice):
    voice_log = env.guild.create_text_channel("voice-log")
    voice = env.guild.create_voice_channel("General Voice")
    await env.settle()

    await alice.join_voice(voice)
    assert voice_log.last_message.content == "alice joined General Voice"
