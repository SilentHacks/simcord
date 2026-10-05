"""Checkout-only manual preview audit gallery; no Discord token required.

Run: uv run python scripts/preview_dogfood.py [--channel | --dm]
Check: uv run python scripts/preview_dogfood.py --check
The printed local URL is a capability: do not share it or put it in reports.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

import discord
from discord.ext import commands

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.discord_reference_bot import post_gallery
from tests.fixtures.preview import catalog

import simcord
from simcord.backend.cdn import CDN_BASE, sticker_url


def check_catalog() -> None:
    """Check walkthrough registration and construct every shared dogfood payload."""
    scenarios = catalog.DOGFOOD_SCENARIOS
    scenario_ids = [item.get("id") for item in scenarios]
    if not scenario_ids or any(not isinstance(value, str) or not value for value in scenario_ids):
        raise ValueError("dogfood scenarios need nonempty IDs")
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("dogfood scenario IDs must be unique")

    coverage_path = Path(__file__).resolve().parents[1] / "tests/fixtures/preview/coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    rows = coverage.get("rows")
    if not isinstance(rows, list):
        raise ValueError("coverage rows must be an array")
    finding_rows = [row for row in rows if isinstance(row, dict) and isinstance(row.get("findingId"), str)]
    registered = {row["findingId"]: row for row in finding_rows}
    if len(finding_rows) != len(registered) or not set(scenario_ids) <= set(registered):
        raise ValueError("coverage must register each dogfood walkthrough exactly once")
    for scenario in scenarios:
        row = registered[scenario["id"]]
        if row.get("runnerScenario") != scenario.get("scenario"):
            raise ValueError(f"{scenario['id']} does not map to its catalog scenario")
        if not row.get("preconditions"):
            raise ValueError(f"{scenario['id']} needs its evidence blockers")
    payloads = catalog.dogfood_payloads("@dogfood-check")
    if not any(str(payload.get("content", "")).startswith("DOG-D01 Guild") for payload in payloads):
        raise ValueError("the dogfood catalog must include the guild D01 modal scenario")
    payloads.append(catalog.dogfood_dm_payload())
    for reference_id in catalog.REFERENCE_IDS:
        payloads.append(catalog.gallery_payload(reference_id))
    for payload in payloads:
        catalog.close_payload(payload)
    print(f"Dogfood catalog check passed ({len(scenarios)} scenarios; no preview URL created).")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local preview dogfood gallery.")
    parser.add_argument("--channel", action="store_true", help="show channel history instead of one message")
    parser.add_argument("--dm", action="store_true", help="open the DM-only D01 UserSelect scenario")
    parser.add_argument("--check", action="store_true", help="validate scenario registration and factories")
    args = parser.parse_args()
    if args.check:
        check_catalog()
        return

    preview_assets: dict[str, tuple[str, bytes]] = {}
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    async with simcord.run(bot) as env:
        if args.dm:
            dm_user = env.create_user("dm-dogfood")
            await dm_user.send_dm("D01 DM-only UserSelect scenario")
            target = await env.bot.fetch_channel(dm_user.dm_channel.id)
            message_target = await target.send(**catalog.dogfood_dm_payload())
            channel_handle = dm_user.dm_channel
            viewers = [dm_user]
        else:
            guild = env.create_guild("Preview dogfood")
            channel = guild.create_text_channel("component-lab", topic="Manual release-readiness audit")
            alice = guild.add_member(env.create_user("alice"))
            bob = guild.add_member(env.create_user("bob"))
            for name in ("Reviewer", "Operator", "Guest"):
                guild.create_role(name, color=0x5865F2)
            for n in range(30):
                guild.add_member(env.create_user(f"candidate-{n:02}"))
            for name in ("releases", "support", "general"):
                guild.create_text_channel(name)
            guild.create_voice_channel("unsupported-voice-candidate")
            guild.create_text_channel(
                "bob-only-candidate",
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    bob: discord.PermissionOverwrite(view_channel=True),
                },
            )
            demo_emoji = guild.create_emoji("dogfood")
            custom_emoji = f"<:{demo_emoji.name}:{demo_emoji.id}>"
            preview_assets[f"{CDN_BASE}/emojis/{demo_emoji.id}.png"] = (
                "dogfood-emoji.png",
                catalog.dogfood_emoji_png(),
            )
            animated_emoji = guild.create_emoji("dogfood_motion", animated=True)
            animated_emoji_markup = f"<a:{animated_emoji.name}:{animated_emoji.id}>"
            preview_assets[f"{CDN_BASE}/emojis/{animated_emoji.id}.gif"] = (
                "dogfood-motion.gif",
                catalog.dogfood_animated_gif(),
            )
            outside_guild = env.create_guild("Out-of-scope candidate fixture")
            outside_guild.add_member(env.create_user("outside-guild-candidate"))

            target = env.bot.get_channel(channel.id)
            await post_gallery(target, alice, None)
            message_target = None
            for payload in catalog.dogfood_payloads(
                alice.mention,
                custom_emoji=custom_emoji,
                animated_emoji=animated_emoji_markup,
            ):
                message = await target.send(**payload)
                if payload["content"].startswith("DOG-D01 Guild"):
                    message_target = message
            await target.send(
                "DOG-SELECT-01 Disabled UserSelect with Alice's authorized default.",
                view=catalog.DogfoodDisabledEntityDefaultView(alice.id),
            )
            sticker = guild.create_sticker("dogfood-motion", format_type=3)
            sticker_path = Path(__file__).parents[1] / "tests" / "fixtures" / "preview" / "moving-square.json"
            sticker_source = await asyncio.to_thread(sticker_path.read_bytes)
            preview_assets[sticker_url(sticker.id, 3)] = ("moving-square.json", sticker_source)
            guild_sticker = await env.bot.get_guild(guild.id).fetch_sticker(sticker.id)
            await target.send("DOG-MEDIA-05 Local animated Lottie sticker fixture.", stickers=[guild_sticker])
            await alice.send(channel, "DOG-ACT-01 Editable message authored by alice.")
            await alice.send(channel, "DOG-ACT-02 Deletable message authored by alice.")

            available_parent = await target.send("DOG-REPLY-01 Parent for an available reply.")
            await alice.send(channel, "DOG-REPLY-01 Reply to an available parent.", reply_to=available_parent)
            deleted_parent = await alice.send(
                channel, "DOG-REPLY-02 Parent deleted before the preview opens."
            )
            await alice.send(
                channel,
                "DOG-REPLY-02 Reply with an unavailable/deleted reference.",
                reply_to=deleted_parent,
            )
            await alice.delete(deleted_parent)

            reaction_message = await target.send("DOG-REACTION-01 Seeded standard and custom reactions.")
            await alice.react(reaction_message, "🔥")
            await bob.react(reaction_message, f"{demo_emoji.name}:{demo_emoji.id}")

            single_poll = discord.Poll(
                question="DOG-POLL-01 Single choice: choose one destination",
                duration=dt.timedelta(hours=1),
            )
            single_poll.add_answer(text="North")
            single_poll.add_answer(text="South")
            single_poll_message = await target.send(poll=single_poll)
            await alice.vote(single_poll_message, answer=1)
            await bob.vote(single_poll_message, answer=2)

            multiple_poll = discord.Poll(
                question="DOG-POLL-02 Multiple choice: choose any destinations",
                duration=dt.timedelta(hours=1),
                multiple=True,
            )
            multiple_poll.add_answer(text="North")
            multiple_poll.add_answer(text="South")
            multiple_poll.add_answer(text="West")
            multiple_poll_message = await target.send(poll=multiple_poll)
            await alice.set_poll_votes(multiple_poll_message, answers=(1, 2))
            await bob.vote(multiple_poll_message, answer=2)
            for n in range(55):
                await target.send(f"DOG-HISTORY {n:02}: paging and picker stress")
            if message_target is None:
                raise RuntimeError("the dogfood catalog must include the guild D01 modal scenario")
            channel_handle = channel
            viewers = [alice, bob]

        async with env.preview(
            channel_handle,
            viewers=viewers,
            assets=preview_assets,
            layout="channel" if args.channel else "message",
        ) as preview:
            await preview.show(channel_handle.last_message if args.channel else message_target)
            print(f"PREVIEW_URL={preview.url}", flush=True)
            await preview.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
