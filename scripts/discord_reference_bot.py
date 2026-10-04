"""Post a labelled Discord component gallery for manual reference screenshots.

Run ``python scripts/discord_reference_bot.py --help`` for setup. The bot never
captures screenshots, controls a user account, or stores credentials.
Optional exports contain only its own fixture assets and message metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import importlib.util
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


def _export_fixture(reference_id: str, payload: dict, mention: str, directory: Path) -> dict:
    normalized = {"content": str(payload.get("content", "")).replace(mention, "@simcord-viewer")}
    if "embed" in payload:
        normalized["embed"] = payload["embed"].to_dict()
    if "view" in payload:
        normalized["components"] = payload["view"].to_components()
        normalized["flags"] = 32768 if isinstance(payload["view"], discord.ui.LayoutView) else 0
    hashes = {}
    for file in payload.get("files", []):
        position = file.fp.tell()
        try:
            data = file.fp.read()
        finally:
            file.fp.seek(position)
        name = file.filename
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("Fixture filenames must be plain filenames")
        (directory / "assets" / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    normalized["attachments"] = hashes
    canonical = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "referenceId": reference_id,
        "postedContent": payload.get("content", ""),
        "viewerMention": mention,
        "normalizedPayload": normalized,
        "normalizedPayloadHash": hashlib.sha256(canonical).hexdigest(),
        "assetHashes": hashes,
    }


def _write_manifest(directory: Path, records: list[dict]) -> None:
    data = {
        "schemaVersion": 1,
        "source": "official-bot-api",
        "discordPyVersion": discord.__version__,
        "hashNormalization": "sorted UTF-8 input JSON; invoking-user mention replaced with @simcord-viewer; attachment bytes hashed",
        "fixtures": records,
    }
    temporary = directory / "bot-fixtures.json.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "bot-fixtures.json")


async def post_gallery(
    channel: discord.abc.Messageable,
    user: discord.abc.User,
    sku_id: int | None,
    *,
    output_dir: Path | None = None,
) -> None:
    records = []
    if output_dir is not None:
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=False)
        await asyncio.to_thread((output_dir / "assets").mkdir)
    for reference_id in REFERENCE_IDS:
        payload = _CATALOG.gallery_payload(reference_id, viewer_mention=user.mention, sku_id=sku_id)
        try:
            record = None
            if output_dir is not None:
                record = await asyncio.to_thread(
                    _export_fixture, reference_id, payload, user.mention, output_dir
                )
            message = await channel.send(**payload)
            if record is not None:
                record.update(
                    {
                        "messageId": str(message.id),
                        "channelId": str(message.channel.id),
                        "createdAt": message.created_at.isoformat(),
                        "author": {
                            "id": str(message.author.id),
                            "username": message.author.name,
                            "displayName": message.author.display_name,
                            "avatarUrl": str(message.author.display_avatar.url),
                        },
                        "previousMessageId": records[-1]["messageId"] if records else None,
                    }
                )
                records.append(record)
                await asyncio.to_thread(_write_manifest, output_dir, records)
        finally:
            _CATALOG.close_payload(payload)


def create_bot(guild_id: int, sku_id: int | None = None, *, output_dir: Path | None = None) -> commands.Bot:
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
        if output_dir is not None and await asyncio.to_thread(output_dir.exists):
            await interaction.edit_original_response(
                content="Fixture output already exists. Restart with a new --output-dir; existing evidence is preserved."
            )
            return
        await post_gallery(interaction.channel, interaction.user, sku_id, output_dir=output_dir)
        await interaction.edit_original_response(
            content="Posted REF-00 through REF-50. Take screenshots manually; the bot never captures or uploads screenshots."
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
    layout, files = _layout_view()
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
    parser.add_argument("--guild-id", type=int, help="private test guild ID (or DISCORD_GUILD_ID)")
    parser.add_argument(
        "--prompt-token", action="store_true", help="enter the bot token without echo or shell history"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="new private fixture export directory; never overwritten"
    )
    args = parser.parse_args(argv)
    if args.check:
        _check()
        return
    token = getpass.getpass("Test BOT token (hidden): ") if args.prompt_token else os.getenv("DISCORD_TOKEN")
    guild_id = args.guild_id if args.guild_id is not None else _optional_int("DISCORD_GUILD_ID")
    if not token or guild_id is None:
        parser.error("provide --prompt-token/--guild-id or DISCORD_TOKEN/DISCORD_GUILD_ID")
    create_bot(guild_id, _optional_int("DISCORD_SKU_ID"), output_dir=args.output_dir).run(
        token, log_handler=None
    )


if __name__ == "__main__":
    main()
