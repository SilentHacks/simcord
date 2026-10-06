"""Command-picker fixtures covering Discord's application command options."""

import asyncio
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

picker_action_started = asyncio.Event()
picker_action_release = asyncio.Event()


class Picker(commands.Cog):
    @app_commands.command(name="private-tag", description="Suggest viewer-specific private tags")
    async def private_tag(self, interaction: discord.Interaction, tag: str) -> None:
        await interaction.response.send_message(tag)

    @private_tag.autocomplete("tag")
    async def private_tag_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(
                name=f"private tag for {interaction.user.name}", value=f"{interaction.user.name}:{current}"
            )
        ]

    @app_commands.command(name="picker-hold", description="Wait for the picker busy test to release")
    async def picker_hold(self, interaction: discord.Interaction) -> None:
        picker_action_started.set()
        await picker_action_release.wait()
        await interaction.response.send_message("Picker hold released")

    @app_commands.command(
        name="picker-upload-mixed", description="Exercise mixed attachment and text keyboard fields"
    )
    async def picker_upload_mixed(
        self, interaction: discord.Interaction, payload: discord.Attachment, label: str
    ) -> None:
        await interaction.response.send_message(f"{payload.filename}:{label}")

    @app_commands.command(description="Exercise common slash option types")
    async def picker_options(
        self,
        interaction: discord.Interaction,
        text: app_commands.Range[str, 2, 20],
        count: app_commands.Range[int, 1, 5],
        ratio: app_commands.Range[float, 0.0, 1.0],
        enabled: bool,
        user: discord.User,
        channel: discord.TextChannel,
        role: discord.Role,
        mentionable: discord.User | discord.Role,
        color: Literal["red", "blue"],
    ) -> None:
        await interaction.response.send_message(
            f"{text}:{count}:{ratio}:{enabled}:{user.name}:{channel.name}:"
            f"{role.name}:{mentionable.name}:{color}"
        )

    @app_commands.command(name="all-optional", description="Exercise optional slash options")
    @app_commands.describe(user="Who to include", confirm="Confirm this action")
    async def all_optional(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        confirm: bool | None = None,
    ) -> None:
        await interaction.response.send_message(f"{user.name if user else 'none'}:{confirm}")

    @app_commands.command(description="Manage guild settings")
    @app_commands.default_permissions(manage_guild=True)
    async def manage_settings(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Settings updated")

    @app_commands.command(description="A command available in guilds and bot DMs")
    @app_commands.allowed_contexts(guilds=True, dms=True)
    async def dm_greeting(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello from the bot", ephemeral=True)

    @app_commands.command(name="dm-member", description="Greet a user in guilds and bot DMs")
    @app_commands.allowed_contexts(guilds=True, dms=True)
    async def dm_member(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.send_message(f"Hello {user.name}")

    @app_commands.command(description="Only available in guilds")
    @app_commands.guild_only()
    async def guild_greeting(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello from the guild")

    @app_commands.command(description="Only available in bot DMs")
    @app_commands.dm_only()
    async def dm_whisper(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello privately")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Picker())
