"""Bot projections use real discord.py parsers; setup handles remain omniscient."""

import discord
import pytest

from simcord.backend import serializers
from simcord.backend.models import Overwrite

VIEW = discord.Permissions.view_channel.flag


def deny_view(guild_id):
    return [Overwrite(target_id=guild_id, type=0, allow=0, deny=VIEW)]


async def test_hidden_channel_wire_cache_rest_and_canonical_history(env):
    hidden = env.guild.create_text_channel(
        "private-plans",
        topic="private topic",
        overwrites={env.guild.default_role: discord.PermissionOverwrite(view_channel=False)},
    )
    await env.settle()
    cached = env.bot.get_channel(hidden.id)
    assert cached.name == "___hidden___"
    assert cached.topic is None
    assert not cached.permissions_for(cached.guild.default_role).view_channel
    assert hidden.name == "private-plans"
    assert env.backend.get_channel(hidden.id).topic == "private topic"
    assert hidden.id not in {c.id for c in await cached.guild.fetch_channels()}
    with pytest.raises(discord.Forbidden):
        await env.bot.fetch_channel(hidden.id)
    canonical = [
        payload
        for kind, event, payload in env.backend.transcript
        if kind == "GATEWAY" and event == "CHANNEL_CREATE" and payload["id"] == str(hidden.id)
    ]
    assert canonical[-1]["name"] == "private-plans"


async def test_overwrite_reveal_and_revoke_preserve_true_state(env):
    channel = env.guild.create_text_channel("secret", topic="sensitive")
    await env.settle()
    env.backend.edit_channel(channel.id, {}, overwrites=deny_view(env.guild.id))
    await env.settle()
    assert env.bot.get_channel(channel.id).name == "___hidden___"
    env.backend.edit_channel(channel.id, {}, overwrites=[])
    await env.settle()
    assert env.bot.get_channel(channel.id).name == "secret"
    assert env.bot.get_channel(channel.id).topic == "sensitive"
    assert channel.name == "secret"


async def test_role_permission_and_bot_role_membership_flips(env):
    backend = env.backend
    guild = backend.get_guild(env.guild.id)
    bot_id = env.bot.user.id
    bot_role_id = guild.members[bot_id].role_ids[0]
    backend.edit_role(guild.id, guild.id, {"permissions": 0})
    backend.edit_role(guild.id, bot_role_id, {"permissions": 0})
    channel = env.guild.create_text_channel("role-private")
    await env.settle()
    assert env.bot.get_channel(channel.id).name == "___hidden___"
    backend.edit_role(guild.id, bot_role_id, {"permissions": VIEW})
    await env.settle()
    assert env.bot.get_channel(channel.id).name == "role-private"
    backend.edit_member(guild.id, bot_id, {"role_ids": []})
    await env.settle()
    assert env.bot.get_channel(channel.id).name == "___hidden___"
    backend.edit_member(guild.id, bot_id, {"role_ids": [bot_role_id]})
    await env.settle()
    assert env.bot.get_channel(channel.id).name == "role-private"
    backend.edit_role(guild.id, bot_role_id, {"permissions": 0})
    await env.settle()
    assert env.bot.get_channel(channel.id).name == "___hidden___"


async def test_bootstrap_threads_reveal_and_revoke_evict_stale_cache(env):
    parent = env.guild.create_text_channel("thread-parent")
    await env.settle()
    thread = await env.bot.get_channel(parent.id).create_thread(
        name="private discussion",
        type=discord.ChannelType.public_thread,
    )
    await env.settle()
    env.backend.edit_channel(parent.id, {}, overwrites=deny_view(env.guild.id))
    await env.settle()
    assert env.bot.get_channel(thread.id) is None
    # Re-delivery exercises the initial GUILD_CREATE channel/thread projection.
    env.backend.emit(
        "GUILD_CREATE",
        serializers.guild_create_payload(
            env.backend,
            env.backend.get_guild(env.guild.id),
        ),
    )
    await env.settle()
    assert env.bot.get_channel(parent.id).name == "___hidden___"
    assert env.bot.get_channel(thread.id) is None
    env.backend.edit_channel(parent.id, {}, overwrites=[])
    await env.settle()
    assert env.bot.get_channel(thread.id).name == "private discussion"
    env.backend.edit_channel(parent.id, {}, overwrites=deny_view(env.guild.id))
    await env.settle()
    assert env.bot.get_channel(thread.id) is None
    assert env.backend.get_channel(thread.id).name == "private discussion"


