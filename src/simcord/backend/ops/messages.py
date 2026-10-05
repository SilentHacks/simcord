"""Messages and auto-moderation (auto-mod runs on send, so they live together)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from ...components import ComponentValidationError, component_mentions, validate_message_state
from ...enums import MessageType
from .. import errors, permissions, serializers
from ..models import (
    AllowedMentions,
    AutoModRule,
    Channel,
    Message,
    MessageSticker,
    Poll,
    SystemMessageMetadata,
)
from .base import BackendBase

_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")

_SYSTEM_CONTENT = {
    MessageType.RECIPIENT_ADD: "added a member to the group.",
    MessageType.RECIPIENT_REMOVE: "removed a member from the group.",
    MessageType.CALL: "started a call.",
    MessageType.CHANNEL_NAME_CHANGE: "changed this channel's name.",
    MessageType.CHANNEL_ICON_CHANGE: "changed this channel's icon.",
    MessageType.PINS_ADD: "pinned a message to this channel.",
    MessageType.NEW_MEMBER: "joined the server.",
    MessageType.PREMIUM_GUILD_SUBSCRIPTION: "boosted the server.",
    MessageType.PREMIUM_GUILD_TIER_1: "boosted the server to Level 1.",
    MessageType.PREMIUM_GUILD_TIER_2: "boosted the server to Level 2.",
    MessageType.PREMIUM_GUILD_TIER_3: "boosted the server to Level 3.",
    MessageType.CHANNEL_FOLLOW_ADD: "started following a channel.",
    MessageType.GUILD_STREAM: "started streaming.",
    MessageType.GUILD_DISCOVERY_DISQUALIFIED: "the server is no longer in Discovery.",
    MessageType.GUILD_DISCOVERY_REQUALIFIED: "the server is back in Discovery.",
    MessageType.GUILD_DISCOVERY_GRACE_PERIOD_INITIAL_WARNING: "the server's Discovery status is at risk.",
    MessageType.GUILD_DISCOVERY_GRACE_PERIOD_FINAL_WARNING: "the server may lose Discovery.",
    MessageType.THREAD_CREATED: "started a thread.",
    MessageType.THREAD_STARTER_MESSAGE: "Thread starter message.",
    MessageType.GUILD_INVITE_REMINDER: "sent an invite reminder.",
    MessageType.AUTO_MODERATION_ACTION: "automod took action.",
    MessageType.ROLE_SUBSCRIPTION_PURCHASE: "purchased a role subscription.",
    MessageType.INTERACTION_PREMIUM_UPSELL: "opened a premium upgrade prompt.",
    MessageType.STAGE_START: "started a stage.",
    MessageType.STAGE_END: "ended the stage.",
    MessageType.STAGE_SPEAKER: "became a stage speaker.",
    MessageType.STAGE_RAISE_HAND: "raised a hand in the stage.",
    MessageType.STAGE_TOPIC: "changed the stage topic.",
    MessageType.GUILD_APPLICATION_PREMIUM_SUBSCRIPTION: "subscribed to the application.",
    MessageType.GUILD_INCIDENT_ALERT_MODE_ENABLED: "enabled server alert mode.",
    MessageType.GUILD_INCIDENT_ALERT_MODE_DISABLED: "disabled server alert mode.",
    MessageType.GUILD_INCIDENT_REPORT_RAID: "reported a raid.",
    MessageType.GUILD_INCIDENT_REPORT_FALSE_ALARM: "reported a false alarm.",
    MessageType.PURCHASE_NOTIFICATION: "completed a purchase.",
    MessageType.POLL_RESULT: "poll results are available.",
    MessageType.EMOJI_ADDED: "added a new emoji to the server.",
}


def _component_error(exc: ComponentValidationError) -> errors.BackendError:
    return errors.invalid_form_body(str(exc))


class MessageMixin(BackendBase):
    # -------------------------------------------------------------- messages

    def create_message(
        self,
        channel_id: int,
        author_id: int,
        content: str | None = "",
        *,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        stickers: list[MessageSticker] | None = None,
        flags: int = 0,
        tts: bool = False,
        allowed_mentions: AllowedMentions | None = None,
        reference: dict[str, Any] | None = None,
        interaction_metadata: dict[str, Any] | None = None,
        webhook_id: int | None = None,
        author_name: str | None = None,
        author_avatar: str | None = None,
        poll: Poll | None = None,
        message_type: MessageType | None = None,
        system_metadata: SystemMessageMetadata | None = None,
        broadcast: bool = True,
    ) -> Message:
        channel = self.get_channel(channel_id)
        content_value = content or ""
        embed_value = [] if embeds is None else embeds
        component_value = [] if components is None else components
        sticker_value = [] if stickers is None else stickers
        mention_policy = allowed_mentions or AllowedMentions()
        try:
            normalized_components = validate_message_state(
                component_value,
                flags=int(flags),
                content=content,
                embeds=embed_value,
                poll=poll,
                stickers=sticker_value,
                attachments=attachments,
                require_nonempty=True,
            )
        except (ComponentValidationError, TypeError, ValueError) as exc:
            raise _component_error(
                exc if isinstance(exc, ComponentValidationError) else ComponentValidationError(str(exc))
            ) from exc
        mention_content = f"{content_value}\n{component_mentions(normalized_components)}"
        mention_users, mention_roles, ping_users, ping_roles, mention_everyone = self._mention_state(
            channel, author_id, mention_content, mention_policy, reference
        )
        if system_metadata is None and self._auto_mod_blocks(channel, author_id, mention_content):
            return Message(
                id=self.snowflake(), channel_id=channel_id, author_id=author_id, content=content_value
            )
        message = Message(
            id=self.snowflake(),
            channel_id=channel_id,
            author_id=author_id,
            content=content_value,
            timestamp=self.now_iso(),
            type=int(
                message_type
                if message_type is not None
                else (MessageType.REPLY if reference else MessageType.DEFAULT)
            ),
            flags=int(flags),
            tts=tts,
            embeds=list(embed_value),
            components=normalized_components,
            attachments=list(attachments or []),
            stickers=list(sticker_value),
            allowed_mentions=mention_policy,
            ping_user_ids=ping_users,
            ping_role_ids=ping_roles,
            mention_user_ids=mention_users,
            mention_role_ids=mention_roles,
            mention_everyone=mention_everyone,
            reference=reference,
            interaction_metadata=interaction_metadata,
            webhook_id=webhook_id,
            author_name=author_name,
            author_avatar=author_avatar,
            poll=poll,
            system_metadata=system_metadata,
        )
        self.messages[channel_id][message.id] = message
        channel.last_message_id = message.id
        if channel.is_thread:
            channel.message_count += 1
        if broadcast:
            self.announce_message_create(message)
        return message

    def _mention_state(
        self,
        channel: Channel,
        author_id: int,
        content: str,
        policy: AllowedMentions,
        reference: dict[str, Any] | None,
    ) -> tuple[list[int], list[int], list[int], list[int], bool]:
        users = [int(user_id) for user_id in _USER_MENTION.findall(content)]
        roles = [int(role_id) for role_id in _ROLE_MENTION.findall(content)]
        guild = self.guilds.get(channel.guild_id) if channel.guild_id is not None else None
        permissions_value = (
            self.compute_permissions(channel.guild_id, author_id, channel.id)
            if channel.guild_id is not None
            else 0
        )
        can_mention_all = bool(permissions_value & permissions.flag("mention_everyone"))
        ping_users = list(
            dict.fromkeys(
                user_id for user_id in users if user_id in self.users and policy.allows_user(user_id)
            )
        )
        ping_roles = list(
            dict.fromkeys(
                role_id
                for role_id in roles
                if policy.allows_role(role_id)
                and guild is not None
                and (role := guild.roles.get(role_id)) is not None
                and (role.mentionable or can_mention_all)
            )
        )
        if reference and policy.replied_user:
            try:
                reply_channel = int(reference.get("channel_id", channel.id))
                reply_message = self.get_message(reply_channel, int(reference["message_id"]))
            except (KeyError, TypeError, ValueError, errors.BackendError):
                pass
            else:
                if reply_message.author_id not in ping_users:
                    ping_users.append(reply_message.author_id)
        mention_everyone = (
            policy.everyone and can_mention_all and ("@everyone" in content or "@here" in content)
        )
        return users, roles, ping_users, ping_roles, mention_everyone

    def create_system_message(
        self,
        channel_id: int,
        message_type: MessageType,
        author_id: int,
        *,
        recipient_id: int | None = None,
        target_channel_id: int | None = None,
        referenced_channel_id: int | None = None,
        referenced_message_id: int | None = None,
    ) -> Message:
        try:
            kind = MessageType(message_type)
        except (TypeError, ValueError) as exc:
            raise errors.invalid_form_body("unknown system message type") from exc
        if kind not in _SYSTEM_CONTENT:
            raise errors.invalid_form_body("message type is not a service system message")
        channel = self.get_channel(channel_id)
        self.get_user(author_id)
        if recipient_id is not None:
            self.get_user(recipient_id)
        target_channel = self.get_channel(target_channel_id) if target_channel_id is not None else None
        if target_channel is not None and target_channel.guild_id != channel.guild_id:
            raise errors.invalid_form_body("system message target channel must be in the same guild")
        reference_channel_id = channel_id if referenced_channel_id is None else referenced_channel_id
        if referenced_message_id is not None:
            self.get_message(reference_channel_id, referenced_message_id)
            referenced_channel = self.get_channel(reference_channel_id)
            if referenced_channel.guild_id != channel.guild_id:
                raise errors.invalid_form_body("system message reference must be in the same guild")
        if kind == MessageType.PINS_ADD and referenced_message_id is None:
            raise errors.invalid_form_body("pin notifications require a referenced message")
        if kind == MessageType.THREAD_CREATED and (
            target_channel is None or not target_channel.is_thread or target_channel.parent_id != channel_id
        ):
            raise errors.invalid_form_body("thread notifications require their created thread")
        if kind in {MessageType.RECIPIENT_ADD, MessageType.RECIPIENT_REMOVE} and recipient_id is None:
            raise errors.invalid_form_body("group member notifications require a recipient")
        metadata = SystemMessageMetadata(
            recipient_id=recipient_id,
            channel_id=target_channel_id,
            referenced_channel_id=reference_channel_id if referenced_message_id is not None else None,
            referenced_message_id=referenced_message_id,
        )
        return self.create_message(
            channel_id,
            author_id,
            _SYSTEM_CONTENT[kind],
            message_type=kind,
            system_metadata=metadata,
        )

    def announce_message_create(self, message: Message) -> None:
        channel = self.get_channel(message.channel_id)
        payload = dict(serializers.message_payload(self, message))
        if channel.guild_id is not None:
            guild = self.guilds[channel.guild_id]
            if message.author_id in guild.members:
                payload["member"] = serializers.member_payload(
                    self, guild, guild.members[message.author_id], with_user=False
                )
        self.emit("MESSAGE_CREATE", payload)

    def edit_message(self, channel_id: int, message_id: int, fields: dict[str, Any]) -> Message:
        message = self.get_message(channel_id, message_id)
        channel = self.get_channel(channel_id)
        content = fields["content"] if "content" in fields else message.content
        embeds = fields["embeds"] if "embeds" in fields else message.embeds
        components = fields["components"] if "components" in fields else message.components
        attachments = fields["attachments"] if "attachments" in fields else message.attachments
        policy = fields.get("allowed_mentions", message.allowed_mentions)
        try:
            flags = (
                int(fields["flags"]) if "flags" in fields and fields["flags"] is not None else message.flags
            )
            normalized_components = validate_message_state(
                [] if components is None else components,
                flags=flags,
                content=content,
                embeds=[] if embeds is None else embeds,
                poll=message.poll,
                stickers=message.stickers,
                previous_flags=message.flags,
            )
        except (ComponentValidationError, TypeError, ValueError) as exc:
            raise _component_error(
                exc if isinstance(exc, ComponentValidationError) else ComponentValidationError(str(exc))
            ) from exc
        content_value = content or ""
        mention_content = f"{content_value}\n{component_mentions(normalized_components)}"
        mention_users, mention_roles, ping_users, ping_roles, mention_everyone = self._mention_state(
            channel, message.author_id, mention_content, policy, message.reference
        )
        message.content = content_value
        message.embeds = list(embeds or [])
        message.components = normalized_components
        message.attachments = list(attachments or [])
        message.flags = flags
        message.allowed_mentions = policy
        message.ping_user_ids = ping_users
        message.ping_role_ids = ping_roles
        message.mention_user_ids = mention_users
        message.mention_role_ids = mention_roles
        message.mention_everyone = mention_everyone
        message.edited_timestamp = self.now_iso()
        self.emit("MESSAGE_UPDATE", dict(serializers.message_payload(self, message)))
        return message

    #: discord.MessageFlags.crossposted — set when an announcement is published.
    CROSSPOSTED_FLAG = 1 << 1

    def crosspost_message(self, channel_id: int, message_id: int) -> Message:
        """Publish an announcement message: set the crossposted flag, announce it."""
        message = self.get_message(channel_id, message_id)
        if message.flags & self.CROSSPOSTED_FLAG:
            # Real Discord rejects re-publishing an already-crossposted message.
            raise errors.already_crossposted()
        message.flags |= self.CROSSPOSTED_FLAG
        self.emit("MESSAGE_UPDATE", dict(serializers.message_payload(self, message)))
        return message

    def delete_message(self, channel_id: int, message_id: int) -> None:
        self.get_message(channel_id, message_id)
        del self.messages[channel_id][message_id]
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"id": str(message_id), "channel_id": str(channel_id)}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_DELETE", payload)

    def bulk_delete_messages(self, channel_id: int, message_ids: Iterable[int]) -> list[int]:
        """Delete several messages at once, announcing a single MESSAGE_DELETE_BULK.

        Ids that are not present are skipped (real Discord tolerates this), and
        only the ids that actually existed are reported and announced — so the
        bot's cache and the returned set agree.
        """
        channel = self.get_channel(channel_id)
        store = self.messages.get(channel_id, {})
        deleted = [mid for mid in message_ids if store.pop(mid, None) is not None]
        payload: dict[str, Any] = {
            "ids": [str(mid) for mid in deleted],
            "channel_id": str(channel_id),
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("MESSAGE_DELETE_BULK", payload)
        return deleted

    def set_pinned(
        self, channel_id: int, message_id: int, pinned: bool, *, actor_id: int | None = None
    ) -> None:
        message = self.get_message(channel_id, message_id)
        if message.pinned == pinned:
            return
        message.pinned = pinned
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"channel_id": str(channel_id), "last_pin_timestamp": self.now_iso()}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("CHANNEL_PINS_UPDATE", payload)
        if pinned and channel.guild_id is not None:
            self.create_system_message(
                channel_id,
                MessageType.PINS_ADD,
                self.bot_user.id if actor_id is None else actor_id,
                referenced_message_id=message_id,
            )

    # ------------------------------------------------------- auto-moderation

    def create_auto_mod_rule(self, guild_id: int, creator_id: int, body: Mapping[str, Any]) -> AutoModRule:
        guild = self.get_guild(guild_id)
        rule = AutoModRule(
            id=self.snowflake(),
            guild_id=guild_id,
            name=body["name"],
            creator_id=creator_id,
            event_type=int(body["event_type"]),
            trigger_type=int(body["trigger_type"]),
            trigger_metadata=dict(body.get("trigger_metadata") or {}),
            actions=list(body.get("actions") or []),
            enabled=bool(body.get("enabled", True)),
            exempt_roles=[int(r) for r in body.get("exempt_roles") or []],
            exempt_channels=[int(c) for c in body.get("exempt_channels") or []],
        )
        guild.auto_mod_rules[rule.id] = rule
        self.emit("AUTO_MODERATION_RULE_CREATE", serializers.auto_mod_rule_payload(self, rule))
        return rule

    def get_auto_mod_rule(self, guild_id: int, rule_id: int) -> AutoModRule:
        rule = self.get_guild(guild_id).auto_mod_rules.get(rule_id)
        if rule is None:
            raise errors.unknown_auto_mod_rule()
        return rule

    def edit_auto_mod_rule(self, guild_id: int, rule_id: int, changes: Mapping[str, Any]) -> AutoModRule:
        rule = self.get_auto_mod_rule(guild_id, rule_id)
        for attr, value in changes.items():
            setattr(rule, attr, value)
        self.emit("AUTO_MODERATION_RULE_UPDATE", serializers.auto_mod_rule_payload(self, rule))
        return rule

    def delete_auto_mod_rule(self, guild_id: int, rule_id: int) -> None:
        guild = self.get_guild(guild_id)
        rule = self.get_auto_mod_rule(guild_id, rule_id)
        del guild.auto_mod_rules[rule_id]
        self.emit("AUTO_MODERATION_RULE_DELETE", serializers.auto_mod_rule_payload(self, rule))

    def _auto_mod_blocks(self, channel: Channel, author_id: int, content: str) -> bool:
        """Evaluate enabled keyword rules; emit executions; return whether to block.

        Only non-bot messages in guild channels are evaluated, and only when the
        guild actually has rules — so guilds without auto-mod see no behaviour
        change.
        """
        if channel.guild_id is None or author_id == self.bot_user.id or not content:
            return False
        guild = self.guilds.get(channel.guild_id)
        if guild is None or not guild.auto_mod_rules:
            return False
        member = guild.members.get(author_id)
        role_ids = set(member.role_ids) if member else set()
        blocked = False
        for rule in guild.auto_mod_rules.values():
            if not rule.enabled:
                continue
            if channel.id in rule.exempt_channels or role_ids & set(rule.exempt_roles):
                continue
            matched = self._auto_mod_match(rule, content)
            if matched is None:
                continue
            for action in rule.actions:
                self._emit_auto_mod_execution(rule, channel, author_id, content, matched, action)
                if int(action.get("type", 0)) == 1:  # BLOCK_MESSAGE
                    blocked = True
        return blocked

    def _auto_mod_match(self, rule: AutoModRule, content: str) -> str | None:
        """Whether ``content`` trips ``rule``; returns the matched keyword.

        Keyword rules (trigger_type 1) return the offending keyword; mention-spam
        rules (trigger_type 5) return an empty string (no keyword) when the
        count of unique user+role mentions exceeds ``mention_total_limit``.
        Other trigger types are not evaluated yet.
        """
        if rule.trigger_type == 1:
            return self._match_keyword(rule, content)
        if rule.trigger_type == 5:  # MENTION_SPAM
            limit = rule.trigger_metadata.get("mention_total_limit")
            if limit is None:
                return None
            # Discord counts unique role/user mentions, so repeats don't stack.
            mentions = len(set(_USER_MENTION.findall(content))) + len(set(_ROLE_MENTION.findall(content)))
            return "" if mentions > int(limit) else None
        return None

    @staticmethod
    def _match_keyword(rule: AutoModRule, content: str) -> str | None:
        """Match ``content`` against a keyword filter using Discord's wildcard rules.

        A bare ``word`` matches only as a whole word (bounded by non-alphanumeric
        characters); ``*`` is a wildcard, so ``word*`` is a prefix match, ``*word``
        a suffix match and ``*word*`` a substring match anywhere — mirroring
        Discord's keyword-filter semantics rather than a naive substring search.
        """
        for keyword in rule.trigger_metadata.get("keyword_filter") or []:
            bare = keyword.strip("*")
            if not bare:
                continue
            left = "" if keyword.startswith("*") else r"(?<![A-Za-z0-9])"
            right = "" if keyword.endswith("*") else r"(?![A-Za-z0-9])"
            if re.search(f"{left}{re.escape(bare)}{right}", content, re.IGNORECASE):
                return keyword
        return None

    def _emit_auto_mod_execution(
        self,
        rule: AutoModRule,
        channel: Channel,
        user_id: int,
        content: str,
        keyword: str,
        action: dict[str, Any],
    ) -> None:
        self.emit(
            "AUTO_MODERATION_ACTION_EXECUTION",
            {
                "guild_id": str(channel.guild_id),
                "action": action,
                "rule_id": str(rule.id),
                "rule_trigger_type": rule.trigger_type,
                "user_id": str(user_id),
                "channel_id": str(channel.id),
                "content": content,
                "matched_keyword": keyword or None,
                "matched_content": keyword or None,
            },
        )
