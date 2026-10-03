"""Checkout-only manual preview audit gallery; no Discord token required.

Run: uv run python scripts/preview_dogfood.py [--channel | --dm]
Check: uv run python scripts/preview_dogfood.py --check
The printed local URL is a capability: do not share it or put it in reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import discord
from discord.ext import commands

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.discord_reference_bot import post_gallery
from tests.fixtures.preview import catalog

import simcord


def check_catalog() -> None:
    """Check finding registrations and construct every shared dogfood payload."""
    expected = tuple(f"D{number:02}" for number in range(1, 13)) + tuple(
        f"U{number:02}" for number in range(1, 10)
    )
    scenarios = catalog.DOGFOOD_SCENARIOS
    scenario_ids = tuple(item["id"] for item in scenarios)
    if scenario_ids != expected:
        raise ValueError("dogfood scenario IDs are missing, duplicated, or out of order")

    coverage_path = Path(__file__).resolve().parents[1] / "tests/fixtures/preview/coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    rows = coverage.get("rows")
    if not isinstance(rows, list):
        raise ValueError("coverage rows must be an array")
    finding_rows = [row for row in rows if isinstance(row, dict) and isinstance(row.get("findingId"), str)]
    registered = {row["findingId"]: row for row in finding_rows}
    if len(finding_rows) != len(expected) or set(registered) != set(expected):
        raise ValueError("coverage must register every D/U finding exactly once")
    for scenario in scenarios:
        row = registered[scenario["id"]]
        if row.get("runnerScenario") != scenario["scenario"]:
            raise ValueError(f"{scenario['id']} does not map to its catalog scenario")
        if not row.get("owningCommits") or not row.get("reason"):
            raise ValueError(f"{scenario['id']} needs an owning commit and evidence prerequisite")
        if row.get("expected", {}).get("visible") != scenario["outcome"]:
            raise ValueError(f"{scenario['id']} outcome differs between catalog and coverage")
        if row.get("referenceStatus") not in {"blocked", "missing"}:
            raise ValueError(f"{scenario['id']} must not claim an authorized reference")
        if row.get("comparisonStatus") != "not_comparable":
            raise ValueError(f"{scenario['id']} cannot claim a comparison without reference evidence")

        if row.get("implementationStatus") not in {"partial", "missing"}:
            raise ValueError(f"{scenario['id']} implementation status is not independently recorded")
    payloads = catalog.dogfood_payloads("@dogfood-check")
    if not any(str(payload.get("content", "")).startswith("DOG-D01 Guild") for payload in payloads):
        raise ValueError("the dogfood catalog must include the guild D01 modal scenario")
    payloads.append(catalog.dogfood_dm_payload())
    for reference_id in catalog.REFERENCE_IDS:
        payloads.append(catalog.gallery_payload(reference_id))
    for payload in payloads:
        catalog.close_payload(payload)
    print(f"Dogfood catalog check passed ({len(expected)} scenarios; no preview URL created).")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local preview dogfood gallery.")
    parser.add_argument("--channel", action="store_true", help="show channel history instead of one message")
    parser.add_argument("--dm", action="store_true", help="open the DM-only D01 UserSelect scenario")
    parser.add_argument("--check", action="store_true", help="validate scenario registration and factories")
    args = parser.parse_args()
    if args.check:
        check_catalog()
        return

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
            outside_guild = env.create_guild("Out-of-scope candidate fixture")
            outside_guild.add_member(env.create_user("outside-guild-candidate"))

            target = env.bot.get_channel(channel.id)
            await post_gallery(target, alice, None)
            message_target = None
            for payload in catalog.dogfood_payloads(alice.mention):
                message = await target.send(**payload)
                if payload["content"].startswith("DOG-D01 Guild"):
                    message_target = message
            for n in range(55):
                await target.send(f"DOG-HISTORY {n:02}: paging and picker stress")
            if message_target is None:
                raise RuntimeError("the dogfood catalog must include the guild D01 modal scenario")
            channel_handle = channel
            viewers = [alice, bob]

        async with env.preview(
            channel_handle,
            viewers=viewers,
            layout="channel" if args.channel else "message",
        ) as preview:
            await preview.show(channel_handle.last_message if args.channel else message_target)
            print(f"PREVIEW_URL={preview.url}", flush=True)
            await preview.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
