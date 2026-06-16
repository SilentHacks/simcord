"""Exercise the world-builder accessors and actor helpers that feature tests
reach past: handle properties/reprs, context menus, self voice state, and
scheduled-event subscription from the actor side.
"""

import datetime


async def test_handle_accessors_and_reprs(env):
    guild = env.guild
    await env.settle()

    assert repr(guild).startswith("<GuildHandle")
    assert guild.name == "Test Guild"
    assert guild.owner.name
    _ = guild.me  # cached bot member (or None)

    role = guild.create_role("VIP")
    assert role.name == "VIP"
    assert role.mention == f"<@&{role.id}>"
    assert repr(role).startswith("<RoleHandle")
    assert "VIP" in guild.roles

    category = guild.create_category("Cat")
    assert category.mention == f"<#{category.id}>"
    emoji = guild.create_emoji("party")
    assert emoji.name == "party"

    guild.create_text_channel("general")
    assert "general" in guild.channels

    user = env.create_user("zed")
    assert repr(user).startswith("<UserHandle")
    assert user.name == "zed"
    assert user.mention == f"<@{user.id}>"

    alice = guild.add_member(user)
    await env.settle()
    assert alice.name == "zed"
    assert alice.mention == f"<@{alice.id}>"
    assert alice.member is not None  # the bot cached the join


async def test_actor_context_menu(env, channel):
    reporter = env.guild.add_member(env.create_user("reporter"))
    target = env.guild.add_member(env.create_user("offender"))
    await env.settle()

    # The sample bot ships a "Report Member" user context menu.
    result = await reporter.context_menu(channel, "Report Member", target)
    assert "Reported" in result.response.content


async def test_actor_set_voice_and_event_subscription(env):
    voice = env.guild.create_voice_channel("Voice")
    alice = env.guild.add_member(env.create_user("alice"))
    await alice.join_voice(voice)
    await alice.set_voice(self_mute=True, self_deaf=True)

    state = env.guild.voice_states()[alice.id]
    assert state.self_mute is True
    assert state.self_deaf is True

    start = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).isoformat()
    event = env.guild.create_scheduled_event("Party", start_time=start, entity_type=2, channel=voice)

    await alice.subscribe_event(event)
    assert alice.id in env.backend.get_scheduled_event(env.guild.id, event.id).user_ids

    await alice.unsubscribe_event(event)
    assert alice.id not in env.backend.get_scheduled_event(env.guild.id, event.id).user_ids
