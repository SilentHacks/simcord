"""Write-projection conformance: edit handlers must never silently drop a field.

The route table fails loudly on an unimplemented *route*; this proves the same
guarantee at the *field* level. An unrecognised edit field raises
``UnsupportedField`` (which the transports deliberately do not disguise as an
``HTTPException``, so a broad ``except`` cannot swallow a parity gap), while the
fields a supported area implies are actually wired through to backend state.

Division of labour with ``test_field_honesty_fuzz.py``: the fuzzer sweeps raw
*loudness* (an undeclared key raises) across every honesty-vetted route, so this
file does not re-hand-roll per-route ``dispatch(..., {"made_up": 1})`` cases. What
lives here is what the fuzzer cannot prove — that declared keys reach backend state
(the ``*_fields_apply`` cases), that loudness survives the *real* discord.py client
(not just raw ``dispatch``), and the branches the fuzzer's single-world probe does
not reach (e.g. forum thread create).
"""

from __future__ import annotations

import io

import discord
import pytest

from simcord.enums import ChannelType
from simcord.http import router


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


# --- behaviour beyond loudness (loudness itself is swept by the fuzzer) -------
# Per-route "an undeclared key raises" cases now live in test_field_honesty_fuzz.py
# (bulk-array reorders, permission overwrite, bulk-ban, prune, voice-state and the
# prune include_roles reject reason are all covered there). What remains here is
# behaviour the fuzzer does not assert.


async def test_prune_count_can_be_suppressed(env):
    """compute_prune_count arrives as the string "false"; the count is then
    omitted (returned as None) rather than always computed."""
    suppressed = router.dispatch(
        env.backend,
        "POST",
        f"/guilds/{env.guild.id}/prune",
        json={"days": 7, "compute_prune_count": "false"},
    )
    assert suppressed["pruned"] is None
    counted = router.dispatch(
        env.backend,
        "POST",
        f"/guilds/{env.guild.id}/prune",
        json={"days": 7, "compute_prune_count": "true"},
    )
    assert counted["pruned"] is not None


# --- forum thread create: the branch the fuzzer's single-world probe misses ---
# create_thread has two field contracts (text-channel vs forum); the fuzzer probes
# one world, reaching the text-channel branch, so the forum branch's honesty is
# pinned here directly.


async def test_forum_thread_create_field_honesty(env):
    """The forum branch of thread-create honours name/message/applied_tags and
    fails loudly on anything else, rather than silently dropping it."""
    forum = env.backend.create_channel(env.guild.id, "forum", type=ChannelType.FORUM)

    with pytest.raises(router.UnsupportedField) as exc:
        router.dispatch(
            env.backend,
            "POST",
            f"/channels/{forum.id}/threads",
            json={"name": "post", "message": {"content": "hi"}, "made_up_field": 1},
        )
    assert "made_up_field" in exc.value.fields

    # The declared fields are accepted (no false rejection) and the starter message
    # — itself vetted by bot_message — is created.
    thread = router.dispatch(
        env.backend,
        "POST",
        f"/channels/{forum.id}/threads",
        json={"name": "post", "message": {"content": "hi"}, "applied_tags": []},
    )
    assert thread["message"]["content"] == "hi"
