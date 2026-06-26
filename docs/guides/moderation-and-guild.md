---
title: "Audit logs, polls, events, voice & more"
description: "Test the extended Discord surface with SimCord — audit logs, polls, scheduled events, voice state, invites, emojis/stickers and auto-moderation — all in-process with real discord.py code paths."
---

# Audit logs, polls, events, voice & more

SimCord covers the extended guild surface most real bots reach for. Every feature is
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

Keywords follow Discord's wildcard rules: a bare `badword` matches only as a whole word,
while `*badword*` matches it as a substring anywhere (and `badword*` / `*badword` match as
a prefix / suffix). Mention-spam rules (`AutoModRuleTriggerType.mention_spam` with a
`mention_limit`) block a message whose user/role mention count exceeds the limit. Exempt
roles and channels are honored; rules in a guild without auto-mod have zero effect.

## Bulk message deletion

`TextChannel.purge()` and `delete_messages()` remove up to 100 messages with one
`MESSAGE_DELETE_BULK` event and a bulk-delete audit entry:

```python
channel = env.bot.get_channel(general.id)
await channel.delete_messages(recent_messages)   # 2–100 at once
```

## Reaction clearing

```python
message = await channel.fetch_message(mid)
await message.clear_reactions()        # MESSAGE_REACTION_REMOVE_ALL
await message.clear_reaction("👍")     # MESSAGE_REACTION_REMOVE_EMOJI
```

## Guilds & channels at runtime

The bot can create and edit guilds and channels mid-test, not just the omnipotent
builder — both go through the same backend, so `CHANNEL_CREATE` / `GUILD_UPDATE` reach
the cache:

```python
guild = env.bot.get_guild(env.guild.id)
await guild.create_text_channel("runtime")       # POST /guilds/{id}/channels
await guild.edit(name="Renamed")                  # GUILD_UPDATE, audit-logged
members = [m async for m in guild.fetch_members(limit=None)]
```

Builders still create the channel kinds your test needs up front:

```python
env.guild.create_voice_channel("General Voice", user_limit=10)
env.guild.create_stage_channel("Town Hall")
env.guild.create_category("Community")
env.guild.create_forum_channel("help")
```

## Forum posts

A forum post is a thread with a mandatory starter message:

```python
forum = env.bot.get_channel(help_forum.id)
await forum.edit(available_tags=[discord.ForumTag(name="bug")])
thread, message = await forum.create_thread(name="It crashes", content="stack trace…")
```

## Webhooks

Beyond create + execute, webhooks can be fetched, edited and deleted (by id or token),
and listed per guild:

```python
hook = await channel.create_webhook(name="Announcer")
await hook.edit(name="News")
await hook.delete()
hooks = await guild.webhooks()
```

## Bot, system & webhook message sources

A moderation bot usually branches on *who* sent a message — delete a human's
message, but leave a bot's, an integration's webhook post, or a system notice
alone. SimCord lets a test produce each source faithfully.

A **bot/application account** is just a user created with `bot=True`; messages it
posts arrive with `message.author.bot` set (and no `webhook_id`):

```python
app = env.guild.add_member(env.create_user("ReactionRoles", bot=True))
msg = await app.send(channel, "pick a role")
assert msg.author.bot and msg.webhook_id is None
```

`create_user` seeds the rest of the account surface too — `system=True` for an
official Discord account, `global_name` for a display name distinct from the
username, `discriminator` for legacy tags, and `public_flags` for badges:

```python
verified = env.create_user(
    "GoodBot", bot=True, public_flags=discord.PublicUserFlags(verified_bot=True)
)
```

A **webhook** is a different source: its messages have `author.bot` True *and*
`message.webhook_id` set. Create one on a channel and post through it — this is
the test-driven counterpart to the bot creating and executing a webhook itself:

```python
hook = env.guild.create_webhook(channel, "CI")
msg = await hook.send(embed=discord.Embed(title="Build passed"), username="CI Bot")
assert msg.webhook_id == hook.id and msg.author.bot
assert msg.author.name == "CI Bot"   # per-message username override
```

So a "delete everything except bots and webhooks" rule can be tested against all
three rows at once:

```python
human = await env.guild.add_member(env.create_user("spammer")).send(channel, "spam")
botmsg = await app.send(channel, "ok")
hookmsg = await hook.send("ok")
# human.author.bot is False; botmsg/hookmsg.author.bot is True; only hookmsg has a webhook_id
```

## Guild settings up front

`create_guild` seeds the settings a bot reads off `discord.Guild`, so you don't
have to drive an edit just to arrange state:

```python
guild = env.create_guild(
    "My Server",
    owner=env.create_user("Boss"),          # owners bypass permission checks
    description="a community",
    verification_level=discord.VerificationLevel.high,
    notifications=discord.NotificationLevel.only_mentions,
    content_filter=discord.ContentFilter.all_members,
    preferred_locale="en-GB",
    afk_timeout=900,
)
```

## Stage request-to-speak

```python
await guild.get_member(alice.id).edit(suppress=False)  # invite alice to speak
await guild.me.request_to_speak()                       # the bot raises its hand
```
