"""Real SDK consumers exercise the fake REST, webhook and interaction transport."""

from typing import Any

import discord
import pytest


async def test_sdk_rest_and_webhook_helpers_share_fake_transport(env: Any, channel: Any) -> None:
    await env.settle()
    sdk_channel = env.bot.get_channel(channel.id)
    hook = await sdk_channel.create_webhook(name="SDK boundary")
    message = await hook.send("original", wait=True)
    assert message.webhook_id == hook.id
    assert (await sdk_channel.fetch_message(message.id)).content == "original"
    assert (await hook.fetch_message(message.id)).content == "original"
    edited = await hook.edit_message(message.id, content="edited")
    assert edited.content == "edited"
    assert env.backend.get_message(channel.id, message.id).content == "edited"
    await hook.delete_message(message.id)
    with pytest.raises(discord.NotFound) as exc:
        await hook.fetch_message(message.id)
    assert exc.value.status == 404
    assert exc.value.code == 10008


async def test_sdk_interaction_helpers_use_initialized_http_session(
    env: Any, channel: Any, alice: Any
) -> None:
    observed: list[str] = []

    @env.bot.tree.command(description="Exercise the SDK compatibility boundary")
    async def boundary(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("original")
        observed.append((await interaction.original_response()).content)
        observed.append((await interaction.edit_original_response(content="edited")).content)
        followup = await interaction.followup.send("followup", wait=True)
        observed.append((await interaction.followup.fetch_message(followup.id)).content)
        await interaction.followup.delete_message(followup.id)
        await interaction.delete_original_response()
        with pytest.raises(discord.NotFound) as exc:
            await interaction.edit_original_response(content="cannot resurrect")
        assert exc.value.code == 10008

    await env.bot.tree.sync()
    await alice.slash(channel, "boundary")
    assert observed == ["original", "edited", "followup"]
