---
title: "Audit logs, polls, events, voice & more"
description: "Test the extended Discord surface with SimCord — audit logs, polls, scheduled events, voice state, invites, emojis/stickers and auto-moderation — all in-process with real discord.py code paths."
---

# Audit logs, polls, events, voice & more

SimCord 0.6.0 covers the extended guild surface most real bots reach for. Every feature is
driven through real discord.py code paths and is permission-checked exactly as on Discord.

## Audit logs

Privileged actions the bot performs over the API — bans, kicks, role/member/channel/role
edits, scheduled-event CRUD — are recorded in the guild audit log and announced via
`on_audit_log_entry_create`. (Omnipotent test setup through builders is *not* logged, just as
Discord only logs real API actions.) The reason a bot passes is captured too:

```python
guild = env.bot.get_guild(env.guild.id)
await guild.get_member(target.id).ban(reason="spam")

entries = [e async for e in guild.audit_logs(action=discord.AuditLogAction.ban)]
assert entries[0].target.id == target.id
assert entries[0].reason == "spam"
```

`guild.audit_logs()` supports `user=`, `action=`, `before`/`after` and `limit` filtering.
For assertions you can also read `env.guild.audit_log()` directly.

## Polls

A bot sends a poll like any message; a user votes with `actor.vote`:

```python
poll = discord.Poll(question="Lunch?", duration=datetime.timedelta(hours=1))
poll.add_answer(text="Pizza"); poll.add_answer(text="Sushi")
message = await env.bot.get_channel(channel.id).send(poll=poll)

await alice.vote(message, answer=1)        # fires MESSAGE_POLL_VOTE_ADD
await alice.remove_vote(message, answer=1)
```

Polls finalize when their deadline passes — either explicitly (`await message.end_poll()`) or
by fast-forwarding the virtual clock with `env.advance_time(...)`.

## Scheduled events

```python
event = await guild.create_scheduled_event(
    name="Community Meetup", start_time=start, end_time=end,
    entity_type=discord.EntityType.external, location="Online",
)
await alice.subscribe_event(event.id)      # fires GUILD_SCHEDULED_EVENT_USER_ADD
```

Voice/stage events take a `channel=`; external events require `location` and `end_time`.

## Voice state (state only — never audio)

SimCord models who is connected where, plus mute/deaf — it never touches audio.

```python
voice = env.guild.create_voice_channel("General Voice")
await alice.join_voice(voice, self_mute=True)   # fires VOICE_STATE_UPDATE
await guild.get_member(alice.id).edit(mute=True) # server mute
await guild.get_member(alice.id).move_to(other)  # move (audit-logged)
await alice.leave_voice()
```

## Invites, emojis & stickers

```python
invite = await env.bot.get_channel(channel.id).create_invite(max_uses=5)
emoji  = await guild.create_custom_emoji(name="party", image=png_bytes)
sticker = env.guild.create_sticker("wave", tags="wave")  # builder setup
```

Create/list/fetch/delete all work and fire the matching gateway events
(`INVITE_CREATE`/`INVITE_DELETE`, `GUILD_EMOJIS_UPDATE`, `GUILD_STICKERS_UPDATE`).

## Auto-moderation

Keyword rules are evaluated on message send. A `block_message` action drops the message and
fires `AUTO_MODERATION_ACTION_EXECUTION`:

```python
await guild.create_automod_rule(
    name="No badwords",
    event_type=discord.AutoModRuleEventType.message_send,
    trigger=discord.AutoModTrigger(
        type=discord.AutoModRuleTriggerType.keyword, keyword_filter=["badword"]
    ),
    actions=[discord.AutoModRuleAction(custom_message="blocked")],
    enabled=True,
)

await alice.send(channel, "contains a badword")  # blocked — appears nowhere
assert channel.history() == []
```

Exempt roles and channels are honored; rules in a guild without auto-mod have zero effect.

## Channel kinds

Builders can create the channel kinds these features need:

```python
env.guild.create_voice_channel("General Voice", user_limit=10)
env.guild.create_stage_channel("Town Hall")
env.guild.create_category("Community")
env.guild.create_forum_channel("help")
```
