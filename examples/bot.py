"""A small but realistic community bot used by the example test suite.

It deliberately touches one of every interaction surface a real bot uses —
a prefix command, a cooldown, a permission-gated slash command, a modal, a
button confirm flow, and a persistent select menu — so the tests in
``test_bot.py`` show how to drive each one with SimCord.
"""

import time

import discord
from discord import app_commands
from discord.ext import commands

#: Roles the persistent role panel can hand out.
PANEL_ROLES = ("Gamer", "Artist")

#: How long between daily reward claims (seconds).
DAILY_COOLDOWN = 60 * 60 * 24


class FeedbackModal(discord.ui.Modal, title="Feedback"):
    """Collects a name and a comment, then thanks the user."""

    name = discord.ui.TextInput(label="Your name", custom_id="name")
    comment = discord.ui.TextInput(
        label="Comment", custom_id="comment", style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"Thanks {self.name.value}!", ephemeral=True)


class ConfirmView(discord.ui.View):
    """A throwaway confirm/cancel prompt (times out on its own)."""

    def __init__(self) -> None:
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Purged.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Cancelled.")


class RolePanel(discord.ui.View):
    """A persistent self-assign role menu (``timeout=None`` so it survives restarts)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.select(
        custom_id="panel:roles",
        placeholder="Pick your roles",
        min_values=0,
        max_values=len(PANEL_ROLES),
        options=[discord.SelectOption(label=name) for name in PANEL_ROLES],
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        assert interaction.guild is not None
        added = []
        for name in select.values:
            role = discord.utils.get(interaction.guild.roles, name=name)
            if role is not None:
                await interaction.user.add_roles(role)  # type: ignore[union-attr]
                added.append(name)
        await interaction.response.send_message(
            f"Updated roles: {', '.join(added) or 'none'}", ephemeral=True
        )


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.command()
    async def ping(ctx: commands.Context) -> None:
        await ctx.send("Pong!")

    last_claim: dict[int, float] = {}

    @bot.command()
    async def daily(ctx: commands.Context) -> None:
        now = time.monotonic()
        previous = last_claim.get(ctx.author.id)
        if previous is not None and now - previous < DAILY_COOLDOWN:
            await ctx.send("Already claimed — please wait until tomorrow.")
            return
        last_claim[ctx.author.id] = now
        embed = discord.Embed(title="Daily Reward", description="You claimed 100 coins!")
        await ctx.send(embed=embed)

    @bot.command()
    async def panel(ctx: commands.Context) -> None:
        await ctx.send("Pick your roles:", view=RolePanel())

    @bot.tree.command(description="Ban a member")
    @app_commands.describe(user="Who to ban", reason="Why")
    async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "no reason") -> None:
        if not interaction.permissions.ban_members:
            await interaction.response.send_message("You can't do that.", ephemeral=True)
            return
        await user.ban(reason=reason)
        await interaction.response.send_message(f"Banned {user.mention}: {reason}", ephemeral=True)

    @bot.tree.command(description="Share feedback")
    async def feedback(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal())

    @bot.tree.command(description="Delete recent messages")
    async def purge(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Really purge this channel?", view=ConfirmView())

    async def setup_hook() -> None:
        await bot.tree.sync()
        bot.add_view(RolePanel())  # re-attach the persistent view on (re)start

    bot.setup_hook = setup_hook
    return bot


if __name__ == "__main__":
    import os

    create_bot().run(os.environ["DISCORD_TOKEN"])
