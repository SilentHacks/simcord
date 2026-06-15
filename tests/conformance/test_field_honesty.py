"""Write-projection conformance: edit handlers must never silently drop a field.

The route table fails loudly on an unimplemented *route*; this proves the same
guarantee at the *field* level. An unrecognised edit field raises
``UnsupportedField`` (which the transports deliberately do not disguise as an
``HTTPException``, so a broad ``except`` cannot swallow a parity gap), while the
fields a supported area implies are actually wired through to backend state.
"""

from __future__ import annotations

import io

import discord
import pytest

from simcord.http import router


async def test_unsupported_edit_field_fails_loudly(env):
    """A body key no handler honours raises UnsupportedField, naming the field."""
    guild = env.bot.get_guild(env.guild.id)
    vc = await guild.create_voice_channel("Voice")
    await env.settle()

    with pytest.raises(router.UnsupportedField) as exc:
        router.dispatch(env.backend, "PATCH", f"/channels/{vc.id}", json={"made_up_field": 1})
    assert "made_up_field" in exc.value.fields


async def test_unsupported_field_is_not_swallowable_as_httpexception(env):
    """The gap must survive a bot's broad ``except discord.HTTPException``."""
    # UnsupportedField is a parity signal, not a Discord error response — so it
    # must not be an HTTPException, exactly like RouteNotImplemented.
    assert not issubclass(router.UnsupportedField, discord.HTTPException)

    guild = env.bot.get_guild(env.guild.id)
    vc = await guild.create_voice_channel("Voice")
    await env.settle()

    # discord.py's VoiceChannel.edit forwards video_quality_mode, which simcord
    # does not model: the edit must raise loudly rather than appear to succeed.
    with pytest.raises(router.UnsupportedField):
        try:
            await vc.edit(video_quality_mode=discord.VideoQualityMode.full)
        except discord.HTTPException:  # pragma: no cover - would mean the gap was swallowed
            pytest.fail("a parity gap was disguised as discord.HTTPException")


async def test_voice_channel_edit_fields_apply(env):
    """Fields a ✅ area implies are wired through, not silently dropped."""
    guild = env.bot.get_guild(env.guild.id)
    vc = await guild.create_voice_channel("Voice")
    await env.settle()

    await vc.edit(bitrate=96000, user_limit=7, rtc_region="us-east")
    await env.settle()

    backend_channel = env.backend.get_channel(vc.id)
    assert backend_channel.bitrate == 96000
    assert backend_channel.user_limit == 7
    assert backend_channel.rtc_region == "us-east"


async def test_text_channel_edit_supported_fields_apply(env, channel):
    """Common TextChannel.edit fields all take effect (none falsely rejected)."""
    dpy_channel = env.bot.get_channel(channel.id)
    await dpy_channel.edit(name="renamed", topic="new topic", nsfw=True, slowmode_delay=10)
    await env.settle()

    backend_channel = env.backend.get_channel(channel.id)
    assert backend_channel.name == "renamed"
    assert backend_channel.topic == "new topic"
    assert backend_channel.nsfw is True
    assert backend_channel.rate_limit_per_user == 10


async def test_role_edit_supported_fields_apply(env):
    """Role.edit's full supported field set is honoured."""
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(name="Mods")
    await env.settle()

    await role.edit(
        name="Admins",
        colour=discord.Colour(0x00FF00),
        hoist=True,
        mentionable=True,
        permissions=discord.Permissions(manage_messages=True),
    )
    await env.settle()

    backend_role = env.backend.get_role(guild.id, role.id)
    assert backend_role.name == "Admins"
    assert backend_role.hoist is True
    assert backend_role.mentionable is True
    assert backend_role.permissions == discord.Permissions(manage_messages=True).value


async def test_webhook_edit_supported_fields_apply(env, channel):
    """Webhook.edit(name=...) is honoured (avatar would reject loudly)."""
    dpy_channel = env.bot.get_channel(channel.id)
    webhook = await dpy_channel.create_webhook(name="hook")
    await env.settle()

    await webhook.edit(name="renamed-hook")
    await env.settle()

    assert env.backend.get_webhook(webhook.id).name == "renamed-hook"


async def test_channel_edit_parent_applies(env, channel):
    """Moving a channel into a category wires parent_id through (coerced to int).

    Only ``parent_id`` reaches this handler: a position-carrying edit routes
    through the (unimplemented) bulk-move endpoint, so it is not exercised here.
    """
    guild = env.bot.get_guild(env.guild.id)
    category = await guild.create_category("Cat")
    dpy_channel = env.bot.get_channel(channel.id)
    await env.settle()

    await dpy_channel.edit(category=category)
    await env.settle()

    backend_channel = env.backend.get_channel(channel.id)
    assert backend_channel.parent_id == category.id
    assert isinstance(backend_channel.parent_id, int)


