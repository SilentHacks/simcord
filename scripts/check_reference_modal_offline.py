"""Exercise the production REF-50 modal callback through an offline SimCord actor."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import discord
from discord.ext import commands

import simcord

CATALOG_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/preview/catalog.py"
spec = importlib.util.spec_from_file_location("reference_catalog_offline", CATALOG_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"reference catalog is unavailable: {CATALOG_PATH}")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def component_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("custom_id"), str):
            found.add(value["custom_id"])
        for child in value.values():
            found.update(component_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(component_ids(child))
    return found


async def main() -> None:
    bot = commands.Bot(command_prefix=commands.when_mentioned, intents=discord.Intents.none())
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("modal-check")
        actor = guild.add_member(env.create_user("modal-check"))
        payload = catalog.gallery_payload("REF-50-MODALS", viewer_mention=actor.mention)
        target = await env.bot.fetch_channel(channel.id)
        assert isinstance(target, discord.TextChannel)
        message = await target.send(**payload)
        result = await actor.click(message, custom_id="reference:open-text")
        assert result.acknowledged and result.modal is not None, repr(result)
        assert result.modal["title"] == "REF-51-TEXT-MODAL"
        ids = component_ids(result.modal["components"])
        assert {"reference:name", "reference:feedback"} <= ids
        print("Offline REF-50 actor click opened the production REF-51 modal and text fields.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
