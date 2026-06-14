"""Read-projection conformance: every serializer payload must parse through
discord.py's *real* model constructors.

Serializers are annotated with ``discord.types`` TypedDicts, but those are a
static promise only — ``cast()`` silences them, so a payload that omits a
required key or stubs the wrong shape still type-checks. This suite proves the
promise at runtime by feeding each payload to the actual discord.py parser the
user's bot would use. If discord.py's own constructor accepts it, the shape is
honest; a missing/renamed key surfaces as a real ``KeyError``/``ValueError``.

``discord.Guild(data=guild_create_payload(...))`` recursively constructs
members, channels, threads, roles, emojis, stickers, voice states, scheduled
events and stage instances, so one parse exercises most nested serializers; the
remaining top-level objects are parsed individually below.
"""

from __future__ import annotations

import discord

from simcord import _dpy_internals
from simcord.backend import serializers as S
from simcord.backend.models import Poll, PollAnswer
from simcord.enums import ChannelType


async def _populate(backend, gid):
    """Create one of (nearly) everything in ``gid`` and return a handle bag."""
    user = backend.make_user("alice")
    backend.add_member(gid, user.id)
    text = backend.create_channel(gid, "general", type=ChannelType.TEXT)
    voice = backend.create_channel(gid, "Voice", type=ChannelType.VOICE)
    backend.create_channel(gid, "Category", type=ChannelType.CATEGORY)
    forum = backend.create_channel(gid, "forum", type=ChannelType.FORUM)
    stage = backend.create_channel(gid, "Stage", type=ChannelType.STAGE_VOICE)
    role = backend.create_role(gid, "Mods", permissions=0)
    emoji = backend.create_emoji(gid, "smile", user.id)
    sticker = backend.create_sticker(gid, "sticky", user.id, tags="smile")
    message = backend.create_message(text.id, user.id, "hi")
    thread = backend.create_thread(text.id, "thread", user.id)
    rule = backend.create_auto_mod_rule(
        gid,
        user.id,
        {
            "name": "no-spam",
            "event_type": 1,
            "trigger_type": 1,
            "trigger_metadata": {"keyword_filter": ["badword"]},
            "actions": [{"type": 1, "metadata": {}}],
        },
    )
    event = backend.create_scheduled_event(
        gid,
        name="Launch",
        entity_type=2,
        scheduled_start_time=backend.iso_after(3600),
        channel_id=voice.id,
        creator_id=user.id,
    )
    invite = backend.create_invite(text.id, user.id)
    backend.set_voice_state(gid, user.id, voice.id)
    voice_state = backend.get_guild(gid).voice_states[user.id]
    webhook = backend.create_webhook(text.id, "hook", user.id)
    instance = backend.create_stage_instance(stage.id, "Topic")
    poll = Poll(
        question="Pizza?",
        answers=[PollAnswer(1, "Yes"), PollAnswer(2, "No")],
        expiry=backend.iso_after(3600),
    )
    poll_msg = backend.create_message(text.id, user.id, "vote", poll=poll)
    return {
        "user": user,
        "text": text,
        "voice": voice,
        "forum": forum,
        "role": role,
        "emoji": emoji,
        "sticker": sticker,
        "message": message,
        "thread": thread,
        "rule": rule,
        "event": event,
        "invite": invite,
        "voice_state": voice_state,
        "webhook": webhook,
        "instance": instance,
        "poll_msg": poll_msg,
    }


async def test_serializers_parse_through_real_discordpy(env):
    """Each serializer's payload is accepted by discord.py's real parser."""
    backend = env.backend
    state = _dpy_internals.get_state(env.bot)
    gid = env.guild.id
    bag = await _populate(backend, gid)
    bg = backend.get_guild(gid)

    # The big one: a full GUILD_CREATE payload recursively constructs members,
    # channels, threads, roles, emojis, stickers, voice states, scheduled events
    # and stage instances — validating all of those serializers at once.
    dpy_guild = discord.Guild(data=S.guild_create_payload(backend, bg), state=state)
    assert dpy_guild.id == gid
    assert {c.name for c in dpy_guild.channels} >= {"general", "Voice", "Category", "forum", "Stage"}
    assert dpy_guild.get_role(bag["role"].id) is not None
    assert dpy_guild.get_member(bag["user"].id) is not None
    assert len(dpy_guild.emojis) >= 1 and len(dpy_guild.stickers) >= 1
    assert len(dpy_guild.scheduled_events) >= 1

    dpy_member = dpy_guild.get_member(bag["user"].id)
    text_channel = dpy_guild.get_channel(bag["text"].id)

    # Top-level serializers not (fully) covered by the guild payload above. Each
    # thunk must construct the real discord.py model without raising.
    cases = {
        "user_payload": lambda: discord.User(state=state, data=S.user_payload(bag["user"])),
        "role_payload": lambda: discord.Role(guild=dpy_guild, state=state, data=S.role_payload(bag["role"])),
        "member_payload": lambda: discord.Member(
            data=S.member_payload(backend, bg, bg.members[bag["user"].id]),
            guild=dpy_guild,
            state=state,
        ),
        "message_payload": lambda: discord.Message(
            state=state, channel=text_channel, data=S.message_payload(backend, bag["message"])
        ),
        "message_payload(poll)": lambda: discord.Message(
            state=state, channel=text_channel, data=S.message_payload(backend, bag["poll_msg"])
        ),
        "thread_payload": lambda: discord.Thread(
            guild=dpy_guild, state=state, data=S.thread_payload(backend, bag["thread"])
        ),
        "guild_emoji_payload": lambda: discord.Emoji(
            guild=dpy_guild, state=state, data=S.guild_emoji_payload(backend, bag["emoji"])
        ),
        "sticker_payload": lambda: discord.GuildSticker(
            state=state, data=S.sticker_payload(backend, bag["sticker"])
        ),
        "scheduled_event_payload": lambda: discord.ScheduledEvent(
            state=state, data=S.scheduled_event_payload(backend, bag["event"])
        ),
        "auto_mod_rule_payload": lambda: discord.AutoModRule(
            data=S.auto_mod_rule_payload(backend, bag["rule"]), guild=dpy_guild, state=state
        ),
        "voice_state_payload": lambda: discord.VoiceState(
            data=S.voice_state_payload(backend, bag["voice_state"]), channel=None
        ),
        "webhook_payload": lambda: discord.Webhook.from_state(
            data=S.webhook_payload(backend, bag["webhook"]), state=state
        ),
        "invite_payload": lambda: discord.Invite.from_incomplete(
            state=state, data=S.invite_payload(backend, bag["invite"])
        ),
        "stage_instance_payload": lambda: discord.StageInstance(
            state=state, guild=dpy_guild, data=S.stage_instance_payload(bag["instance"])
        ),
    }

    failures = []
    for label, thunk in cases.items():
        try:
            model = thunk()
            assert model is not None
        except Exception as exc:  # collect every failure and report them together
            failures.append(f"{label}: {type(exc).__name__}: {exc}")

    # The poll rides along on its message; prove it actually parsed.
    poll_model = discord.Message(
        state=state, channel=text_channel, data=S.message_payload(backend, bag["poll_msg"])
    )
    if poll_model.poll is None:
        failures.append("poll_payload: message.poll did not parse from the payload")

    assert dpy_member is not None
    assert not failures, "serializers rejected by discord.py's real parsers:\n" + "\n".join(failures)
