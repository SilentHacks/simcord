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

import discord

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


def _png(width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> bytes:
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
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _image_file(filename: str, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> discord.File:
    return discord.File(io.BytesIO(_png(640, 360, top, bottom)), filename=filename)


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
        text="Feedback",
        description="Optional longer response.",
        component=discord.ui.TextInput(
            custom_id="reference:feedback",
            style=discord.TextStyle.paragraph,
            placeholder="Tell us what you think…",
            required=False,
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
            "## REF-40-V2-LAYOUT-MEDIA\nCapture the complete message and each interactive state."
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
                "attachment://ref-gallery-wide.png", description="Wide blue reference image"
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
            discord.ui.TextDisplay("REF-41-CONTAINER-SPOILER\nReveal this container."), spoiler=True
        )
    )
    files = [
        _image_file("ref-v2-thumbnail.png", (88, 101, 242), (70, 35, 110)),
        _image_file("ref-gallery-wide.png", (0, 170, 255), (0, 55, 110)),
        _image_file("ref-gallery-spoiler.png", (255, 170, 0), (145, 45, 20)),
        discord.File(io.BytesIO(b"SimCord visual reference fixture\n"), filename="ref-document.txt"),
    ]
    return view, files


def gallery_payload(
    reference_id: str,
    *,
    viewer_mention: str = "@simcord-viewer",
    sku_id: int | None = None,
) -> dict[str, object]:
    """Build one of the labelled gallery messages for local capture worlds.

    The payloads intentionally mirror ``scripts.discord_reference_bot``.  The
    capture runner uses this small factory rather than importing the bot
    process, so a world can be created and torn down in one command.
    """
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
