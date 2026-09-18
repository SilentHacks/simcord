"""Post a labelled Discord component gallery for manual reference screenshots.

Run ``python scripts/discord_reference_bot.py --help`` for setup. The bot never
captures screenshots, controls a user account, or stores credentials or images.
"""

from __future__ import annotations

import argparse
import asyncio
import binascii
import datetime as dt
import io
import json
import os
import struct
import zlib
from collections.abc import Sequence

import discord
from discord import app_commands
from discord.ext import commands

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


async def post_gallery(channel: discord.abc.Messageable, user: discord.abc.User, sku_id: int | None) -> None:
    await channel.send(
        "**REF-00-INDEX — visual reference gallery**\n"
        "Keep browser zoom at 100% and use the agreed capture profile. Each surface carries its own REF identifier. "
        "Capture idle, hover, keyboard-focus, disabled, open/selected, and validation states where available. "
        "No screenshot is uploaded by this bot.",
    )
    await channel.send(
        f"**REF-10-LEGACY-EMBED**\nReference viewer: {user.mention}\n"
        "Markdown: **bold**, *italic*, __underline__, ~~strike~~, ||spoiler||, `inline code`, and a very-long-token-for-wrap-testing-0123456789.",
        embed=_embed(),
        files=[
            _image_file("ref-thumbnail.png", (88, 101, 242), (35, 39, 42)),
            _image_file("ref-hero.png", (35, 165, 90), (20, 80, 130)),
        ],
    )
    await channel.send(
        "**REF-11-ATTACHMENTS**\nCapture the inline image, file tile, filename wrapping, sizes, and download controls.",
        files=[
            _image_file("ref-inline-image-with-a-long-name.png", (210, 70, 90), (65, 25, 90)),
            discord.File(
                io.BytesIO(b"Standalone legacy attachment reference\n"),
                filename="ref-standalone-document-with-a-long-name.txt",
            ),
        ],
    )
    premium = (
        " Included: active premium SKU."
        if sku_id is not None
        else " Premium omitted: set DISCORD_SKU_ID to an active SKU owned by this app."
    )
    await channel.send(
        "**REF-20-BUTTONS**\nCapture idle, hover, pressed, keyboard focus, and disabled states." + premium,
        view=ButtonGallery(sku_id),
    )
    await channel.send(
        "**REF-30-STRING-SELECT**\nCapture closed, open, hover, keyboard focus, one/two selected, cleared, and disabled states.",
        view=StringSelectGallery(),
    )
    await channel.send(
        "**REF-31-ENTITY-SELECTS**\nOpen each menu and capture its candidate decoration, selection, and keyboard focus.",
        view=EntitySelectGallery(),
    )
    layout, files = _layout_view()
    await channel.send(view=layout, files=files)
    await channel.send(
        "**REF-50-MODALS**\nOpen each modal. Capture empty, focus, filled, validation, selection/upload, and button-focus states.",
        view=ModalGallery(),
    )


def create_bot(guild_id: int, sku_id: int | None = None) -> commands.Bot:
    bot = commands.Bot(
        command_prefix=commands.when_mentioned,
        intents=discord.Intents.none(),
        allowed_mentions=discord.AllowedMentions.none(),
    )

    @bot.tree.command(name="visual_references", description="Post the labelled component reference gallery")
    @app_commands.guild_only()
    async def visual_references(interaction: discord.Interaction) -> None:
        if interaction.guild_id != guild_id:
            await interaction.response.send_message(
                "This command is restricted to the configured reference guild.", ephemeral=True
            )
            return
        if interaction.channel is None:
            await interaction.response.send_message(
                "Run this command in a server text channel.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await post_gallery(interaction.channel, interaction.user, sku_id)
        await interaction.edit_original_response(
            content="Posted REF-00 through REF-50. Take screenshots manually; the bot stores none."
        )

    async def setup_hook() -> None:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced /visual_references to guild {guild_id} ({len(synced)} command).")

    bot.setup_hook = setup_hook  # type: ignore[method-assign]
    return bot


async def _check_post_gallery() -> None:
    records: list[str] = []

    class Sink:
        async def send(self, content: str | None = None, **kwargs: object) -> None:
            view = kwargs.get("view")
            embed = kwargs.get("embed")
            records.append(
                "\n".join(
                    part
                    for part in (
                        content,
                        json.dumps(view.to_components()) if hasattr(view, "to_components") else None,
                        json.dumps(embed.to_dict()) if isinstance(embed, discord.Embed) else None,
                    )
                    if part is not None
                )
            )
            for file in kwargs.get("files", []):
                file.close()

    class User:
        mention = "@reference-viewer"

    await post_gallery(Sink(), User(), None)  # type: ignore[arg-type]
    output = "\n".join(records)
    assert len(records) == 8
    assert all(reference_id in output for reference_id in REFERENCE_IDS)


def _component_types(value: object) -> set[int]:
    found: set[int] = set()
    if isinstance(value, dict):
        if isinstance(value.get("type"), int):
            found.add(value["type"])
        for child in value.values():
            found.update(_component_types(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_component_types(child))
    return found


def _check() -> None:
    assert len(REFERENCE_IDS) == len(set(REFERENCE_IDS))
    assert _png(2, 2, (0, 0, 0), (255, 255, 255)).startswith(b"\x89PNG\r\n\x1a\n")
    assert len(ButtonGallery(None).children) == 7
    assert len(StringSelectGallery().children) == 2
    assert len(EntitySelectGallery().children) == 4
    layout, files = _layout_view()
    assert len(layout.children) == 6 and len(files) == 4
    assert len(ModalGallery().children) == 4
    assert len(TextModal().children) == 3
    assert len(ChoiceModal().children) == 4
    assert len(EntityModal().children) == 4
    assert len(UploadModal().children) == 1
    fixtures = (
        ButtonGallery(None),
        StringSelectGallery(),
        EntitySelectGallery(),
        ModalGallery(),
        TextModal(),
        ChoiceModal(),
        EntityModal(),
        UploadModal(),
        layout,
    )
    covered = set().union(*(_component_types(fixture.to_components()) for fixture in fixtures))
    assert covered == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 21, 22, 23}
    for file in files:
        file.close()
    asyncio.run(_check_post_gallery())
    print(f"Reference gallery check passed ({len(REFERENCE_IDS)} labelled surfaces).")


def _optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Post labelled Discord component fixtures for manual screenshots.",
        epilog=(
            "Set DISCORD_TOKEN and DISCORD_GUILD_ID, install the app in that private test guild with "
            "bot + applications.commands scopes, then run this script and invoke /visual_references. "
            "Optionally set DISCORD_SKU_ID to an active SKU belonging to the app. The command needs "
            "Send Messages, Embed Links, Attach Files, and Use Application Commands permissions."
        ),
    )
    parser.add_argument(
        "--check", action="store_true", help="validate the fixture catalog without connecting"
    )
    args = parser.parse_args(argv)
    if args.check:
        _check()
        return
    token = os.getenv("DISCORD_TOKEN")
    guild_id = _optional_int("DISCORD_GUILD_ID")
    if not token or guild_id is None:
        parser.error("set DISCORD_TOKEN and DISCORD_GUILD_ID (use --check without credentials)")
    create_bot(guild_id, _optional_int("DISCORD_SKU_ID")).run(token, log_handler=None)


if __name__ == "__main__":
    main()
