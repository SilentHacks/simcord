"""Messages and auto-moderation (auto-mod runs on send, so they live together)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ...components import (
    COMPONENTS_V2_FLAG,
    ComponentValidationError,
    check_attachment_references,
    component_mentions,
    merge_edit_flags,
    validate_components,
    validate_message_fields,
    validate_message_state,
)
from ...enums import ChannelType, MessageType
from .. import errors, permissions, serializers
from ..models import (
    AllowedMentions,
    AutoModRule,
    Channel,
    Interaction,
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


@dataclass(slots=True)
class _PreparedMessageEdit:
    target: Message
    fields: dict[str, Any]
    deferred: Interaction | None


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
        message = self.prepare_message(
            channel_id,
            author_id,
            content,
            embeds=embeds,
            components=components,
            attachments=attachments,
            stickers=stickers,
            flags=flags,
            tts=tts,
            allowed_mentions=allowed_mentions,
            reference=reference,
            interaction_metadata=interaction_metadata,
            webhook_id=webhook_id,
            author_name=author_name,
            author_avatar=author_avatar,
            poll=poll,
            message_type=message_type,
            system_metadata=system_metadata,
        )
        return self.commit_message(message, broadcast=broadcast)

    def prepare_message(
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
    ) -> Message:
        """Validate and derive a detached message without writes or events."""
        channel = self.get_channel(channel_id)
        reference = self.normalize_message_reference(channel_id, reference)
        snapshots: list[dict[str, Any]] = []
        is_forward = reference is not None and reference["type"] == 1
        if is_forward and reference is not None:
            source = self._forward_source(channel_id, author_id, reference)
            source_channel = self.get_channel(source.channel_id)
            if source_channel.guild_id is not None:
                reference["guild_id"] = str(source_channel.guild_id)
            snapshots = [serializers.message_snapshot_payload(self, source)]
            flags |= 1 << 14
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
                require_nonempty=not (is_forward or (flags & (1 << 7) and interaction_metadata is not None)),
            )
        except (ComponentValidationError, TypeError, ValueError) as exc:
            raise _component_error(
                exc if isinstance(exc, ComponentValidationError) else ComponentValidationError(str(exc))
            ) from exc
        reply_author_id = None
        reply_author_interaction = False
        if reference and not is_forward:
            try:
                target = self.get_message(
                    int(reference.get("channel_id", channel.id)), int(reference["message_id"])
                )
            except (KeyError, TypeError, ValueError, errors.BackendError):
                pass
            else:
                reply_author_id = target.author_id
                reply_author_interaction = target.interaction_metadata is not None
        mention_content = f"{content_value}\n{component_mentions(normalized_components)}"
        mention_users, mention_roles, ping_users, ping_roles, mention_everyone = self._mention_state(
            channel, author_id, mention_content, mention_policy, reply_author_id
        )
        return Message(
            id=self.snowflake(),
            channel_id=channel_id,
            author_id=author_id,
            content=content_value,
            timestamp=self.now_iso(),
            type=int(
                message_type
                if message_type is not None
                else (MessageType.REPLY if reference and not is_forward else MessageType.DEFAULT)
            ),
            flags=int(flags),
            tts=tts,
            embeds=deepcopy(embed_value),
            components=normalized_components,
            attachments=deepcopy(attachments or []),
            stickers=list(sticker_value),
            allowed_mentions=mention_policy,
            ping_user_ids=ping_users,
            ping_role_ids=ping_roles,
            mention_user_ids=mention_users,
            mention_role_ids=mention_roles,
            mention_everyone=mention_everyone,
            reference=reference,
            snapshots=snapshots,
            reply_author_id=reply_author_id,
            reply_author_interaction=reply_author_interaction,
            interaction_metadata=deepcopy(interaction_metadata),
            webhook_id=webhook_id,
            author_name=author_name,
            author_avatar=author_avatar,
            poll=poll,
            system_metadata=system_metadata,
        )

    def commit_message(self, message: Message, *, broadcast: bool = True) -> Message:
        """Apply auto-mod, then install a prepared message and publish it."""
        channel = self.get_channel(message.channel_id)
        mention_content = f"{message.content}\n{component_mentions(message.components)}"
        if message.system_metadata is None and self._auto_mod_blocks(
            channel, message.author_id, mention_content
        ):
            return Message(
                id=message.id,
                channel_id=message.channel_id,
                author_id=message.author_id,
                content=message.content,
            )
        previous = self.messages[channel.id].get(message.id)
        if previous is not None:
            self._index_message(previous, remove=True)
        self.messages[channel.id][message.id] = message
        self._index_message(message)
        if message.id not in self._message_order:
            self._message_order[message.id] = self._registration_order()
        self._schedule_poll(message)
        channel.last_message_id = message.id
        if channel.is_thread:
            channel.message_count += 1
        if broadcast:
            self.announce_message_create(message)
        return message

    def normalize_message_reference(
        self, channel_id: int, reference: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if reference is None:
            return None
        if not isinstance(reference, dict) or reference.keys() - {
            "type",
            "channel_id",
            "message_id",
            "guild_id",
            "fail_if_not_exists",
        }:
            raise errors.invalid_form_body("invalid message_reference fields")
        kind = reference.get("type", 0)
        if type(kind) is not int or kind not in (0, 1):
            raise errors.invalid_form_body("message_reference.type must be 0 or 1")
        if "message_id" not in reference or (kind == 1 and "channel_id" not in reference):
            raise errors.invalid_form_body(
                "message_reference requires message_id and forwards require channel_id"
            )
        normalized: dict[str, Any] = {"type": kind, "channel_id": str(channel_id)}
        for key in ("channel_id", "message_id", "guild_id"):
            if key not in reference:
                continue
            value = reference[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (str, int))
                or not str(value).isascii()
                or not str(value).isdecimal()
            ):
                raise errors.invalid_form_body(f"message_reference.{key} must be a snowflake")
            number = int(value)
            if not 0 < number < 1 << 64:
                raise errors.invalid_form_body(f"message_reference.{key} must be a snowflake")
            normalized[key] = str(number)
        fail = reference.get("fail_if_not_exists", True)
        if not isinstance(fail, bool):
            raise errors.invalid_form_body("message_reference.fail_if_not_exists must be a boolean")
        normalized["fail_if_not_exists"] = fail
        if kind == 0 and int(normalized["channel_id"]) != channel_id:
            raise errors.invalid_form_body("replies must reference a message in the same channel")
        return normalized

    def _forward_source(self, channel_id: int, author_id: int, reference: dict[str, Any]) -> Message:
        source_channel = self.get_channel(int(reference["channel_id"]))
        destination = self.get_channel(channel_id)
        if (
            destination.guild_id is None
            and author_id != self.bot_user.id
            and author_id not in destination.recipient_ids
        ):
            raise errors.missing_access()
        if source_channel.guild_id is None:
            if author_id != self.bot_user.id and author_id not in source_channel.recipient_ids:
                raise errors.missing_access()
            if author_id == self.bot_user.id and source_channel.id not in self.dm_channels.values():
                raise errors.missing_access()
        else:
            self.require_permissions(
                source_channel.guild_id, author_id, source_channel.id, "read_message_history"
            )
            if source_channel.type == ChannelType.PRIVATE_THREAD:
                guild = self.get_guild(source_channel.guild_id)
                perms = self.compute_permissions(guild.id, author_id, source_channel.id)
                if author_id not in source_channel.thread_members and not perms & (
                    permissions.flag("administrator") | permissions.flag("manage_threads")
                ):
                    raise errors.missing_access()
        source = self.get_message(source_channel.id, int(reference["message_id"]))
        if not source.visible_to(author_id):
            raise errors.unknown_message()
        if "guild_id" in reference and int(reference["guild_id"]) != source_channel.guild_id:
            raise errors.invalid_form_body("message_reference.guild_id does not match the source channel")
        if (
            source.is_ephemeral
            or source.flags & (1 << 7)
            or source.type not in (0, 19, 20, 23)
            or source.poll is not None
        ):
            raise errors.invalid_form_body("this message cannot be forwarded")
        if source.snapshots:
            raise NotImplementedError(
                "SimCord does not model forwarding an already-forwarded message: "
                "Discord's nested snapshot transformation is not yet supported."
            )
        send = "send_messages_in_threads" if destination.is_thread else "send_messages"
        self.require_permissions(destination.guild_id, author_id, destination.id, send)
        if destination.type == ChannelType.PRIVATE_THREAD:
            assert destination.guild_id is not None
            perms = self.compute_permissions(destination.guild_id, author_id, destination.id)
            if author_id not in destination.thread_members and not perms & (
                permissions.flag("administrator") | permissions.flag("manage_threads")
            ):
                raise errors.missing_access()
        has_attachments = bool(source.attachments)
        if has_attachments:
            self.require_permissions(destination.guild_id, author_id, destination.id, "attach_files")
        return source

    def _mention_state(
        self,
        channel: Channel,
        author_id: int,
        content: str,
        policy: AllowedMentions,
        reply_author_id: int | None,
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
        if reply_author_id is not None and policy.replied_user:
            if reply_author_id not in ping_users:
                ping_users.append(reply_author_id)
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
        content = _SYSTEM_CONTENT[kind]
        if kind == MessageType.CHANNEL_NAME_CHANGE:
            content = (target_channel or channel).name or ""
        elif kind == MessageType.THREAD_CREATED and target_channel is not None:
            content = target_channel.name or ""
        return self.create_message(
            channel_id,
            author_id,
            content,
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
        return self.commit_message_edit(self.prepare_message_edit(channel_id, message_id, fields))

    def prepare_message_edit(
        self,
        channel_id: int,
        message_id: int,
        fields: dict[str, Any],
        *,
        attachment_bindings: list[dict[str, Any]] | None = None,
    ) -> _PreparedMessageEdit:
        """Validate and own incoming edits without changing canonical state."""
        message = self.get_message(channel_id, message_id)
        channel = self.get_channel(channel_id)
        content = fields["content"] if "content" in fields else message.content
        embeds = fields["embeds"] if "embeds" in fields else message.embeds
        components = fields["components"] if "components" in fields else message.components
        attachments = fields["attachments"] if "attachments" in fields else message.attachments
        poll = fields.get("poll", message.poll)
        policy = fields.get("allowed_mentions", message.allowed_mentions)
        try:
            requested_flags = (
                int(fields["flags"]) if "flags" in fields and fields["flags"] is not None else message.flags
            )
        except (TypeError, ValueError) as exc:
            raise errors.invalid_form_body("flags must be an integer") from exc
        try:
            flags = merge_edit_flags(message.flags, requested_flags)
            validate_message_fields(
                [] if components is None else components,
                flags=flags,
                content=content,
                embeds=[] if embeds is None else embeds,
                poll=poll,
                stickers=message.stickers,
                previous_flags=message.flags,
            )
            normalized_components = [] if components is None else components
            if "components" in fields or (flags ^ message.flags) & COMPONENTS_V2_FLAG:
                normalized_components = validate_components(normalized_components, flags=flags)
            if attachment_bindings is not None:
                check_attachment_references(normalized_components, attachment_bindings)
        except (ComponentValidationError, TypeError, ValueError) as exc:
            raise _component_error(
                exc if isinstance(exc, ComponentValidationError) else ComponentValidationError(str(exc))
            ) from exc
        content_value = content or ""
        mention_content = f"{content_value}\n{component_mentions(normalized_components)}"
        mention_users, mention_roles, ping_users, ping_roles, mention_everyone = self._mention_state(
            channel, message.author_id, mention_content, policy, message.reply_author_id
        )
        if "embeds" in fields:
            embeds = deepcopy(embeds)
        if "attachments" in fields:
            attachments = deepcopy(attachments)
        deferred = None
        if message.flags & (1 << 7) and message.interaction_metadata is not None:
            flags &= ~(1 << 7)
            deferred = self.interactions.get(int(message.interaction_metadata["id"]))
        return _PreparedMessageEdit(
            message,
            {
                "content": content_value,
                "embeds": embeds if embeds is not None else [],
                "components": normalized_components,
                "attachments": attachments if attachments is not None else [],
                "poll": poll,
                "flags": flags,
                "allowed_mentions": policy,
                "ping_user_ids": ping_users,
                "ping_role_ids": ping_roles,
                "mention_user_ids": mention_users,
                "mention_role_ids": mention_roles,
                "mention_everyone": mention_everyone,
            },
            deferred,
        )

    def commit_message_edit(self, prepared: _PreparedMessageEdit) -> Message:
        """Apply a prepared edit to the existing message, then publish it."""
        message = prepared.target
        old_id = message.id
        old_keys = self._message_index_keys(message)
        record = prepared.deferred
        if (
            message.flags & (1 << 7)
            and record is not None
            and record.message_id == message.id
            and record.loading
        ):
            record.complete_deferred()
        for name, value in prepared.fields.items():
            setattr(message, name, value)
        message.edited_timestamp = self.now_iso()
        if (message.id, self._message_index_keys(message)) != (old_id, old_keys):
            self._remove_message_index(old_id, old_keys)
            self._index_message(message)
        self._schedule_poll(message)
        if not message.is_ephemeral:
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
        message = self.get_message(channel_id, message_id)
        self._index_message(message, remove=True)
        self._poll_deadlines.set((channel_id, message_id), None, ())
        del self.messages[channel_id][message_id]
        self._message_order.pop(message_id, None)
        channel = self.get_channel(channel_id)
        payload: dict[str, Any] = {"id": str(message_id), "channel_id": str(channel_id)}
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        if not message.is_ephemeral:
            self.emit("MESSAGE_DELETE", payload)

    def bulk_delete_messages(self, channel_id: int, message_ids: Iterable[int]) -> list[int]:
        """Delete several messages at once, announcing a single MESSAGE_DELETE_BULK.

        Ids that are not present are skipped (real Discord tolerates this), and
        only the ids that actually existed are reported and announced — so the
        bot's cache and the returned set agree.
        """
        channel = self.get_channel(channel_id)
        store = self.messages.get(channel_id, {})
        deleted = []
        for mid in message_ids:
            message = store.pop(mid, None)
            if message is not None:
                self._index_message(message, remove=True)
                self._poll_deadlines.set((channel_id, mid), None, ())
                self._message_order.pop(mid, None)
                deleted.append(mid)
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
        self._index_message(message, remove=True)
        message.pinned = pinned
        self._index_message(message)
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
        body = deepcopy(body)
        rule = AutoModRule(
            id=self.snowflake(),
            guild_id=guild_id,
            name=body["name"],
            creator_id=creator_id,
            event_type=int(body["event_type"]),
            trigger_type=int(body["trigger_type"]),
            trigger_metadata=body.get("trigger_metadata") or {},
            actions=body.get("actions") or [],
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
        changes = deepcopy(changes)
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
        keywords = tuple(rule.trigger_metadata.get("keyword_filter") or ())
        if rule._keyword_preparation is None or rule._keyword_preparation[0] != keywords:
            rule._keyword_preparation = (keywords, {})
        preparations = rule._keyword_preparation[1]
        ascii_content = type(content) is str and content.isascii()
        lowered_content: str | None = None
        for position, keyword in enumerate(keywords):
            preparation = preparations.get(position)
            if preparation is None:
                bare = keyword.strip("*")
                if not bare:
                    continue
                prepared_keyword = keyword
                literal = bare.lower() if type(keyword) is str and keyword.isascii() else None
                pattern = None
            else:
                prepared_keyword, literal, pattern = preparation
            # Only exact ASCII strings have equivalent lowercase and IGNORECASE literals.
            if ascii_content and literal is not None:
                if lowered_content is None:
                    lowered_content = content.lower()
                if literal not in lowered_content:
                    if preparation is None:
                        preparations[position] = (prepared_keyword, literal, None)
                    continue
            if pattern is None:
                # Equal-valued subclasses must not redefine a deferred entry's boundaries.
                if preparation is not None:
                    bare = prepared_keyword.strip("*")
                left = "" if prepared_keyword.startswith("*") else r"(?<![A-Za-z0-9])"
                right = "" if prepared_keyword.endswith("*") else r"(?![A-Za-z0-9])"
                pattern = re.compile(f"{left}{re.escape(bare)}{right}", re.IGNORECASE)
                preparations[position] = (prepared_keyword, literal, pattern)
            if pattern.search(content):
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
