"""Guild emojis, application emojis and stickers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .. import errors, serializers
from ..models import GuildEmoji, Sticker
from .base import BackendBase


class ExpressionsMixin(BackendBase):
    # ------------------------------------------------------------- emojis

    def create_emoji(
        self, guild_id: int, name: str, user_id: int, *, animated: bool = False, role_ids: Iterable[int] = ()
    ) -> GuildEmoji:
        guild = self.get_guild(guild_id)
        emoji = GuildEmoji(
            id=self.snowflake(), name=name, user_id=user_id, animated=animated, role_ids=list(role_ids)
        )
        guild.emojis[emoji.id] = emoji
        self._emit_emojis_update(guild_id)
        return emoji

    def get_emoji(self, guild_id: int, emoji_id: int) -> GuildEmoji:
        emoji = self.get_guild(guild_id).emojis.get(emoji_id)
        if emoji is None:
            raise errors.unknown_emoji()
        return emoji

    def edit_emoji(self, guild_id: int, emoji_id: int, changes: Mapping[str, Any]) -> GuildEmoji:
        emoji = self.get_emoji(guild_id, emoji_id)
        for attr, value in changes.items():
            setattr(emoji, attr, value)
        self._emit_emojis_update(guild_id)
        return emoji

    def delete_emoji(self, guild_id: int, emoji_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.get_emoji(guild_id, emoji_id)
        del guild.emojis[emoji_id]
        self._emit_emojis_update(guild_id)

    def _emit_emojis_update(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.emit(
            "GUILD_EMOJIS_UPDATE",
            {
                "guild_id": str(guild_id),
                "emojis": [serializers.guild_emoji_payload(self, e) for e in guild.emojis.values()],
            },
        )

    # --------------------------------------------------- application emojis
    # Application-owned emojis are not scoped to a guild and are not announced
    # over the gateway — they only exist via the REST API a bot polls.

    def create_application_emoji(self, name: str, *, animated: bool = False) -> GuildEmoji:
        emoji = GuildEmoji(id=self.snowflake(), name=name, user_id=self.bot_user.id, animated=animated)
        self.application_emojis[emoji.id] = emoji
        return emoji

    def get_application_emoji(self, emoji_id: int) -> GuildEmoji:
        emoji = self.application_emojis.get(emoji_id)
        if emoji is None:
            raise errors.unknown_emoji()
        return emoji

    def edit_application_emoji(self, emoji_id: int, name: str | None) -> GuildEmoji:
        emoji = self.get_application_emoji(emoji_id)
        if name is not None:  # discord.py omits unchanged fields (e.g. a roles-only edit)
            emoji.name = name
        return emoji

    def delete_application_emoji(self, emoji_id: int) -> None:
        self.get_application_emoji(emoji_id)
        del self.application_emojis[emoji_id]

    # ------------------------------------------------------------- stickers

    def create_sticker(
        self,
        guild_id: int,
        name: str,
        user_id: int,
        *,
        description: str | None = None,
        tags: str = "",
        format_type: int = 1,
        file_data: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> Sticker:
        if isinstance(format_type, bool) or format_type not in {1, 2, 3, 4}:
            raise errors.invalid_form_body("format_type must be a supported sticker format")
        guild = self.get_guild(guild_id)
        sticker_id = self.snowflake()
        sticker = Sticker(
            id=sticker_id,
            name=name,
            guild_id=guild_id,
            user_id=user_id,
            description=description,
            tags=tags,
            format_type=format_type,
            url=self.cdn.store_sticker(sticker_id, format_type, file_data) if file_data is not None else None,
            filename=filename,
            content_type=content_type,
            size=len(file_data) if file_data is not None else 0,
        )
        guild.stickers[sticker.id] = sticker
        self._emit_stickers_update(guild_id)
        return sticker

    def get_sticker(self, guild_id: int, sticker_id: int) -> Sticker:
        sticker = self.get_guild(guild_id).stickers.get(sticker_id)
        if sticker is None:
            raise errors.unknown_sticker()
        return sticker

    def edit_sticker(self, guild_id: int, sticker_id: int, changes: Mapping[str, Any]) -> Sticker:
        sticker = self.get_sticker(guild_id, sticker_id)
        for attr, value in changes.items():
            setattr(sticker, attr, value)
        self._emit_stickers_update(guild_id)
        return sticker

    def delete_sticker(self, guild_id: int, sticker_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.get_sticker(guild_id, sticker_id)
        del guild.stickers[sticker_id]
        self._emit_stickers_update(guild_id)

    def _emit_stickers_update(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        self.emit(
            "GUILD_STICKERS_UPDATE",
            {
                "guild_id": str(guild_id),
                "stickers": [serializers.sticker_payload(self, s) for s in guild.stickers.values()],
            },
        )
