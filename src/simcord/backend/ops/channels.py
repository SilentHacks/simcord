"""Channels, threads and stage instances — one mutation cluster."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields as dataclass_fields
from typing import TYPE_CHECKING, Any, cast

from ...components import ComponentValidationError, check_attachment_references, resolve_attachment_references
from ...enums import ChannelType, MessageType
from .. import errors, serializers
from ..models import Channel, Message, Overwrite, StageInstance, ThreadMetadata
from .base import BackendBase

if TYPE_CHECKING:
    from ..state import Backend


class ChannelMixin(BackendBase):
    # -------------------------------------------------------------- channels

    def create_channel(
        self,
        guild_id: int | None,
        name: str | None,
        *,
        type: int = ChannelType.TEXT,
        overwrites: list[Overwrite] | None = None,
        announce: bool = True,
        **fields: Any,
    ) -> Channel:
        channel = Channel(
            id=self.snowflake(),
            type=type,
            name=name,
            guild_id=guild_id,
            overwrites=overwrites or [],
            **fields,
        )
        self.channels[channel.id] = channel
        self._channel_order[channel.id] = self._registration_order()
        self.messages[channel.id] = {}
        if guild_id is not None:
            guild = self.get_guild(guild_id)
            # A caller may pin an explicit position (create_text_channel(position=));
            # otherwise the channel lands at the end of the existing ordering.
            if "position" not in fields:
                channel.position = len(guild.channel_ids)
            guild.channel_ids.append(channel.id)
            if announce:
                self.emit("CHANNEL_CREATE", serializers.channel_payload(self, channel))
        return channel

    def delete_channel(self, channel_id: int) -> None:
        channel = self.get_channel(channel_id)
        # Deleting a stage channel closes any live stage instance on it (firing
        # STAGE_INSTANCE_DELETE) so it does not outlive its channel.
        if channel_id in self.stage_instances:
            self.delete_stage_instance(channel_id)
        payload = serializers.channel_payload(self, channel)
        del self.channels[channel_id]
        self._drop_channel_messages(channel_id)
        if channel.guild_id is not None:
            guild = self.get_guild(channel.guild_id)
            if channel.id in guild.channel_ids:
                guild.channel_ids.remove(channel.id)
            if channel.id in guild.thread_ids:
                guild.thread_ids.remove(channel.id)
        self.emit("THREAD_DELETE" if channel.is_thread else "CHANNEL_DELETE", dict(payload))

    def announce_channel_update(self, channel_id: int) -> None:
        channel = self.get_channel(channel_id)
        event = "THREAD_UPDATE" if channel.is_thread else "CHANNEL_UPDATE"
        self.emit(event, serializers.channel_payload(self, channel))

    def edit_channel(
        self,
        channel_id: int,
        changes: Mapping[str, Any],
        *,
        overwrites: list[Overwrite] | None = None,
        thread_metadata: Mapping[str, Any] | None = None,
    ) -> Channel:
        """Apply field/overwrite changes to a channel and announce the update."""
        channel = self.get_channel(channel_id)
        allowed = {field.name for field in dataclass_fields(Channel)}
        unsupported = sorted(set(changes) - allowed)
        if unsupported:
            raise ValueError(f"Unsupported channel field(s): {', '.join(unsupported)}")
        old_name = channel.name
        for attr, value in changes.items():
            setattr(channel, attr, value)
        if overwrites is not None:
            channel.overwrites = overwrites
        if thread_metadata and channel.thread_metadata is not None:
            for attr, value in thread_metadata.items():
                setattr(channel.thread_metadata, attr, value)
        self.announce_channel_update(channel.id)
        if (
            "name" in changes
            and old_name != channel.name
            and channel.guild_id is not None
            and not channel.is_thread
        ):
            cast("Backend", self).create_system_message(
                channel.id,
                MessageType.CHANNEL_NAME_CHANGE,
                self.bot_user.id,
                target_channel_id=channel.id,
            )
        return channel

    def reorder_channels(self, guild_id: int, updates: Iterable[Mapping[str, Any]]) -> None:
        """Apply ``{id, position, parent_id?}`` updates from the bulk-move endpoint.

        discord.py routes any position-carrying channel move through
        ``PATCH /guilds/{id}/channels`` (sending the whole sibling ordering), and
        attaches ``parent_id``/``lock_permissions`` to the moved channel.
        ``lock_permissions`` is accepted and ignored — overwrites are unchanged.
        """
        changed: list[Channel] = []
        for item in updates:
            channel = self.channels.get(int(item["id"]))
            if channel is None or channel.guild_id != guild_id:
                continue
            moved = False
            if item.get("position") is not None and channel.position != int(item["position"]):
                channel.position = int(item["position"])
                moved = True
            if "parent_id" in item:
                new_parent = int(item["parent_id"]) if item["parent_id"] is not None else None
                if channel.parent_id != new_parent:
                    channel.parent_id = new_parent
                    moved = True
            if moved:
                changed.append(channel)
        for channel in changed:
            self.announce_channel_update(channel.id)

    # ---------------------------------------------------------- thread members

    def get_thread(self, channel_id: int) -> Channel:
        """Fetch a channel that must be a thread, else fail like real Discord (50024)."""
        channel = self.get_channel(channel_id)
        if not channel.is_thread:
            raise errors.cannot_execute_on_channel_type()
        return channel

    def add_thread_member(self, thread_id: int, user_id: int) -> Channel:
        thread = self.get_thread(thread_id)
        if user_id not in thread.thread_members:
            thread.thread_members[user_id] = self.now_iso()
            payload = dict(serializers.thread_payload(self, thread))
            payload["added_members"] = [serializers.thread_member_payload(thread, user_id)]
            self.emit("THREAD_MEMBERS_UPDATE", payload)
        return thread

    def remove_thread_member(self, thread_id: int, user_id: int) -> Channel:
        thread = self.get_thread(thread_id)
        if thread.thread_members.pop(user_id, None) is not None:
            payload = dict(serializers.thread_payload(self, thread))
            payload["removed_member_ids"] = [str(user_id)]
            self.emit("THREAD_MEMBERS_UPDATE", payload)
        return thread

    def active_threads(self, guild_id: int) -> list[Channel]:
        guild = self.get_guild(guild_id)
        return [
            t
            for tid in guild.thread_ids
            if (t := self.channels.get(tid)) is not None
            and t.thread_metadata is not None
            and not t.thread_metadata.archived
        ]

    def archived_threads(self, channel_id: int, *, private: bool) -> list[Channel]:
        kind = ChannelType.PRIVATE_THREAD if private else ChannelType.PUBLIC_THREAD
        return [
            t
            for t in self.channels.values()
            if t.parent_id == channel_id
            and t.type == kind
            and t.thread_metadata is not None
            and t.thread_metadata.archived
        ]

    def set_overwrite(self, channel_id: int, overwrite: Overwrite) -> None:
        """Add or replace a single permission overwrite and announce the update."""
        channel = self.get_channel(channel_id)
        channel.overwrites = [o for o in channel.overwrites if o.target_id != overwrite.target_id]
        channel.overwrites.append(overwrite)
        self.announce_channel_update(channel.id)

    def delete_overwrite(self, channel_id: int, target_id: int) -> None:
        """Remove a permission overwrite and announce the update."""
        channel = self.get_channel(channel_id)
        channel.overwrites = [o for o in channel.overwrites if o.target_id != target_id]
        self.announce_channel_update(channel.id)

    def get_dm_channel(self, user_id: int) -> Channel:
        self.get_user(user_id)
        channel_id = self.dm_channels.get(user_id)
        if channel_id is not None:
            return self.channels[channel_id]
        channel = self.create_channel(None, None, type=ChannelType.DM)
        channel.recipient_ids = [user_id]
        self.dm_channels[user_id] = channel.id
        return channel

    def create_thread(
        self,
        parent_id: int,
        name: str,
        owner_id: int,
        *,
        type: int = ChannelType.PUBLIC_THREAD,
        auto_archive_duration: int = 1440,
        message_id: int | None = None,
        applied_tags: Iterable[int] = (),
        broadcast: bool = True,
    ) -> Channel:
        parent = self.get_channel(parent_id)
        guild = self.get_guild(parent.guild_id)  # type: ignore[arg-type]
        now = self.now_iso()
        thread = Channel(
            id=message_id or self.snowflake(),
            type=type,
            name=name,
            guild_id=parent.guild_id,
            parent_id=parent_id,
            owner_id=owner_id,
            applied_tags=list(applied_tags),
            thread_metadata=ThreadMetadata(
                auto_archive_duration=auto_archive_duration, archive_timestamp=now, create_timestamp=now
            ),
        )
        thread.thread_members[owner_id] = now
        self.channels[thread.id] = thread
        self._channel_order[thread.id] = self._registration_order()
        self.messages.setdefault(thread.id, {})
        guild.thread_ids.append(thread.id)
        if broadcast:
            self.announce_thread_create(thread)
            cast("Backend", self).create_system_message(
                parent_id,
                MessageType.THREAD_CREATED,
                owner_id,
                target_channel_id=thread.id,
                referenced_message_id=message_id,
            )
        return thread

    def create_forum_post(
        self,
        forum_id: int,
        name: str,
        starter: Message,
        *,
        uploads: Iterable[tuple[str, bytes, str | None]] = (),
        auto_archive_duration: int = 1440,
        applied_tags: Iterable[int] = (),
        broadcast: bool = True,
    ) -> tuple[Channel, Message]:
        """Prepare the mandatory starter and all forum state before publishing."""
        backend = cast("Backend", self)
        forum = self.get_channel(forum_id)
        if forum.type != ChannelType.FORUM or forum.guild_id is None:
            raise errors.cannot_execute_on_channel_type()
        self.get_guild(forum.guild_id)
        if not isinstance(name, str) or not 1 <= len(name) <= 100:
            raise errors.invalid_form_body("name must be between 1 and 100 characters")
        if auto_archive_duration not in (60, 1440, 4320, 10080):
            raise errors.invalid_form_body("Invalid auto_archive_duration")
        tags = list(applied_tags)
        available = {int(tag["id"]) for tag in forum.available_tags if tag.get("id") is not None}
        if len(tags) > 5 or any(tag not in available for tag in tags):
            raise errors.invalid_form_body("applied_tags: Unknown or excessive tags")
        if starter.channel_id != forum_id or starter.author_id != self.bot_user.id:
            raise errors.invalid_form_body("Invalid forum starter")
        # A reply to a message in the parent forum cannot become a reply in a
        # newly created thread. Forward references retain their source channel.
        if starter.reference is not None and starter.reference["type"] == 0:
            raise errors.invalid_form_body("Forum starters cannot be replies")
        if starter.reference is not None and starter.reference["type"] == 1:
            self.require_permissions(forum.guild_id, starter.author_id, forum.id, "send_messages_in_threads")
        upload_list = list(uploads)
        if len(upload_list) != len(starter.attachments) or any(
            not isinstance(filename, str)
            or not isinstance(data, bytes)
            or (description is not None and not isinstance(description, str))
            for filename, data, description in upload_list
        ):
            raise errors.invalid_form_body("Invalid forum uploads")
        try:
            check_attachment_references(starter.components, starter.attachments)
        except (ComponentValidationError, TypeError, ValueError) as exc:
            raise errors.invalid_form_body(str(exc)) from exc

        thread_id = self.snowflake()
        staged = [
            (self.cdn.prepare_attachment(self.snowflake(), thread_id, filename, data, description), data)
            for filename, data, description in upload_list
        ]
        attachments = [attachment for attachment, _ in staged]
        starter.components = resolve_attachment_references(starter.components, attachments, owned=True)
        thread = self.create_thread(
            forum_id,
            name,
            starter.author_id,
            auto_archive_duration=auto_archive_duration,
            applied_tags=tags,
            message_id=thread_id,
            broadcast=False,
        )
        starter.channel_id = thread.id
        starter.attachments = attachments
        self.cdn.install_attachments(staged)
        backend.commit_message(starter, broadcast=False)
        forum.last_message_id = thread.id
        self.announce_thread_create(thread)
        if broadcast:
            backend.announce_message_create(starter)
        return thread, starter

    def announce_thread_create(self, thread: Channel) -> None:
        payload = cast("dict[str, Any]", serializers.channel_payload(self, thread))
        payload["newly_created"] = True
        self.emit("THREAD_CREATE", payload)

    # ------------------------------------------------------- stage instances

    def create_stage_instance(self, channel_id: int, topic: str, *, privacy_level: int = 2) -> StageInstance:
        channel = self.get_channel(channel_id)
        if channel.type != ChannelType.STAGE_VOICE:
            raise errors.cannot_execute_on_channel_type()
        if channel_id in self.stage_instances:
            # One live instance per stage channel; real Discord 400s a second open.
            raise errors.invalid_form_body("A stage instance already exists for this channel")
        instance = StageInstance(
            id=self.snowflake(),
            guild_id=channel.guild_id,  # type: ignore[arg-type]
            channel_id=channel_id,
            topic=topic,
            privacy_level=privacy_level,
        )
        self.stage_instances[channel_id] = instance
        self.emit("STAGE_INSTANCE_CREATE", serializers.stage_instance_payload(instance))
        return instance

    def get_stage_instance(self, channel_id: int) -> StageInstance:
        instance = self.stage_instances.get(channel_id)
        if instance is None:
            raise errors.unknown_channel()
        return instance

    def edit_stage_instance(self, channel_id: int, changes: Mapping[str, Any]) -> StageInstance:
        instance = self.get_stage_instance(channel_id)
        for attr, value in changes.items():
            setattr(instance, attr, value)
        self.emit("STAGE_INSTANCE_UPDATE", serializers.stage_instance_payload(instance))
        return instance

    def delete_stage_instance(self, channel_id: int) -> None:
        instance = self.get_stage_instance(channel_id)
        del self.stage_instances[channel_id]
        self.emit("STAGE_INSTANCE_DELETE", serializers.stage_instance_payload(instance))