async def test_guild_edit_channel_pointers_apply(env):
    """Guild.edit's rules/public-updates channel pointers are wired through."""
    guild = env.bot.get_guild(env.guild.id)
    rules = await guild.create_text_channel("rules")
    updates = await guild.create_text_channel("mod-updates")
    await env.settle()

    await guild.edit(rules_channel=rules, public_updates_channel=updates)
    await env.settle()

    backend_guild = env.backend.get_guild(guild.id)
    assert backend_guild.rules_channel_id == rules.id
    assert backend_guild.public_updates_channel_id == updates.id


# --- create-projection honesty: the same guarantee at creation time ----------


async def test_unsupported_create_field_fails_loudly(env):
    """A body key no create handler honours raises UnsupportedField, naming it."""
    with pytest.raises(router.UnsupportedField) as exc:
        router.dispatch(
            env.backend,
            "POST",
            f"/guilds/{env.guild.id}/channels",
            json={"name": "x", "type": 0, "made_up_field": 1},
        )
    assert "made_up_field" in exc.value.fields


async def test_create_voice_channel_unmodelled_field_rejected(env):
    """An unmodelled create field surfaces loudly, not as a swallowable error."""
    guild = env.bot.get_guild(env.guild.id)
    with pytest.raises(router.UnsupportedField):
        try:
            await guild.create_voice_channel("V", video_quality_mode=discord.VideoQualityMode.full)
        except discord.HTTPException:  # pragma: no cover - would mean the gap was swallowed
            pytest.fail("a create parity gap was disguised as discord.HTTPException")


async def test_create_text_channel_supported_fields_apply(env):
    """TextChannel create fields are wired through, not silently dropped."""
    guild = env.bot.get_guild(env.guild.id)
    channel = await guild.create_text_channel("support", topic="help here", nsfw=True, slowmode_delay=5)
    await env.settle()

    backend_channel = env.backend.get_channel(channel.id)
    assert backend_channel.topic == "help here"
    assert backend_channel.nsfw is True
    assert backend_channel.rate_limit_per_user == 5


async def test_create_role_colour_applies(env):
    """Role create honours colour — discord.py sends the gradient ``colors``
    object, so reading the legacy ``color`` key would silently drop it."""
    guild = env.bot.get_guild(env.guild.id)
    role = await guild.create_role(
        name="Coloured",
        colour=discord.Colour(0x00FF00),
        hoist=True,
        permissions=discord.Permissions(manage_messages=True),
    )
    await env.settle()

    backend_role = env.backend.get_role(guild.id, role.id)
    assert backend_role.color == 0x00FF00
    assert backend_role.hoist is True
    assert backend_role.permissions == discord.Permissions(manage_messages=True).value


async def test_create_sticker_multipart_fields_apply(env):
    """Sticker creation is multipart: name/description/tags arrive as scalar form
    parts (reconstructed by ``parse_form``) and must reach backend state."""
    guild = env.bot.get_guild(env.guild.id)
    sticker = await guild.create_sticker(
        name="wave",
        description="a wave",
        emoji="👋",
        file=discord.File(io.BytesIO(b"not-a-real-png"), filename="wave.png"),
    )
    await env.settle()

    backend_sticker = env.backend.get_guild(guild.id).stickers[sticker.id]
    assert backend_sticker.name == "wave"
    assert backend_sticker.description == "a wave"
    assert backend_sticker.tags == "👋"


# --- bulk (JSON-array) bodies: the same honesty for list payloads -----------


async def test_unsupported_bulk_channel_field_fails_loudly(env):
    """The reorder endpoints take a JSON array, not an object — an unrecognised
    per-item key must still surface loudly via ``list_fields``, not be dropped."""
    with pytest.raises(router.UnsupportedField) as exc:
        router.dispatch(
            env.backend,
            "PATCH",
            f"/guilds/{env.guild.id}/channels",
            json=[{"id": "1", "position": 0, "made_up_field": 1}],
        )
    assert "made_up_field" in exc.value.fields


async def test_unsupported_bulk_role_field_fails_loudly(env):
    """Role reordering is array-bodied too; unknown per-item keys fail loudly."""
    everyone = env.guild.id
    with pytest.raises(router.UnsupportedField) as exc:
        router.dispatch(
            env.backend,
            "PATCH",
            f"/guilds/{env.guild.id}/roles",
            json=[{"id": str(everyone), "position": 0, "made_up_field": 1}],
        )
    assert "made_up_field" in exc.value.fields
