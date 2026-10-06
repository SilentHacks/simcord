"""Command-picker fixtures covering Discord's application command options."""

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands


class Picker(commands.Cog):
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

    @app_commands.command(description="Only available in age-restricted channels", nsfw=True)
    async def age_gate(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Age-restricted command")

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
