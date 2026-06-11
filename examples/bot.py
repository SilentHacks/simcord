"""A small but realistic bot used by the example test suite."""

import discord
from discord import app_commands
from discord.ext import commands


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.command()
    async def ping(ctx: commands.Context) -> None:
        await ctx.send("Pong!")

    @bot.tree.command(description="Ban a member")
    @app_commands.describe(user="Who to ban", reason="Why")
    async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "no reason") -> None:
        if not interaction.permissions.ban_members:
            await interaction.response.send_message("You can't do that.", ephemeral=True)
            return
        await user.ban(reason=reason)
        await interaction.response.send_message(f"Banned {user.mention}: {reason}", ephemeral=True)

    async def setup_hook() -> None:
        await bot.tree.sync()

    bot.setup_hook = setup_hook
    return bot


if __name__ == "__main__":
    import os

    create_bot().run(os.environ["DISCORD_TOKEN"])
