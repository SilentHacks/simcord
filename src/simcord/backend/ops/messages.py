"""Messages and auto-moderation (auto-mod runs on send, so they live together)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from ...enums import MessageType
from .. import errors, permissions, serializers
from ..models import AutoModRule, Channel, Message, Poll
from .base import BackendBase

_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")


class MessageMixin(BackendBase):
    # -------------------------------------------------------------- messages

    def create_message(
        self,
        channel_id: int,
        author_id: int,
        content: str = "",
        *,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        flags: int = 0,
        reference: dict[str, Any] | None = None,
        interaction_metadata: dict[str, Any] | None = None,
        webhook_id: int | None = None,
        author_name: str | None = None,
        poll: Poll | None = None,
        broadcast: bool = True,
    ) -> Message:
        channel = self.get_channel(channel_id)
        if content and len(content) > 2000:
            raise errors.invalid_form_body("content must be 2000 or fewer in length")
        if self._auto_mod_blocks(channel, author_id, content or ""):
            # Blocked by an auto-moderation rule: the execution event already
            # fired; build the message object for the caller but never store or
            # broadcast it, so it appears nowhere — exactly as on real Discord.
            return Message(
                id=self.snowflake(), channel_id=channel_id, author_id=author_id, content=content or ""
            )
        message = Message(
            id=self.snowflake(),
            channel_id=channel_id,
            author_id=author_id,
            content=content or "",
            timestamp=self.now_iso(),
            type=MessageType.REPLY if reference else MessageType.DEFAULT,
            flags=flags,
            embeds=embeds or [],
            components=components or [],
            attachments=attachments or [],
            reference=reference,
            interaction_metadata=interaction_metadata,
            webhook_id=webhook_id,
            author_name=author_name,
            poll=poll,
            mention_user_ids=[int(m) for m in _USER_MENTION.findall(content or "")],
            mention_role_ids=[int(m) for m in _ROLE_MENTION.findall(content or "")],
            mention_everyone=self._mentions_everyone(channel, author_id, content or ""),
        )
        self.messages[channel_id][message.id] = message
        channel.last_message_id = message.id
        if channel.is_thread:
            channel.message_count += 1
        if broadcast:
            payload = dict(serializers.message_payload(self, message))
            if channel.guild_id is not None:
                guild = self.guilds[channel.guild_id]
                if author_id in guild.members:
                    payload["member"] = serializers.member_payload(
                        self, guild, guild.members[author_id], with_user=False
                    )
            self.emit("MESSAGE_CREATE", payload)
        return message

    def _mentions_everyone(self, channel: Channel, author_id: int, content: str) -> bool:
        """An @everyone only actually pings if the author may mention everyone."""
        if "@everyone" not in content and "@here" not in content:
            return False
        if channel.guild_id is None:
            return False
        perms = self.compute_permissions(channel.guild_id, author_id, channel.id)
        return bool(perms & permissions.flag("mention_everyone"))

    def edit_message(self, channel_id: int, message_id: int, fields: dict[str, Any]) -> Message:
        message = self.get_message(channel_id, message_id)
        if "content" in fields and fields["content"] is not None:
            message.content = fields["content"]
        for key in ("embeds", "components", "attachments"):
            if key in fields and fields[key] is not None:
                setattr(message, key, fields[key])
        if "flags" in fields and fields["flags"] is not None:
            message.flags = int(fields["flags"])
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

    def set_pinned(self, channel_id: int, message_id: int, pinned: bool) -> None:
        message = self.get_message(channel_id, message_id)
        message.pinned = pinned
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"channel_id": str(channel_id), "last_pin_timestamp": self.now_iso()}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit("CHANNEL_PINS_UPDATE", payload)

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
        count of *unique* user+role mentions exceeds ``mention_total_limit``.
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
