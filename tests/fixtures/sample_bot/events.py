"""Gateway event listeners."""

import discord
from discord.ext import commands


class Events(commands.Cog):
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # Target a dedicated welcome channel so this global listener doesn't
        # silently inject messages into every test's shared channel.
        channel = discord.utils.get(member.guild.text_channels, name="welcome")
        if channel is not None:
            await channel.send(f"Welcome {member.mention}!")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.abc.User) -> None:
        if not user.bot and str(reaction.emoji) == "👋":
            await reaction.message.channel.send(f"{user.display_name} waved!")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events())
