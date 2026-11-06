"""Run with python -m scripts.behavior_reference; credentials are environment-only."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from contextlib import AsyncExitStack
from pathlib import Path

import aiohttp
from scripts.monetization_reference_transport import require_capture_transport

from .scenarios import CASES, select
from .transport import LAB_GUILD, Journal, Live, MultipartSession, cleanup, open_evidence, require


def parser():
    result = argparse.ArgumentParser(
        description="Authorized Discord behavior lab recorder. Hypotheses are never live evidence until captured."
    )
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory", help="Print hand-written scenarios and single-cause controls (no network)")
    for name in ("preflight", "record", "crosscheck", "cleanup"):
        command = sub.add_parser(name)
        command.add_argument(
            "--guild", default=LAB_GUILD, help="Only the approved Community behavior lab is authorized"
        )
        command.add_argument(
            "--directory",
            type=Path,
            required=True,
            help="Private evidence and durable ownership journal directory",
        )
        command.add_argument(
            "--subject-id",
            default=os.environ.get("BEHAVIOR_SUBJECT_ID"),
            help="Expected Zoku bot ID (or BEHAVIOR_SUBJECT_ID); identity checked before writes",
        )
        if name in {"record", "crosscheck"}:
            command.add_argument(
                "--case",
                action="append",
                default=[],
                help="Scenario glob; controls are included automatically; omit for full catalogue",
            )
            command.add_argument(
                "--ledger",
                type=Path,
                required=True,
                help="Normalized live ledger output, written only after successful cleanup",
            )
    publish = sub.add_parser(
        "publish",
        help="Recover real durable observations after receipt-proven cleanup; offline, no credentials",
    )
    publish.add_argument("--directory", type=Path, required=True)
    publish.add_argument("--ledger", type=Path, required=True)
    publish.add_argument(
        "--supplement",
        type=Path,
        action="append",
        default=[],
        help="Merge genuine supplementary captures; overlapping rows must agree exactly",
    )
    replay = sub.add_parser(
        "replay", help="Replay raw symbolic scenarios against SimCord and enforce known-gap ratchet"
    )
    replay.add_argument("--ledger", type=Path, required=True)
    replay.add_argument("--known-gaps", type=Path, required=True)
    table = sub.add_parser("table", help="Generate evidence table for parity-matrix integration")
    table.add_argument("--ledger", type=Path, required=True)
    return result


async def preflight(adapter, subject_id):
    journal = adapter.journal
    admin = await require(adapter, "admin", "GET", "/users/@me")
    subject = await require(adapter, "subject", "GET", "/users/@me")
    if (
        not subject_id
        or str(subject["id"]) != str(subject_id)
        or not subject.get("bot")
        or not admin.get("bot")
        or admin["id"] == subject["id"]
    ):
        raise RuntimeError(
            "Expected distinct Serverling and explicitly identified Zoku bot accounts before any mutation"
        )
    journal.state.update(subject=str(subject["id"]), author=str(admin["id"]), application=str(subject["id"]))
    journal.state["command_name"] = "lab-" + secrets.token_hex(6)
    journal.save()
    guild = journal.state["guild"]
    await require(adapter, "admin", "GET", f"/guilds/{guild}")
    member = await require(adapter, "admin", "GET", f"/guilds/{guild}/members/{subject['id']}")
    author_member = await require(adapter, "admin", "GET", f"/guilds/{guild}/members/{admin['id']}")
    roles = await require(adapter, "admin", "GET", f"/guilds/{guild}/roles")
    subject_roles = set(member["roles"]) | {guild}
    if any(int(role["permissions"]) & 8 for role in roles if role["id"] in subject_roles):
        raise RuntimeError(
            "Zoku has pre-existing ADMINISTRATOR: permission-denial cases cannot isolate a single cause safely"
        )
    journal.state["baseline_member"] = {
        key: member.get(key) for key in ("nick", "communication_disabled_until")
    }
    journal.state["baseline_subject_permissions"] = 0
    for role in roles:
        if role["id"] in subject_roles:
            journal.state["baseline_subject_permissions"] |= int(role["permissions"])
    journal.save()
    commands = await require(
        adapter, "subject", "GET", f"/applications/{subject['id']}/guilds/{guild}/commands"
    )
    if any(command["name"] == journal.state["command_name"] for command in commands):
        raise RuntimeError("Random lab command name already exists; refusing an upsert")
    role = await require(
        adapter,
        "admin",
        "POST",
        f"/guilds/{guild}/roles",
        body={"name": "lab-behavior-subject", "permissions": "0", "mentionable": False},
    )
    # A real assignment proves hierarchy; no managed role is ever moved or edited.
    try:
        await require(adapter, "admin", "PUT", f"/guilds/{guild}/members/{subject['id']}/roles/{role['id']}")
    except RuntimeError:
        admin_roles = set(author_member["roles"])
        positions = [role["position"] for role in roles if role["id"] in admin_roles]
        raise RuntimeError(
            f"Serverling cannot assign the positively owned lab role (highest existing role position {max(positions, default=0)}). Move Serverling above newly created lab roles, then rerun in a NEW directory; cleanup receipts are retained."
        ) from None
    journal.state["subject_role"] = str(role["id"])
    journal.state["restores"].append(
        {
            "method": "DELETE",
            "path": f"/guilds/{guild}/members/{subject['id']}/roles/{role['id']}",
            "done": False,
        }
    )
    journal.save()
    print(
        "Preflight: distinct bots verified; owned role create/assignment succeeded. No managed role or existing command changed."
    )


async def live(args):
    if args.guild != LAB_GUILD:
        raise ValueError("Refusing unauthorized guild before opening authenticated sessions")
    admin_token, subject_token = (
        os.environ.get("BEHAVIOR_ADMIN_TOKEN"),
        os.environ.get("BEHAVIOR_SUBJECT_TOKEN"),
    )
    if not admin_token or not subject_token:
        raise ValueError(
            "Set BEHAVIOR_ADMIN_TOKEN and BEHAVIOR_SUBJECT_TOKEN; tokens must never be CLI arguments"
        )
    require_capture_transport()
    evidence = open_evidence(args.directory, (admin_token, subject_token), resume=args.command == "cleanup")
    journal = Journal(evidence, args.guild, resume=args.command == "cleanup")
    async with AsyncExitStack() as stack:
        sessions = {}
        for actor, token in (("admin", admin_token), ("subject", subject_token)):
            session = await stack.enter_async_context(
                aiohttp.ClientSession(
                    headers={"Authorization": f"Bot {token}"}, timeout=aiohttp.ClientTimeout(total=35)
                )
            )
            sessions[actor] = MultipartSession(session)
        adapter = Live(sessions, evidence, journal)
        if args.command == "cleanup":
            # Recovery credentials must match both recorded identities before any deletion.
            for actor, key in (("admin", "author"), ("subject", "subject")):
                user = await require(adapter, actor, "GET", "/users/@me")
                if str(user["id"]) != journal.state.get(key):
                    raise RuntimeError("Recovery bot identity differs from journal")
            await cleanup(adapter)
            return
        rows = None
        try:
            await preflight(adapter, args.subject_id)
            if args.command in {"record", "crosscheck"}:
                if args.command == "crosscheck":
                    from .oracle import record
                else:
                    from .runner import record

                rows = await record(adapter, select(args.case), subject_token, sessions["subject"].session)
        finally:
            # Shield cleanup from the first Ctrl-C; a second interruption retains its intent.
            task = asyncio.create_task(cleanup(adapter))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        if rows is not None:
            from .ledger import write_ledger

            provenance = (
                {
                    "permission_oracle": {
                        "implicit": False,
                        "cases": journal.state["permission_oracle_cases"],
                        "comparison": "Known explicit permission bits; raw live receipts in permissions.jsonl",
                    }
                }
                if journal.state.get("permission_oracle_cases")
                else None
            )
            write_ledger(args.ledger, rows, provenance=provenance)


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "inventory":
        for case in CASES:
            print(
                f"{case.id}\t{case.operation}\t{case.target}\tcontrol={case.control or '-'}\tdocs={case.source}"
            )
    elif args.command in {"preflight", "record", "crosscheck", "cleanup"}:
        try:
            asyncio.run(live(args))
        except (RuntimeError, ValueError, aiohttp.ClientError, TimeoutError) as error:
            # Never print exception text from transport URLs or credential-bearing inputs.
            if isinstance(error, (aiohttp.ClientError, TimeoutError)):
                raise SystemExit(
                    f"Transport stopped ({type(error).__name__}); inspect redacted receipts and run cleanup. Writes are never replayed."
                ) from None
            raise SystemExit(str(error)) from None
    elif args.command == "publish":
        from .publish import publish

        try:
            publish(args.directory, args.ledger, args.supplement)
        except (RuntimeError, ValueError) as error:
            raise SystemExit(str(error)) from None
    elif args.command == "replay":
        from .ledger import replay

        asyncio.run(replay(args.ledger, args.known_gaps))
    else:
        from .ledger import evidence_table, load_ledger

        print(evidence_table(load_ledger(args.ledger)))


if __name__ == "__main__":
    main()
