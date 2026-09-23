"""Post a labelled Discord component gallery for manual reference screenshots.

Run ``python scripts/discord_reference_bot.py --help`` for setup. The bot never
captures screenshots, controls a user account, or stores credentials or images.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import os
from collections.abc import Sequence
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "preview" / "catalog.py"
_CATALOG_SPEC = importlib.util.spec_from_file_location("simcord_preview_fixture_catalog", _CATALOG_PATH)
if _CATALOG_SPEC is None or _CATALOG_SPEC.loader is None:
    raise ImportError(f"preview fixture catalog is unavailable: {_CATALOG_PATH}")
_CATALOG = importlib.util.module_from_spec(_CATALOG_SPEC)
_CATALOG_SPEC.loader.exec_module(_CATALOG)

REFERENCE_IDS = _CATALOG.REFERENCE_IDS
ButtonGallery = _CATALOG.ButtonGallery
ChoiceModal = _CATALOG.ChoiceModal
EntityModal = _CATALOG.EntityModal
EntitySelectGallery = _CATALOG.EntitySelectGallery
ModalGallery = _CATALOG.ModalGallery
StringSelectGallery = _CATALOG.StringSelectGallery
TextModal = _CATALOG.TextModal
UploadModal = _CATALOG.UploadModal
_acknowledge = _CATALOG._acknowledge
_embed = _CATALOG._embed
_image_file = _CATALOG._image_file
_layout_view = _CATALOG._layout_view
_png = _CATALOG._png


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
