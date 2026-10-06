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


class ExpiringView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=180)
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is not None:
            await self.message.edit(content="Offer expired.", view=None)

    @discord.ui.button(label="Claim", custom_id="claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Claimed!", view=None)


class ColorView(discord.ui.View):
    @discord.ui.select(
        custom_id="color",
        options=[discord.SelectOption(label=c, value=c) for c in ("red", "green", "blue")],
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.send_message(f"Picked {select.values[0]}")


class AssignView(discord.ui.View):
    @discord.ui.select(cls=discord.ui.UserSelect, custom_id="who", min_values=1, max_values=2)
    async def who(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        names = ", ".join(u.display_name for u in select.values)
        await interaction.response.send_message(f"Picked {names}")

    @discord.ui.select(cls=discord.ui.RoleSelect, custom_id="role")
    async def role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        await interaction.response.send_message(f"Role {select.values[0].name}")

    @discord.ui.select(cls=discord.ui.ChannelSelect, custom_id="chan")
    async def chan(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        await interaction.response.send_message(f"Channel {select.values[0].name}")


class PersistentView(discord.ui.View):
    """A view with no timeout and a fixed custom_id — re-registered on restart."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Ping", custom_id="persistent:ping")
    async def ping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("pong")


class PickerBusyView(discord.ui.View):
    @discord.ui.button(label="Wait", custom_id="picker:wait")
    async def wait_for_release(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await asyncio.sleep(1.5)
        await interaction.followup.send("Picker action released")


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
        if current == "empty":
            return []
        return [app_commands.Choice(name=t, value=t) for t in TAGS if current in t]

    @app_commands.command(description="Search tags with a bounded result count")
    async def suggest(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 5],
        query: str,
    ) -> None:
        await interaction.response.send_message(f"{query}:{limit}")

    @suggest.autocomplete("query")
    async def suggest_autocomplete(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=t, value=t) for t in TAGS if current in t]

    @app_commands.command(description="Receive an uploaded file")
    async def upload(self, interaction: discord.Interaction, attachment: discord.Attachment) -> None:
        assert isinstance(attachment, discord.Attachment)
        data = await attachment.read()
        await interaction.response.send_message(f"{attachment.filename}:{data.decode()}")

    @app_commands.command(name="upload-bundle", description="Check aggregate attachment limits")
    async def upload_bundle(
        self,
        interaction: discord.Interaction,
        first: discord.Attachment,
        second: discord.Attachment,
        third: discord.Attachment,
    ) -> None:
        await interaction.response.send_message(f"{first.filename}:{second.filename}:{third.filename}")

    @app_commands.command(name="option-check", description="Check slash option constraints")
    async def option_check(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 10],
        label: app_commands.Range[str, 2, 5],
    ) -> None:
        await interaction.response.send_message(f"{label}:{limit}")

    @app_commands.command(description="Slow command that defers")
    async def slow(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send("Done after a while")

    @app_commands.command(name="offer", description="Time-limited offer with a button")
    async def offer(self, interaction: discord.Interaction) -> None:
        view = ExpiringView()
        await interaction.response.send_message("Claim within 3 minutes!", view=view)
        view.message = await interaction.original_response()

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

    @app_commands.command(description="Pick people, a role and a channel")
    async def assign(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pick:", view=AssignView())

    @app_commands.command(description="Post a persistent control panel")
    async def panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Panel", view=PersistentView())

    @app_commands.command(name="picker-busy-panel", description="Post a releasable action for picker tests")
    async def picker_busy_panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Busy control", view=PickerBusyView())

    @app_commands.command(description="Give feedback")
    async def feedback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Interactions())
