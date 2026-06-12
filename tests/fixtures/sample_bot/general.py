"""Prefix commands exercising converters, replies, pins, DMs and threads."""

import discord
from discord.ext import commands


class General(commands.Cog):
    @commands.command()
    async def ping(self, ctx: commands.Context) -> None:
        await ctx.send("Pong!")

    @commands.command()
    async def whois(self, ctx: commands.Context, member: discord.Member) -> None:
        await ctx.send(f"That is {member.display_name}")

    @commands.command()
    async def pinit(self, ctx: commands.Context) -> None:
        message = await ctx.send("pin me")
        await message.pin()

    @commands.command()
    async def dmme(self, ctx: commands.Context) -> None:
        await ctx.author.send("Here's your DM")

    @commands.command()
    async def thread(self, ctx: commands.Context) -> None:
        created = await ctx.message.create_thread(name="discussion")
        await created.send("Let's talk here")

    @commands.command()
    @commands.cooldown(rate=1, per=60.0, type=commands.BucketType.user)
    async def daily(self, ctx: commands.Context) -> None:
        await ctx.send("Claimed!")

    @commands.command(name="announce-to-locked")
    async def announce(self, ctx: commands.Context, *, text: str) -> None:
        locked = discord.utils.get(ctx.guild.text_channels, name="locked")
        await locked.send(text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General())
