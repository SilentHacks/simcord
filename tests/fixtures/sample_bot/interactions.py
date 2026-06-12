"""Slash commands exercising groups, autocomplete, deferral, views and modals."""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

TAGS = ["python", "pytest", "asyncio"]


class ConfirmView(discord.ui.View):
    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Deleted all data.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", view=None)


class DeferEditView(discord.ui.View):
    @discord.ui.button(label="Slow Edit", custom_id="slow_edit")
    async def slow_edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # defer() on a component is a deferred *update* (type 6): the later
        # edit_original_response edits this very message in place.
        await interaction.response.defer()
        await interaction.edit_original_response(content="edited in place", view=None)


class ColorView(discord.ui.View):
    @discord.ui.select(
        custom_id="color",
        options=[discord.SelectOption(label=c, value=c) for c in ("red", "green", "blue")],
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.send_message(f"Picked {select.values[0]}")


class FeedbackModal(discord.ui.Modal, title="Feedback"):
    name = discord.ui.TextInput(label="Name", custom_id="name")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"Thanks {self.name.value}")


class Interactions(commands.Cog):
    config = app_commands.Group(name="config", description="Configuration")

    @config.command(description="Set a key")
    async def set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        await interaction.response.send_message(f"{key}={value}")

    @app_commands.command(description="Look up a tag")
    async def tag(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.send_message(f"Tag: {name}")

    @tag.autocomplete("name")
    async def tag_autocomplete(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=t, value=t) for t in TAGS if current in t]

    @app_commands.command(description="Slow command that defers")
    async def slow(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send("Done after a while")

    @app_commands.command(name="paced", description="Pauses before replying")
    async def paced(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await asyncio.sleep(0.2)  # cooldown/backoff-style pause; settle must wait it out
        await interaction.followup.send("paced reply")

    @app_commands.command(name="defer-edit", description="Button that defers then edits in place")
    async def defer_edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("click me", view=DeferEditView())

    @app_commands.command(name="delete-data", description="Delete your data")
    async def delete_data(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Are you sure?", view=ConfirmView())

    @app_commands.command(description="Pick a color")
    async def color(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pick:", view=ColorView())

    @app_commands.command(description="Give feedback")
    async def feedback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Interactions())
