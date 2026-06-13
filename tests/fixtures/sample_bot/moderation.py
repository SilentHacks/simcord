"""Moderation slash commands exercising permissions, resolved options and timeouts."""

import datetime

import discord
from discord import app_commands
from discord.ext import commands


class Moderation(commands.Cog):
    @app_commands.command(description="Ban a member")
    @app_commands.describe(user="Who to ban", reason="Why")
    async def ban(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = "no reason"
    ) -> None:
        if not interaction.permissions.ban_members:
            await interaction.response.send_message("You can't do that.", ephemeral=True)
            return
        await user.ban(reason=reason)
        await interaction.response.send_message(f"Banned {user.mention}: {reason}", ephemeral=True)

    @app_commands.command(description="Time out a member")
    async def quiet(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await user.timeout(datetime.timedelta(minutes=10))
        await interaction.response.send_message(f"Timed out {user.display_name}")

    @app_commands.command(name="recent-bans", description="List recently banned users from the audit log")
    async def recent_bans(self, interaction: discord.Interaction) -> None:
        names = [
            entry.target.name
            async for entry in interaction.guild.audit_logs(limit=5, action=discord.AuditLogAction.ban)
        ]
        await interaction.response.send_message(
            "Recent bans: " + (", ".join(names) if names else "none"), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation())
