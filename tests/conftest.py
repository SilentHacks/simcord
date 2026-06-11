import discord
import pytest
import pytest_asyncio
from discord import app_commands
from discord.ext import commands

import discord_py_test as dpt


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.command()
    async def ping(ctx: commands.Context) -> None:
        await ctx.send("Pong!")

    @bot.command()
    async def whois(ctx: commands.Context, member: discord.Member) -> None:
        await ctx.send(f"That is {member.display_name}")

    @bot.tree.command(description="Ban a member")
    @app_commands.describe(user="Who to ban", reason="Why")
    async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "no reason") -> None:
        if not interaction.permissions.ban_members:
            await interaction.response.send_message("You can't do that.", ephemeral=True)
            return
        await user.ban(reason=reason)
        await interaction.response.send_message(f"Banned {user.mention}: {reason}", ephemeral=True)

    class ConfirmView(discord.ui.View):
        @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.edit_message(content="Deleted all data.", view=None)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.edit_message(content="Cancelled.", view=None)

    @bot.tree.command(name="delete-data", description="Delete your data")
    async def delete_data(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Are you sure?", view=ConfirmView())

    @bot.tree.command(description="Slow command that defers")
    async def slow(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send("Done after a while")

    @bot.command(name="announce-to-locked")
    async def announce(ctx: commands.Context, *, text: str) -> None:
        locked = discord.utils.get(ctx.guild.text_channels, name="locked")
        await locked.send(text)

    config = app_commands.Group(name="config", description="Configuration")

    @config.command(description="Set a key")
    async def set(interaction: discord.Interaction, key: str, value: str) -> None:
        await interaction.response.send_message(f"{key}={value}")

    bot.tree.add_command(config)

    @bot.tree.context_menu(name="Report")
    async def report(interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.send_message(f"Reported {member.display_name}", ephemeral=True)

    @bot.tree.command(description="Look up a tag")
    async def tag(interaction: discord.Interaction, name: str) -> None:
        await interaction.response.send_message(f"Tag: {name}")

    @tag.autocomplete("name")
    async def tag_autocomplete(interaction: discord.Interaction, current: str):
        tags = ["python", "pytest", "asyncio"]
        return [app_commands.Choice(name=t, value=t) for t in tags if current in t]

    class FeedbackModal(discord.ui.Modal, title="Feedback"):
        name = discord.ui.TextInput(label="Name", custom_id="name")

        async def on_submit(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_message(f"Thanks {self.name.value}")

    @bot.tree.command(description="Give feedback")
    async def feedback(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal())

    class ColorView(discord.ui.View):
        @discord.ui.select(
            custom_id="color",
            options=[discord.SelectOption(label=c, value=c) for c in ("red", "green", "blue")],
        )
        async def pick(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
            await interaction.response.send_message(f"Picked {select.values[0]}")

    @bot.tree.command(description="Pick a color")
    async def color(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pick:", view=ColorView())

    @bot.tree.command(description="Time out a member")
    async def quiet(interaction: discord.Interaction, user: discord.Member) -> None:
        import datetime

        await user.timeout(datetime.timedelta(minutes=10))
        await interaction.response.send_message(f"Timed out {user.display_name}")

    @bot.command()
    async def pinit(ctx: commands.Context) -> None:
        message = await ctx.send("pin me")
        await message.pin()

    @bot.command()
    async def dmme(ctx: commands.Context) -> None:
        await ctx.author.send("Here's your DM")

    @bot.command()
    async def thread(ctx: commands.Context) -> None:
        created = await ctx.message.create_thread(name="discussion")
        await created.send("Let's talk here")

    @bot.listen()
    async def on_member_join(member: discord.Member) -> None:
        channel = discord.utils.get(member.guild.text_channels, name="general")
        if channel is not None:
            await channel.send(f"Welcome {member.mention}!")

    @bot.listen()
    async def on_reaction_add(reaction: discord.Reaction, user: discord.abc.User) -> None:
        if not user.bot and str(reaction.emoji) == "👋":
            await reaction.message.channel.send(f"{user.display_name} waved!")

    async def setup_hook() -> None:
        await bot.tree.sync()

    bot.setup_hook = setup_hook
    return bot


@pytest_asyncio.fixture
async def env():
    bot = create_bot()
    async with dpt.run(bot) as env:
        env.create_guild()
        yield env


@pytest.fixture
def channel(env):
    return env.guild.create_text_channel("general")


@pytest.fixture
def alice(env):
    return env.guild.add_member(env.create_user("alice"))
