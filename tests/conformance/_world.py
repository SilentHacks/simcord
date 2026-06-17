"""A fully-populated backend world for the honesty fuzzer.

The honesty layer lives in ``RequestContext.fields``/``list_fields`` and is driven
by the synchronous ``router.dispatch(backend, ...)`` entry point — no bot, no event
loop. This builds one ``Backend`` holding one of every addressable resource so a
fuzzer can reach almost every route's ``ctx.fields()`` call with a concrete path,
then resolves a route template (``/channels/{channel_id}``) to a concrete path
against that world.
"""

from __future__ import annotations

from dataclasses import dataclass

from simcord.backend import Backend
from simcord.enums import ChannelType


@dataclass
class World:
    backend: Backend
    gid: int
    member_uid: int
    role: int
    text: int
    voice: int
    stage: int
    news: int
    forum: int
    thread: int
    msg: int
    news_msg: int
    webhook: int
    webhook_token: str
    wh_msg: int
    emoji: int
    app_emoji: int
    sticker: int
    event: int
    rule: int
    invite: str
    app_id: int
    dm_uid: int
    interaction_token: str


def build_world() -> World:
    """Populate a backend with one of every addressable resource."""
    b = Backend()
    guild = b.create_guild("Fuzz Guild")
    gid = guild.id

    # A plain non-owner member the bot outranks (owners bypass permission checks).
    member = b.make_user("member")
    b.add_member(gid, member.id)

    # A role below the bot's managed role so role edits clear the hierarchy check.
    role = b.create_role(gid, "role")

    text = b.create_channel(gid, "general", type=ChannelType.TEXT)
    voice = b.create_channel(gid, "Voice", type=ChannelType.VOICE)
    stage = b.create_channel(gid, "Stage", type=ChannelType.STAGE_VOICE)
    news = b.create_channel(gid, "news", type=ChannelType.NEWS)
    forum = b.create_channel(gid, "forum", type=ChannelType.FORUM)
    thread = b.create_thread(text.id, "thread", b.bot_user.id)

    msg = b.create_message(text.id, b.bot_user.id, "hello")
    news_msg = b.create_message(news.id, b.bot_user.id, "announce")

    webhook = b.create_webhook(text.id, "wh", b.bot_user.id)
    wh_msg = b.create_message(text.id, webhook.webhook_user_id, "via webhook", webhook_id=webhook.id)

    emoji = b.create_emoji(gid, "emo", b.bot_user.id)
    app_emoji = b.create_application_emoji("appemo")
    sticker = b.create_sticker(gid, "stick", b.bot_user.id)

    event = b.create_scheduled_event(
        gid, name="ev", entity_type=2, scheduled_start_time=b.iso_after(3600), channel_id=voice.id
    )
    rule = b.create_auto_mod_rule(
        gid,
        b.bot_user.id,
        {
            "name": "rule",
            "event_type": 1,
            "trigger_type": 1,
            "trigger_metadata": {"keyword_filter": ["spam"]},
            "actions": [],
        },
    )
    invite = b.create_invite(text.id, b.bot_user.id)
    b.create_stage_instance(stage.id, "topic")

    # Put the bot and the member on the stage so voice-state edits have a state.
    b.set_voice_state(gid, b.bot_user.id, stage.id)
    b.set_voice_state(gid, member.id, stage.id)

    dm = b.make_user("dm-target")

    # An interaction with a materialised response message. The webhook-shaped
    # interaction-response routes (``/webhooks/{id}/{token}/messages/...``) key
    # off the interaction token, so this is what lets the fuzzer reach their
    # ``message_edit_changes`` (and thus ``ctx.fields``) call.
    interaction = b.new_interaction(type=2, channel_id=text.id, user_id=member.id, guild_id=gid)
    interaction.respond_with_message(msg.id, ephemeral=False)

    return World(
        backend=b,
        gid=gid,
        member_uid=member.id,
        role=role.id,
        text=text.id,
        voice=voice.id,
        stage=stage.id,
        news=news.id,
        forum=forum.id,
        thread=thread.id,
        msg=msg.id,
        news_msg=news_msg.id,
        webhook=webhook.id,
        webhook_token=webhook.token,
        wh_msg=wh_msg.id,
        emoji=emoji.id,
        app_emoji=app_emoji.id,
        sticker=sticker.id,
        event=event.id,
        rule=rule.id,
        invite=invite.code,
        app_id=b.application_id,
        dm_uid=dm.id,
        interaction_token=interaction.token,
    )


def resolve(template: str, w: World) -> str | None:
    """Resolve a route template to a concrete path against ``w``; None if unmapped."""
    parts: list[str] = []
    for seg in template.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            value = _param(seg[1:-1], template, w)
            if value is None:
                return None
            parts.append(str(value))
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


def _channel_for(template: str, w: World) -> int:
    """Resolve an overloaded ``{channel_id}`` to the channel *type* the route wants.

    The placeholder addresses different channel types depending on the route, so
    the type is read from the template. The fallback is a plain text channel — the
    correct default for the bulk of channel routes. A route whose field contract
    depends on a *non-text* channel type must be registered here explicitly;
    otherwise it would be (silently) probed against the text channel.
    """
    if "/stage-instances/" in template:
        return w.stage
    if "thread-members" in template or "threads/archived" in template:
        return w.thread
    if "/followers" in template or "crosspost" in template:
        return w.news
    return w.text


def _param(name: str, template: str, w: World) -> object | None:
    # 1:1 placeholders: one name, one world resource, no template context needed.
    simple: dict[str, object] = {
        "guild_id": w.gid,
        "user_id": w.member_uid,
        "target_id": w.role,
        "role_id": w.role,
        "webhook_id": w.webhook,
        "rule_id": w.rule,
        "event_id": w.event,
        "sticker_id": w.sticker,
        "code": w.invite,
        "application_id": w.app_id,
        "answer_id": 1,
    }
    if name in simple:
        return simple[name]
    # Overloaded placeholders: the same name addresses different resources by route.
    if name == "channel_id":
        return _channel_for(template, w)
    if name == "message_id":
        return w.news_msg if "crosspost" in template else w.msg
    if name == "token":
        # The ``/webhooks/{id}/{token}/messages/...`` family are interaction-response
        # routes keyed by an interaction token; plain webhook routes use the
        # webhook's own token.
        return w.interaction_token if "/messages/" in template else w.webhook_token
    if name == "emoji_id":
        return w.app_emoji if template.startswith("/applications") else w.emoji
    # An unrecognised placeholder leaves the route unresolved. It is not silently
    # lost: ``test_every_write_route_is_classified`` flags any write route that is
    # neither vetted nor EXEMPT, so a new placeholder type surfaces there.
    return None