async def test_categories_follow_visible_children_and_connected_voice(env):
    backend = env.backend
    category = backend.create_channel(
        env.guild.id,
        "private category",
        type=4,
        overwrites=deny_view(env.guild.id),
    )
    child = backend.create_channel(
        env.guild.id,
        "voice room",
        type=2,
        parent_id=category.id,
        overwrites=deny_view(env.guild.id),
    )
    await env.settle()
    assert env.bot.get_channel(category.id).name == "___hidden___"
    backend.set_voice_state(env.guild.id, env.bot.user.id, child.id)
    await env.settle()
    assert env.bot.get_channel(child.id).name == "voice room"
    assert env.bot.get_channel(category.id).name == "private category"
    listed = {c.id for c in await env.bot.get_guild(env.guild.id).fetch_channels()}
    assert {category.id, child.id} <= listed
    backend.set_voice_state(env.guild.id, env.bot.user.id, None)
    await env.settle()
    assert env.bot.get_channel(child.id).name == "___hidden___"
    assert env.bot.get_channel(category.id).name == "___hidden___"


async def test_bot_creating_self_hidden_channel_has_projected_cache(env):
    guild = env.bot.get_guild(env.guild.id)
    created = await guild.create_text_channel(
        "self-hidden",
        overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)},
    )
    await env.settle()
    assert env.bot.get_channel(created.id).name == "___hidden___"
    assert env.backend.get_channel(created.id).name == "self-hidden"


@pytest.mark.parametrize("initially_visible", [False, True])
async def test_queued_events_keep_emission_time_access(env, alice, initially_visible):
    seen = []

    async def capture(message):
        seen.append(message.content)

    env.bot.add_listener(capture, "on_message")
    guild = env.bot.get_guild(env.guild.id)
    created = await guild.create_text_channel(
        "queued-projection",
        overwrites={guild.me: discord.PermissionOverwrite(view_channel=initially_visible)},
    )
    env.backend.create_message(created.id, alice.id, "before access changed")
    env.backend.set_overwrite(
        created.id,
        Overwrite(
            target_id=env.bot.user.id,
            type=1,
            allow=0 if initially_visible else VIEW,
            deny=VIEW if initially_visible else 0,
        ),
    )
    await env.settle()
    assert seen == (["before access changed"] if initially_visible else [])
    assert env.bot.get_channel(created.id).name == (
        "___hidden___" if initially_visible else "queued-projection"
    )


async def test_hidden_channel_messages_not_dispatched(env):
    seen = []

    async def capture(message):
        seen.append(message)

    env.bot.add_listener(capture, "on_message")
    hidden = env.guild.create_text_channel(
        "secret",
        overwrites={env.guild.default_role: discord.PermissionOverwrite(view_channel=False)},
    )
    backend = env.backend
    backend.create_message(hidden.id, backend.bot_user.id, "sensitive body")
    await env.settle()
    assert seen == []
    assert hidden.last_message.content == "sensitive body"


async def test_rest_listing_requires_membership(env):
    guild = env.bot.get_guild(env.guild.id)
    env.backend.get_guild(guild.id).members.pop(env.bot.user.id)
    with pytest.raises(discord.Forbidden):
        await guild.fetch_channels()


async def test_interaction_resolved_channel_keeps_full_metadata(env, channel, alice):
    hidden = env.guild.create_text_channel(
        "selected-private",
        overwrites={
            env.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            alice: discord.PermissionOverwrite(view_channel=True),
        },
    )
    seen = []

    @env.bot.tree.command(description="Inspect a selected channel")
    async def inspect_private(interaction: discord.Interaction, target: discord.TextChannel):
        seen.append(interaction.data["resolved"]["channels"][str(target.id)]["name"])
        await interaction.response.send_message("inspected", ephemeral=True)

    await env.bot.tree.sync()
    await env.settle()
    assert env.bot.get_channel(hidden.id).name == "___hidden___"
    await alice.slash(channel, "inspect_private", target=hidden)
    assert seen == ["selected-private"]
    assert env.bot.get_channel(hidden.id).name == "___hidden___"


async def test_rest_channel_creation_and_following_edits_preserve_gateway_order(env):
    guild = env.bot.get_guild(env.guild.id)
    created = await guild.create_text_channel("before")
    await created.edit(name="after")
    await env.settle()
    assert env.bot.get_channel(created.id).name == "after"
