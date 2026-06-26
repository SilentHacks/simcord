from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    id: int
    name: str
    bot: bool = False
    system: bool = False
    #: Display name (discord.py ``User.global_name``); falls back to ``name`` when None.
    global_name: str | None = None
    #: Legacy four-digit tag; "0" for migrated accounts, still set for some bots.
    discriminator: str = "0"
    #: Avatar hash (discord.py builds the CDN asset from it); None means the default avatar.
    avatar: str | None = None
    #: discord.py ``User.public_flags`` bitfield (badges, incl. ``verified_bot``).
    public_flags: int = 0
