"""Channels, threads and stage instances — one mutation cluster."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ...enums import ChannelType
from .. import errors, serializers
from ..models import Channel, Overwrite, StageInstance, ThreadMetadata
from .base import BackendBase


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
        self.messages[channel.id] = {}
        if guild_id is not None:
            guild = self.get_guild(guild_id)
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
        self.messages.pop(channel_id, None)
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
        for attr, value in changes.items():
            setattr(channel, attr, value)
        if overwrites is not None:
            channel.overwrites = overwrites
        if thread_metadata and channel.thread_metadata is not None:
            for attr, value in thread_metadata.items():
                setattr(channel.thread_metadata, attr, value)
        self.announce_channel_update(channel.id)
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
        self.messages.setdefault(thread.id, {})
        guild.thread_ids.append(thread.id)
        payload = dict(serializers.thread_payload(self, thread))
        payload["newly_created"] = True
        self.emit("THREAD_CREATE", payload)
        return thread

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
