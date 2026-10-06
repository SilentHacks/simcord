"""Shared Discord reference-gallery factories.

The reference bot and local preview scenarios use these exact discord.py
payload factories. This module intentionally has no capture or runtime
imports; it is checkout fixture data, not part of the SimCord wheel.
"""

from __future__ import annotations

import binascii
import datetime as dt
import io
import struct
import zlib
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

REFERENCE_IDS = (
    "REF-00-INDEX",
    "REF-10-LEGACY-EMBED",
    "REF-11-ATTACHMENTS",
    "REF-20-BUTTONS",
    "REF-30-STRING-SELECT",
    "REF-31-ENTITY-SELECTS",
    "REF-40-V2-LAYOUT-MEDIA",
    "REF-50-MODALS",
)


def register_picker_commands(tree: app_commands.CommandTree) -> None:
    """Register deterministic slash-picker fixtures on a discord.py command tree."""

    @app_commands.command(description="Exercise common slash option types")
    async def picker_options(
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
        interaction: discord.Interaction,
        user: discord.User | None = None,
        confirm: bool | None = None,
    ) -> None:
        await interaction.response.send_message(f"{user.name if user else 'none'}:{confirm}")

    @app_commands.command(name="no-option", description="Run a command with no options")
    async def no_option(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("No-option command ran")

    @app_commands.command(description="Look up a tag")
    async def tag(interaction: discord.Interaction, name: str) -> None:
        await interaction.response.send_message(f"Tag: {name}")

    @tag.autocomplete("name")
    async def tag_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=value, value=value)
            for value in ("Falcon", "Forest", "Fjord")
            if current.casefold() in value.casefold()
        ]

    @app_commands.command(description="Search tags with a bounded result count")
    async def suggest(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 5],
        query: str,
    ) -> None:
        await interaction.response.send_message(f"{query}:{limit}")

    @suggest.autocomplete("query")
    async def suggest_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=value, value=value)
            for value in ("Falcon", "Forest", "Fjord")
            if current.casefold() in value.casefold()
        ]

    @app_commands.command(description="Receive an uploaded file")
    async def upload(interaction: discord.Interaction, attachment: discord.Attachment) -> None:
        await interaction.response.send_message(f"Received {attachment.filename}")

    @app_commands.command(name="option-check", description="Check slash option constraints")
    async def option_check(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 10],
        label: app_commands.Range[str, 2, 5],
    ) -> None:
        await interaction.response.send_message(f"{label}:{limit}")

    config = app_commands.Group(name="config", description="Configuration")

    @config.command(name="set", description="Set a key")
    async def set_key(interaction: discord.Interaction, key: str, value: str) -> None:
        await interaction.response.send_message(f"{key}={value}")

    @config.command(name="remove", description="Remove a key")
    async def remove_key(interaction: discord.Interaction, key: str) -> None:
        await interaction.response.send_message(f"Removed {key}")

    @app_commands.command(name="manage_settings", description="Manage guild settings")
    @app_commands.default_permissions(manage_guild=True)
    async def manage_settings(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Settings updated")

    @app_commands.command(description="A command available in guilds and bot DMs")
    @app_commands.allowed_contexts(guilds=True, dms=True)
    async def dm_greeting(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello from the bot", ephemeral=True)

    @app_commands.command(description="Only available in guilds")
    @app_commands.guild_only()
    async def guild_greeting(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello from the guild")

    @app_commands.command(description="Only available in bot DMs")
    @app_commands.dm_only()
    async def dm_whisper(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello privately")

    for command in (
        picker_options,
        all_optional,
        no_option,
        tag,
        suggest,
        upload,
        option_check,
        manage_settings,
        dm_greeting,
        guild_greeting,
        dm_whisper,
        config,
    ):
        tree.add_command(command)


def _png(
    width: int,
    height: int,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    *,
    compression_level: int = zlib.Z_DEFAULT_COMPRESSION,
) -> bytes:
    """Create a deterministic RGB gradient PNG without another dependency."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))

    rows = bytearray()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        colour = bytes(round(a + (b - a) * ratio) for a, b in zip(top, bottom, strict=True))
        rows.extend(b"\0" + colour * width)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, level=compression_level))
        + chunk(b"IEND", b"")
    )


def _image_file(filename: str, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> discord.File:
    return discord.File(io.BytesIO(_png(640, 360, top, bottom)), filename=filename)


def _fixture_file(filename: str, *, source: str | None = None) -> discord.File:
    data = Path(__file__).with_name(source or filename).read_bytes()
    return discord.File(io.BytesIO(data), filename=filename)


def dogfood_emoji_png() -> bytes:
    return _png(64, 64, (255, 188, 66), (155, 52, 114))


def dogfood_animated_gif() -> bytes:
    return bytes.fromhex(
        "474946383961 0100 0100 80 00 00 ff0000 0000ff "
        "21ff0b 4e45545343415045322e30 0301 0000 00 "
        "21f90400 0a00 0000 2c00000000 0100 0100 00 02 02 4401 00 "
        "21f90400 0a00 0000 2c00000000 0100 0100 00 02 02 4c01 00 3b"
    )


def dogfood_animated_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))

    image_header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    red = zlib.compress(b"\0\xff\0\0")
    blue = zlib.compress(b"\0\0\0\xff")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", image_header)
        + chunk(b"acTL", struct.pack(">II", 2, 0))
        + chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 1, 10, 0, 0))
        + chunk(b"IDAT", red)
        + chunk(b"fcTL", struct.pack(">IIIIIHHBB", 1, 1, 1, 0, 0, 1, 10, 0, 0))
        + chunk(b"fdAT", struct.pack(">I", 2) + blue)
        + chunk(b"IEND", b"")
    )


def dogfood_oversized_spoiler_png() -> bytes:
    return _png(2000, 1800, (30, 80, 140), (210, 120, 40), compression_level=0)


async def _acknowledge(interaction: discord.Interaction) -> None:
    await interaction.response.defer()


class ButtonGallery(discord.ui.View):
    def __init__(self, sku_id: int | None) -> None:
        super().__init__(timeout=None)
        for style, label in (
            (discord.ButtonStyle.primary, "Primary"),
            (discord.ButtonStyle.secondary, "Secondary"),
            (discord.ButtonStyle.success, "Success"),
            (discord.ButtonStyle.danger, "Danger"),
        ):
            button = discord.ui.Button(
                style=style, label=label, custom_id=f"reference:{label.lower()}", row=0
            )
            button.callback = _acknowledge
            self.add_item(button)
        self.add_item(
            discord.ui.Button(style=discord.ButtonStyle.link, label="Link", url="https://discord.com", row=0)
        )
        disabled = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Disabled",
            custom_id="reference:disabled",
            disabled=True,
            row=1,
        )
        disabled.callback = _acknowledge
        self.add_item(disabled)
        emoji = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label="With emoji",
            emoji="✨",
            custom_id="reference:emoji",
            row=1,
        )
        emoji.callback = _acknowledge
        self.add_item(emoji)
        if sku_id is not None:
            self.add_item(discord.ui.Button(sku_id=sku_id, row=1))


class StringSelectGallery(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        select = discord.ui.Select(
            custom_id="reference:string-select",
            placeholder="Choose up to two destinations",
            min_values=0,
            max_values=2,
            options=[
                discord.SelectOption(label="Moon Base", value="moon", description="Low gravity", emoji="🌙"),
                discord.SelectOption(
                    label="Forest Camp",
                    value="forest",
                    description="Default destination",
                    emoji="🌲",
                    default=True,
                ),
                discord.SelectOption(
                    label="Ocean Lab", value="ocean", description="Underwater research", emoji="🌊"
                ),
            ],
            row=0,
        )
        select.callback = _acknowledge
        self.add_item(select)
        disabled = discord.ui.Select(
            custom_id="reference:disabled-select",
            placeholder="Disabled select",
            options=[discord.SelectOption(label="Unavailable", value="unavailable")],
            disabled=True,
            row=1,
        )
        disabled.callback = _acknowledge
        self.add_item(disabled)


class EntitySelectGallery(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        controls = (
            discord.ui.UserSelect(custom_id="reference:user", placeholder="Choose a user", row=0),
            discord.ui.RoleSelect(custom_id="reference:role", placeholder="Choose a role", row=1),
            discord.ui.MentionableSelect(
                custom_id="reference:mentionable", placeholder="Choose a user or role", row=2
            ),
            discord.ui.ChannelSelect(
                custom_id="reference:channel",
                placeholder="Choose a text channel",
                channel_types=[discord.ChannelType.text],
                row=3,
            ),
        )
        for control in controls:
            control.callback = _acknowledge
            self.add_item(control)


class TextModal(discord.ui.Modal, title="REF-51-TEXT-MODAL"):
    note = discord.ui.TextDisplay("Required and optional text fields. Submit empty to reveal validation.")
    name = discord.ui.Label(
        text="Display name",
        description="Use between 3 and 20 characters.",
        component=discord.ui.TextInput(
            custom_id="reference:name", placeholder="Ada Lovelace", min_length=3, max_length=20
        ),
    )
    feedback = discord.ui.Label(
        text="Optional feedback",
        description="Leave empty or enter at least 3 characters.",
        component=discord.ui.TextInput(
            custom_id="reference:feedback",
            style=discord.TextStyle.paragraph,
            placeholder="Tell us what you think…",
            required=False,
            min_length=3,
            max_length=4000,
        ),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "REF-51 submitted; reopen it for more captures.", ephemeral=True
        )


class ChoiceModal(discord.ui.Modal, title="REF-52-CHOICE-MODAL"):
    destination = discord.ui.Label(
        text="Destinations",
        description="Choose one or two.",
        component=discord.ui.Select(
            custom_id="reference:modal-select",
            min_values=1,
            max_values=2,
            options=[
                discord.SelectOption(label="Moon Base", value="moon", description="Low gravity", emoji="🌙"),
                discord.SelectOption(
                    label="Forest Camp", value="forest", description="Among the trees", emoji="🌲"
                ),
                discord.SelectOption(label="Ocean Lab", value="ocean", description="Underwater", emoji="🌊"),
            ],
        ),
    )
    optional_destinations = discord.ui.Label(
        text="Optional destinations",
        description="Choose zero to two.",
        component=discord.ui.Select(
            custom_id="reference:modal-select-optional",
            min_values=0,
            max_values=2,
            required=False,
            options=[
                discord.SelectOption(label="Moon Base", value="moon"),
                discord.SelectOption(label="Forest Camp", value="forest"),
                discord.SelectOption(label="Ocean Lab", value="ocean"),
            ],
        ),
    )
    priority = discord.ui.Label(
        text="Priority",
        component=discord.ui.RadioGroup(
            custom_id="reference:priority",
            options=[
                discord.RadioGroupOption(
                    label="Normal", value="normal", description="Standard handling", default=True
                ),
                discord.RadioGroupOption(
                    label="Urgent", value="urgent", description="Needs prompt attention"
                ),
            ],
        ),
    )
    features = discord.ui.Label(
        text="Features",
        description="Choose zero to two.",
        component=discord.ui.CheckboxGroup(
            custom_id="reference:features",
            required=False,
            min_values=0,
            max_values=2,
            options=[
                discord.CheckboxGroupOption(
                    label="Alerts", value="alerts", description="Receive notifications"
                ),
                discord.CheckboxGroupOption(
                    label="Reports", value="reports", description="Weekly summary", default=True
                ),
                discord.CheckboxGroupOption(label="Exports", value="exports", description="Download data"),
            ],
        ),
    )
    consent = discord.ui.Label(
        text="I understand this is a reference fixture",
        component=discord.ui.Checkbox(custom_id="reference:consent"),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "REF-52 submitted; reopen it for more captures.", ephemeral=True
        )


class StringSelectVariantModal(discord.ui.Modal, title="REF-52-STRING-SELECT-VARIANTS"):
    required_single = discord.ui.Label(
        text="Required single destination",
        component=discord.ui.Select(
            custom_id="reference:modal-select-single",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Moon Base", value="moon"),
                discord.SelectOption(label="Forest Camp", value="forest"),
            ],
        ),
    )
    optional_single = discord.ui.Label(
        text="Optional single destination",
        description="Starts at Moon Base",
        component=discord.ui.Select(
            custom_id="reference:modal-select-single-optional",
            min_values=0,
            max_values=1,
            required=False,
            options=[
                discord.SelectOption(label="Moon Base", value="moon", default=True),
                discord.SelectOption(label="Forest Camp", value="forest"),
            ],
        ),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("REF-52 string-select variants submitted.", ephemeral=True)


class EntityModal(discord.ui.Modal, title="REF-53-ENTITY-MODAL"):
    user = discord.ui.Label(text="User", component=discord.ui.UserSelect(custom_id="reference:modal-user"))
    role = discord.ui.Label(
        text="Role", component=discord.ui.RoleSelect(custom_id="reference:modal-role", required=False)
    )
    mentionable = discord.ui.Label(
        text="Mentionable",
        component=discord.ui.MentionableSelect(custom_id="reference:modal-mentionable", required=False),
    )
    channel = discord.ui.Label(
        text="Channel",
        component=discord.ui.ChannelSelect(
            custom_id="reference:modal-channel", channel_types=[discord.ChannelType.text], required=False
        ),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "REF-53 submitted; reopen it for more captures.", ephemeral=True
        )


class DogfoodEntityDefaultsModal(discord.ui.Modal, title="DOG-D01-GUILD-ENTITY-DEFAULTS"):
    def __init__(self, guild: discord.Guild, viewer_id: int) -> None:
        super().__init__()
        viewer = guild.get_member(viewer_id)
        default_member = next(
            (candidate for candidate in guild.members if candidate.name == "candidate-29"),
            None,
        )
        role = next((item for item in guild.roles if item != guild.default_role), None)
        channel = next(iter(guild.text_channels), None)
        if viewer is None or default_member is None or role is None or channel is None:
            raise ValueError(
                "the D01 guild modal requires the viewer, candidate-29, a non-default role, and a text channel"
            )

        user_default = discord.SelectDefaultValue(
            id=default_member.id, type=discord.SelectDefaultValueType.user
        )
        role_default = discord.SelectDefaultValue(id=role.id, type=discord.SelectDefaultValueType.role)
        channel_default = discord.SelectDefaultValue(
            id=channel.id, type=discord.SelectDefaultValueType.channel
        )
        self.add_item(
            discord.ui.Label(
                text="Required user (candidate-29 default)",
                component=discord.ui.UserSelect(
                    custom_id="dogfood:d01-guild-user",
                    required=True,
                    default_values=[user_default],
                ),
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Optional role",
                component=discord.ui.RoleSelect(
                    custom_id="dogfood:d01-guild-role",
                    required=False,
                    default_values=[role_default],
                ),
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Optional user and role",
                component=discord.ui.MentionableSelect(
                    custom_id="dogfood:d01-guild-mentionable",
                    max_values=2,
                    required=False,
                    default_values=[user_default, role_default],
                ),
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Optional channel",
                component=discord.ui.ChannelSelect(
                    custom_id="dogfood:d01-guild-channel",
                    channel_types=[discord.ChannelType.text],
                    required=False,
                    default_values=[channel_default],
                ),
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("D01 guild entity modal submitted.", ephemeral=True)


class DogfoodDMEntityDefaultsModal(discord.ui.Modal, title="DOG-D01-DM-USER-DEFAULTS"):
    def __init__(self, viewer_id: int) -> None:
        super().__init__()
        default = discord.SelectDefaultValue(id=viewer_id, type=discord.SelectDefaultValueType.user)
        self.add_item(
            discord.ui.Label(
                text="Required user",
                component=discord.ui.UserSelect(
                    custom_id="dogfood:d01-dm-user",
                    required=True,
                    default_values=[default],
                ),
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Optional user",
                component=discord.ui.UserSelect(
                    custom_id="dogfood:d01-dm-optional-user",
                    required=False,
                ),
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("D01 DM user modal submitted.", ephemeral=True)


class DogfoodModalGallery(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Guild entity defaults", style=discord.ButtonStyle.primary, custom_id="dogfood:d01-guild"
    )
    async def guild_entities(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Open the guild D01 scenario.", ephemeral=True)
            return
        await interaction.response.send_modal(
            DogfoodEntityDefaultsModal(interaction.guild, interaction.user.id)
        )


class DogfoodDMModalGallery(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="DM user defaults", style=discord.ButtonStyle.primary, custom_id="dogfood:d01-dm"
    )
    async def dm_entities(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(DogfoodDMEntityDefaultsModal(interaction.user.id))


class UploadModal(discord.ui.Modal, title="REF-54-UPLOAD-MODAL"):
    upload = discord.ui.Label(
        text="Reference files",
        description="Choose between one and three files.",
        component=discord.ui.FileUpload(custom_id="reference:files", min_values=1, max_values=3),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "REF-54 submitted; reopen it for more captures.", ephemeral=True
        )


class ModalGallery(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open text modal", style=discord.ButtonStyle.primary, custom_id="reference:open-text"
    )
    async def text(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TextModal())

    @discord.ui.button(
        label="Open choice modal", style=discord.ButtonStyle.secondary, custom_id="reference:open-choice"
    )
    async def choices(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ChoiceModal())

    @discord.ui.button(
        label="Open single-select variants",
        style=discord.ButtonStyle.secondary,
        custom_id="reference:open-single-select-variants",
    )
    async def single_select_variants(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(StringSelectVariantModal())

    @discord.ui.button(
        label="Open entity modal", style=discord.ButtonStyle.secondary, custom_id="reference:open-entity"
    )
    async def entities(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EntityModal())

    @discord.ui.button(
        label="Open upload modal", style=discord.ButtonStyle.secondary, custom_id="reference:open-upload"
    )
    async def upload(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(UploadModal())


def _embed() -> discord.Embed:
    embed = discord.Embed(
        title="REF-10 Mixed-field expedition",
        url="https://discord.com/developers/docs",
        description="A **bold** description with `inline code`, a [safe link](https://discord.com), and a long line for wrapping calibration.",
        colour=discord.Colour.blurple(),
        timestamp=dt.datetime(2026, 1, 2, 15, 4, 5, tzinfo=dt.UTC),
    )
    embed.set_author(name="Reference Bot")
    embed.add_field(name="Temperature", value="21 °C", inline=True)
    embed.add_field(name="Pressure", value="101.3 kPa", inline=True)
    embed.add_field(name="Status", value="Nominal", inline=True)
    embed.add_field(
        name="Mission notes",
        value="This non-inline field follows a complete inline row and contains enough text to wrap at narrow widths.",
        inline=False,
    )
    embed.set_thumbnail(url="attachment://ref-thumbnail.png")
    embed.set_image(url="attachment://ref-hero.png")
    embed.set_footer(text="Deterministic footer")
    return embed


def _layout_view() -> tuple[discord.ui.LayoutView, list[discord.File]]:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.TextDisplay(
            "## REF-40-V2-LAYOUT-MEDIA\nCapture the complete message and each interactive state.\n"
            "DOG-V2-01 Public gallery beside a gallery in the spoiler container."
        )
    )
    accessory = discord.ui.Button(
        label="Accessory", style=discord.ButtonStyle.primary, custom_id="reference:accessory"
    )
    accessory.callback = _acknowledge
    row_button = discord.ui.Button(
        label="Nested action", style=discord.ButtonStyle.secondary, custom_id="reference:nested"
    )
    row_button.callback = _acknowledge
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                "### Expedition status\nA section, separators, wrapping text, and an accent container."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.Section(
                "**Section heading**\nThe accessory stays aligned while this text wraps.", accessory=accessory
            ),
            discord.ui.ActionRow(row_button),
            discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large),
            accent_colour=discord.Colour.blurple(),
        )
    )
    view.add_item(
        discord.ui.Section(
            "**Thumbnail accessory**\nIncludes description and spoiler presentation.",
            accessory=discord.ui.Thumbnail(
                "attachment://ref-v2-thumbnail.png",
                description="Blue-to-purple reference gradient",
                spoiler=True,
            ),
        )
    )
    view.add_item(
        discord.ui.MediaGallery(
            discord.MediaGalleryItem(
                "attachment://ref-gallery-wide.png",
                description="DOG-V2-01 Public gallery image",
            ),
            discord.MediaGalleryItem(
                "attachment://ref-gallery-spoiler.png",
                description="Spoiler orange reference image",
                spoiler=True,
            ),
        )
    )
    view.add_item(discord.ui.File("attachment://ref-document.txt"))
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                "REF-41-CONTAINER-SPOILER\nDOG-V2-01 Hidden gallery; reveal this container."
            ),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    "attachment://ref-gallery-container-hidden.png",
                    description="DOG-V2-01 Image inside spoiler container",
                )
            ),
            spoiler=True,
        )
    )
    files = [
        _image_file("ref-v2-thumbnail.png", (88, 101, 242), (70, 35, 110)),
        _image_file("ref-gallery-wide.png", (0, 170, 255), (0, 55, 110)),
        _image_file("ref-gallery-spoiler.png", (255, 170, 0), (145, 45, 20)),
        _image_file("ref-gallery-container-hidden.png", (130, 70, 210), (20, 40, 70)),
        discord.File(io.BytesIO(b"SimCord visual reference fixture\n"), filename="ref-document.txt"),
    ]
    return view, files


def gallery_payload(
    reference_id: str,
    *,
    viewer_mention: str = "@simcord-viewer",
    sku_id: int | None = None,
) -> dict[str, object]:
    """Build one labelled message for both the official bot and local capture worlds."""
    if reference_id == "REF-00-INDEX":
        return {
            "content": (
                "**REF-00-INDEX — visual reference gallery**\n"
                "Keep browser zoom at 100% and use the agreed capture profile."
            )
        }
    if reference_id == "REF-10-LEGACY-EMBED":
        return {
            "content": (
                f"**REF-10-LEGACY-EMBED**\nReference viewer: {viewer_mention}\n"
                "Markdown: **bold**, *italic*, __underline__, ~~strike~~, ||spoiler||, "
                "`inline code`, and a very-long-token-for-wrap-testing-0123456789."
            ),
            "embed": _embed(),
            "files": [
                _image_file("ref-thumbnail.png", (88, 101, 242), (35, 39, 42)),
                _image_file("ref-hero.png", (35, 165, 90), (20, 80, 130)),
            ],
        }
    if reference_id == "REF-11-ATTACHMENTS":
        return {
            "content": (
                "**REF-11-ATTACHMENTS**\nCapture the inline image, file tile, filename "
                "wrapping, sizes, and download controls."
            ),
            "files": [
                _image_file("ref-inline-image-with-a-long-name.png", (210, 70, 90), (65, 25, 90)),
                discord.File(
                    io.BytesIO(b"Standalone legacy attachment reference\n"),
                    filename="ref-standalone-document-with-a-long-name.txt",
                ),
            ],
        }
    if reference_id == "REF-20-BUTTONS":
        return {
            "content": (
                "**REF-20-BUTTONS**\nCapture idle, hover, pressed, keyboard focus, and disabled "
                "states." + (" Included: active premium SKU." if sku_id is not None else "")
            ),
            "view": ButtonGallery(sku_id),
        }
    if reference_id == "REF-30-STRING-SELECT":
        return {
            "content": (
                "**REF-30-STRING-SELECT**\nCapture closed, open, hover, keyboard focus, "
                "one/two selected, cleared, and disabled states."
            ),
            "view": StringSelectGallery(),
        }
    if reference_id == "REF-31-ENTITY-SELECTS":
        return {
            "content": (
                "**REF-31-ENTITY-SELECTS**\nOpen each menu and capture its candidate "
                "decoration, selection, and keyboard focus."
            ),
            "view": EntitySelectGallery(),
        }
    if reference_id == "REF-40-V2-LAYOUT-MEDIA":
        view, files = _layout_view()
        return {"view": view, "files": files}
    if reference_id == "REF-50-MODALS":
        return {
            "content": (
                "**REF-50-MODALS**\nOpen each modal. Capture empty, focus, filled, validation, "
                "selection/upload, and button-focus states."
            ),
            "view": ModalGallery(),
        }
    raise ValueError(f"unknown reference fixture {reference_id!r}")


def close_payload(payload: dict[str, object]) -> None:
    """Close files owned by a payload after a local world has settled."""
    for value in payload.get("files", ()):
        if isinstance(value, discord.File):
            value.close()


class DogfoodChoices(discord.ui.View):
    def __init__(
        self,
        *,
        custom_id: str,
        minimum: int = 1,
        maximum: int = 1,
        default_first: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        select = discord.ui.Select(
            custom_id=custom_id,
            placeholder="Choose deployment regions",
            min_values=minimum,
            max_values=maximum,
            options=[
                discord.SelectOption(
                    label=f"Region {number}",
                    value=str(number),
                    description=f"Deployment destination {number}",
                    default=default_first and number == 0,
                )
                for number in range(25)
            ],
        )

        async def picked(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send(f"Selected: {select.values}", ephemeral=True)

        select.callback = picked
        self.add_item(select)


class DogfoodDisabledEntityDefaultView(discord.ui.View):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=None)
        select = discord.ui.UserSelect(
            custom_id="dogfood:disabled-entity-default",
            placeholder="Disabled UserSelect with Alice's authorized default",
            min_values=0,
            max_values=1,
            disabled=True,
            default_values=[discord.SelectDefaultValue(id=user_id, type=discord.SelectDefaultValueType.user)],
        )
        select.callback = _acknowledge
        self.add_item(select)


class DogfoodLongMessageView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="DOG-05 final control",
        style=discord.ButtonStyle.primary,
        custom_id="dogfood:long:tail",
    )
    async def tail(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("DOG-05 final control reached.", ephemeral=True)


class DogfoodCallbackView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="DOG-CB-01 Raise callback failure",
        style=discord.ButtonStyle.danger,
        custom_id="dogfood:callback-failure",
    )
    async def fail(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        raise RuntimeError("DOG-CB-01 intentional callback failure")


class DogfoodDeferredView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="DOG-CB-02 Defer and follow up",
        style=discord.ButtonStyle.primary,
        custom_id="dogfood:deferred-followup",
    )
    async def deferred_followup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("DOG-CB-02 deferred follow-up completed.", ephemeral=True)


DOGFOOD_SCENARIOS = (
    {
        "id": "D01",
        "scenario": "dogfood.modal-entity-guild-dm",
        "title": "Modal entity candidates, defaults, and access scope",
        "action": (
            "Open DOG-D01 Guild entity defaults and inspect User, Role, Mentionable, and Channel; "
            "Submit defaults and alternates; verify candidate-29 can be defaulted independently of the visible candidate page. "
            "Switch viewers and attempt the unsupported voice channel, denied channel, and other-guild candidate."
        ),
        "outcome": (
            "Each supported family has independently resolved defaults and authorized candidates; "
            "required/optional values submit, while out-of-scope candidates stay unavailable."
        ),
        "controls": [
            "dogfood:d01-guild",
            "dogfood:d01-guild-user",
            "dogfood:d01-guild-role",
            "dogfood:d01-guild-mentionable",
            "dogfood:d01-guild-channel",
            "dogfood:d01-dm",
            "dogfood:d01-dm-user",
            "dogfood:d01-dm-optional-user",
        ],
    },
    {
        "id": "D02",
        "scenario": "dogfood.focused-content-geometry",
        "title": "Focused-message scroll and visible crop",
        "action": "Open DOG-05 and REF-40 in message layout; reach the final line/control without changing height.",
        "outcome": "All content remains reachable in the owned viewport; captures describe the visible crop.",
        "controls": ["dogfood:long:tail"],
    },
    {
        "id": "D03",
        "scenario": "dogfood.actual-render-geometry",
        "title": "Actual render dimensions",
        "action": "Compare requested profile dimensions with the measured rendered viewport beside the inspector.",
        "outcome": "Requested profile, actual rendered viewport, and host availability are reported separately.",
    },
    {
        "id": "D04",
        "scenario": "dogfood.responsive-fit",
        "title": "Responsive first-open fit",
        "action": "Open at 360x640 and 800x600; reach toolbar, preview, modal Submit, and inspector without profile changes.",
        "outcome": "The labeled responsive page fits reachable controls without scaling an exact capture profile.",
    },
    {
        "id": "D05",
        "scenario": "dogfood.unicode-font-surfaces",
        "title": "Unicode glyph surfaces",
        "action": "Inspect DOG-01 text and the REF-20 button, select option, selected value, and labels with emoji sequences.",
        "outcome": "Actual glyph fallback remains readable and native shaping/variation sequences stay intact.",
    },
    {
        "id": "D06",
        "scenario": "dogfood.safe-bare-links",
        "title": "Safe bare URL linkification",
        "action": "Compare DOG-01 masked and bare HTTP(S) links with code, escaped text, and unsafe-scheme negatives.",
        "outcome": "Only safe absolute HTTP(S) links outside literal/code contexts become navigable; nothing is fetched.",
    },
    {
        "id": "D07",
        "scenario": "dogfood.keyboard-focus-zoom-contrast",
        "title": "Keyboard focus, zoom, and forced colors",
        "action": "Tab through toolbar, inspector, DOG-02, and REF-20 at increased zoom and forced colors.",
        "outcome": "Every keyboard control retains a visible, legible focus indicator.",
    },
    {
        "id": "D08",
        "scenario": "dogfood.single-poll-failure-recovery",
        "title": "One poll failure and healthy recovery",
        "action": "After settling, make exactly one state-poll fetch fail, restore normal polling, and do not mutate the scenario.",
        "outcome": "Healthy unchanged reads clear only active transport failure; history and unrelated diagnostics remain.",
    },
    {
        "id": "D09",
        "scenario": "dogfood.channel-latest-focus",
        "title": "Latest channel message visibility",
        "action": "Launch with --channel and inspect the selected final DOG-HISTORY message above the composer after layout settles.",
        "outcome": "The target is visible after publication without stealing scroll position from history readers.",
    },
    {
        "id": "D10",
        "scenario": "dogfood.multiselect-invalid-commit",
        "title": "Invalid minimum-count commit",
        "action": "On DOG-03, attempt to commit one selection, then correct to two or cancel with pointer and keyboard.",
        "outcome": "The draft remains recoverable and associated minimum-count guidance is announced; no invalid callback runs.",
    },
    {
        "id": "D11",
        "scenario": "dogfood.thumbnail-spoiler-geometry",
        "title": "Compact thumbnail spoiler label",
        "action": "Inspect the unrevealed REF-40 thumbnail badge at supported narrow/zoomed sizes, then reveal by keyboard.",
        "outcome": "The short label stays unbroken and the reveal target remains accessible.",
    },
    {
        "id": "D12",
        "scenario": "dogfood.screen-reader-error-recovery",
        "title": "Screen-reader diagnostic recovery",
        "action": "With focus in a component/modal, trigger D08 once and perform a human screen-reader failure/recovery walkthrough.",
        "outcome": "Meaningful status changes are announced once without moving focus; history remains separately navigable.",
    },
    {
        "id": "U01",
        "scenario": "dogfood.distinct-message-summaries",
        "title": "Distinct authorized message summaries",
        "action": "Compare both DOG-06 duplicates, component-only REF-40, and DOG-07 attachment-only content in navigation.",
        "outcome": "Summaries distinguish author, time, local ID, content kind, and safe component/attachment excerpt.",
    },
    {
        "id": "U02",
        "scenario": "dogfood.search-page-and-exact-id",
        "title": "Searchable bounded message navigation",
        "action": "With DOG-HISTORY loaded, keyboard-search, page results, move previous/next, and jump to an exact authorized ID.",
        "outcome": "Selection is stable and bounded; denied/deleted IDs reveal no hidden history.",
    },
    {
        "id": "U03",
        "scenario": "dogfood.callback-followup-discovery",
        "title": "Causal callback followup",
        "action": "Activate DOG-02 and DOG-03, then open the visible response/followup affordance without changing another page.",
        "outcome": "The admitted action's actual response is explicitly navigable; unrelated output is not attributed to it.",
        "controls": ["dogfood:single-select", "dogfood:multi-min2-select"],
    },
    {
        "id": "U04",
        "scenario": "dogfood.variant-select-traces",
        "title": "Variant-specific select transitions",
        "action": "Acquire authorized traces for string-select commit/cancel/search/clear and modal validation before parity claims.",
        "outcome": "Record each observed transition separately; no guessed Apply control or shared message/modal behavior.",
    },
    {
        "id": "U05",
        "scenario": "dogfood.responsive-exact-capture-profiles",
        "title": "Responsive and exact profile workflow",
        "action": "Switch Responsive/Exact, inspect actual-size readout, edit/reset a profile, and choose current viewport for capture.",
        "outcome": "Display mode and deterministic capture profile remain explicitly distinct.",
    },
    {
        "id": "U06",
        "scenario": "dogfood.inspector-collapse-focus-restore",
        "title": "Inspector space and focus restoration",
        "action": "Inspect an empty inspector at narrow and wide host sizes; open/close its tools and restore keyboard focus.",
        "outcome": "Empty inspector is collapsed, status is concise, and a narrow-screen drawer restores focus.",
    },
    {
        "id": "U07",
        "scenario": "dogfood.sensitive-support-export",
        "title": "Sensitive support-report inputs",
        "action": (
            "Exercise report fields using only synthetic sentinels: credential-sentinel, body-sentinel, "
            "identifier-sentinel, and path-sentinel; never paste credentials or a capability URL."
        ),
        "outcome": "The share-safe report omits sentinel content, IDs, paths, and capabilities while retaining allowlisted diagnostics.",
    },
    {
        "id": "U08",
        "scenario": "dogfood.page-layout-and-capture-guidance",
        "title": "Page-local layout and capture guidance",
        "action": "Switch message/channel layout on one page, preserve target/draft, and compare managed recipe with live-page guidance.",
        "outcome": "Only the selected page changes; deterministic capture and live popup/draft capture are distinct.",
    },
    {
        "id": "U09",
        "scenario": "dogfood.publication-freshness-refresh",
        "title": "Publication freshness and Refresh",
        "action": "Mutate the scenario through Python after publication, wait for healthy polls, then explicitly Refresh.",
        "outcome": "The page distinguishes read health from published revision/time and explains explicit publication.",
    },
)


def dogfood_payloads(
    viewer_mention: str,
    *,
    custom_emoji: str | None = None,
    animated_emoji: str | None = None,
) -> list[dict[str, object]]:
    """Build the local stress demos and finding-specific browser walkthrough cards."""
    markdown = (
        "DOG-01 Markdown\n# Heading\n## Smaller\n### Smallest\n-# Quiet subtext\n"
        "__underline__ ~~strike~~ ||secret||\n> Quote\n>>> Multi-line quote\ncontinued\n"
        "[Masked link](https://example.com)\nhttps://example.com\n"
        "Escaped: \\https://example.com\nUnsafe schemes: javascript:alert(1) data:text/plain,unsafe\n"
        "```python\nprint('hello')\n```\n:smile: 😀\n"
        "Latin العربية עברית 中文 नमस्ते 👩🏽‍💻 👍🏽 🇺🇳 1️⃣ ♥︎ ♥️\n" + viewer_mention
    )
    formatted_spoilers = (
        "DOG-MD-01 Formatted spoiler concealment\n"
        "Bare URL: ||https://example.com/private||\n"
        "Masked link: ||[hidden docs](https://example.com/docs)||\n"
        "Inline code: ||`token-sentinel`||\n"
        + (f"Custom emoji: ||{custom_emoji}||\n" if custom_emoji else "")
    )
    payloads: list[dict[str, object]] = [
        {"content": markdown},
        {"content": formatted_spoilers},
        {
            "content": (
                "DOG-MD-02 Later-line subtext boundary\n"
                "First paragraph\n-# only this later line is subtext\nthird line stays normal\n\n"
                "-# first subtext line\n-# second subtext line\n\n"
                "||Normal spoiler first\n-# small spoiler middle\nnormal spoiler last||\n\n"
                "Normal line\n-# ||small spoiler first\nnormal spoiler continuation||"
            )
        },
        {
            "content": (
                "DOG-MD-03 Highlighted multiline Python declaration\n"
                "```python\ndef hello():\n    return 1\n```"
            )
        },
        {
            "content": "DOG-02 Single select, 25 choices",
            "view": DogfoodChoices(custom_id="dogfood:single-select"),
        },
        {
            "content": "DOG-02 Required multi select, 25 choices, min 1/max 2",
            "view": DogfoodChoices(custom_id="dogfood:multi-required-select", minimum=1, maximum=2),
        },
        {
            "content": "DOG-04 Optional single select, default Region 0; 25 choices",
            "view": DogfoodChoices(
                custom_id="dogfood:single-optional-select", minimum=0, maximum=1, default_first=True
            ),
        },
        {
            "content": "DOG-03 Multi select, 25 choices, requires two",
            "view": DogfoodChoices(custom_id="dogfood:multi-min2-select", minimum=2, maximum=3),
        },
        {
            "content": "DOG-04 Optional multi select, default Region 0",
            "view": DogfoodChoices(
                custom_id="dogfood:multi-optional-select",
                minimum=0,
                maximum=2,
                default_first=True,
            ),
        },
        {
            "content": "DOG-CB-01 Callback failure; activate the button to exercise the error path.",
            "view": DogfoodCallbackView(),
        },
        {
            "content": "DOG-CB-02 Deferred response followed by an ephemeral follow-up.",
            "view": DogfoodDeferredView(),
        },
        {
            "content": "DOG-05 Long content\n" + "Long message line, inspect scrolling and clipping.\n" * 32,
            "view": DogfoodLongMessageView(),
        },
        {"content": "DOG-06 Duplicate label"},
        {"content": "DOG-06 Duplicate label"},
        {
            "content": "DOG-07 Spoiler attachment",
            "files": [
                _image_file("SPOILER_secret.png", (240, 100, 30), (30, 60, 90)),
                discord.File(io.BytesIO(b"dogfood download\n"), filename="notes.txt"),
            ],
        },
        {
            "content": "DOG-MEDIA-01 Image, playable audio/video, ordinary file, and original downloads.",
            "files": [
                _image_file("dogfood-image.png", (35, 165, 90), (20, 80, 130)),
                _fixture_file("video.mp4"),
                _fixture_file("voice.ogg"),
                discord.File(io.BytesIO(b"DOG-MEDIA-01 downloadable notes\n"), filename="notes.txt"),
            ],
        },
        {
            "content": "DOG-MEDIA-02 Spoiler image, audio, video, and text-file attachments.",
            "files": [
                _image_file("SPOILER_media-image.png", (240, 100, 30), (30, 60, 90)),
                _fixture_file("SPOILER_video.mp4", source="video.mp4"),
                _fixture_file("SPOILER_voice.ogg", source="voice.ogg"),
                discord.File(io.BytesIO(b"DOG-MEDIA-02 hidden notes\n"), filename="SPOILER_notes.txt"),
            ],
        },
        {
            "content": "DOG-MEDIA-03 Corrupt spoiler image; reveal its unavailable fallback.",
            "files": [discord.File(io.BytesIO(b"not a valid PNG image"), filename="SPOILER_broken.png")],
        },
        {
            "content": (
                "DOG-MEDIA-04 Animated GIF and APNG attachments"
                + (f"; authorized animated custom emoji: {animated_emoji}" if animated_emoji else "")
                + "."
            ),
            "files": [
                discord.File(io.BytesIO(dogfood_animated_gif()), filename="dogfood-animated.gif"),
                discord.File(io.BytesIO(dogfood_animated_png()), filename="dogfood-animated.png"),
            ],
        },
        {
            "content": "DOG-MEDIA-06 Oversized spoiler PNG; reveal fallback and download retained original.",
            "files": [
                discord.File(
                    io.BytesIO(dogfood_oversized_spoiler_png()),
                    filename="SPOILER_oversized.png",
                )
            ],
        },
        {
            "content": (
                "**REF-50-MODALS**\nOpen each modal. Capture empty, focus, filled, validation, "
                "selection/upload, and button-focus states."
            ),
            "view": ModalGallery(),
        },
        {
            "content": (
                "DOG-D01 Guild modal entity defaults\n"
                "The scenario uses four actual entity families and the synthetic guild fixture."
            ),
            "view": DogfoodModalGallery(),
        },
        {
            "content": (
                "DOG-UI-01 Conversation and media walkthrough\n"
                "REF-11: text-file content starts visible; toggle its code icon, then restore it. "
                "Open the image: check author/time, Zoom, arrow-key or drag panning, Fit, "
                "original-byte Download, Open original, and Escape focus return. "
                "Unrevealed spoilers must not appear in image navigation.\n"
                "Composer: Shift+Enter inserts a newline; Enter sends. IME Enter must finish "
                "composition without sending. Open/close developer tools while drafting; "
                "the draft and history position must survive. These are local checks, not parity certification."
            ),
        },
    ]
    payloads.extend(
        {
            "content": (
                f"{scenario['id']} — {scenario['title']}\n"
                f"Scenario: {scenario['scenario']}\n"
                f"Action: {scenario['action']}\n"
                f"Expected: {scenario['outcome']}\n"
                f"Controls: {', '.join(scenario.get('controls', ())) or 'see referenced fixture'}"
            )
        }
        for scenario in DOGFOOD_SCENARIOS
    )
    return payloads


def dogfood_dm_payload() -> dict[str, object]:
    """Build the user-only entity modal fixture for a DM preview."""
    return {
        "content": (
            "DOG-D01 DM UserSelect defaults\n"
            "DMs have no guild roles or channels; this scenario covers user-only selectors."
        ),
        "view": DogfoodDMModalGallery(),
    }
