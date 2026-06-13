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

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        # Log to a dedicated channel when present, so this doesn't pollute tests
        # that don't opt in (same pattern as on_member_join above).
        channel = discord.utils.get(member.guild.text_channels, name="voice-log")
        if channel is not None and before.channel is None and after.channel is not None:
            await channel.send(f"{member.display_name} joined {after.channel.name}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events())
