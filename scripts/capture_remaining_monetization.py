"""Capture remaining authorized monetization probes, with recovery and explicit blockers.

No automated checkout/publication, user-token login, or simulator mutation.
Optional manual checkout stages require explicit flags. Run --help.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import discord
from capture_monetization_reference import COMMAND, Evidence, Recorder, Rest, redact
from monetization_reference_checkout import capture_checkouts
from monetization_reference_expiry import load_expiry_source, prepare_expiry
from monetization_reference_lifecycle import capture_guild, capture_lifecycle
from validate_monetization_reference import validate_bundle


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def references(evidence, before):
    return {
        stream: [f"{stream}.jsonl:{line}" for line in range(before.get(stream, 0) + 1, count + 1)]
        for stream, count in evidence.counts.items()
        if count > before.get(stream, 0)
    }


def subscription_catalog(skus, names, scope):
    selected = []
    scopes = []
    for name in names:
        matches = [
            sku for sku in skus if sku["name"] == name and sku["type"] == discord.SKUType.subscription.value
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Need one subscription named {name!r}; inspect setup.skus evidence.")
        sku = matches[0]
        user = bool(sku.get("flags", 0) & discord.SKUFlags.user_subscription.flag)
        guild = bool(sku.get("flags", 0) & discord.SKUFlags.guild_subscription.flag)
        if user == guild:
            raise RuntimeError("Subscription scope is absent or ambiguous; stopped before mutation.")
        scopes.append("user" if user else "guild")
        selected.append(sku)
    if len({sku["id"] for sku in selected}) != 2 or len(set(scopes)) != 1:
        raise RuntimeError("Need two distinct subscriptions with the same ownership scope.")
    if scope != "auto" and scope != scopes[0]:
        raise RuntimeError("Requested scope does not match the app's subscription SKUs.")
    return selected, 2 if scopes[0] == "user" else 1


class Capture:
    def __init__(self, args, evidence, state):
        self.args, self.evidence, self.state = args, evidence, state
        self.app = {}
        self.skus = []
        self.subscriptions = []
        self.owner_type = state.get("owner_type", 2)
        self.owner_id = state.get("owner_id", str(args.user))
        self.base = ""
        self.rest: Any = None
        self.client = Recorder(args, evidence)
        self.client.connection_epoch = state.get("last_connection_epoch", 0)
        self.gateway: Any = None
        self.last_request = 0.0
        self.cleaning = False
        commands = self.state.setdefault("temporary_commands", {})
        if state.get("temporary_command_id") and str(args.guild) not in commands:
            if not state.get("command_create_ref"):
                raise RuntimeError(
                    "Legacy command journal lacks a positive create receipt; inspect it before cleanup."
                )
            commands[str(args.guild)] = {
                "id": state["temporary_command_id"],
                "create_ref": state["command_create_ref"],
                "deleted": False,
            }

    def save(self):
        self.state["last_connection_epoch"] = self.client.connection_epoch
        self.evidence.manifest(**self.state)

    def block(self, case, reason):
        self.state["cases"][case] = {"status": "blocked", "reason": reason}
        print(f"BLOCKED {case}: {reason}", flush=True)
        self.save()

    def finish_section(self, section):
        self.state["sections"][section] = {"done": True}
        self.save()

    def received_create(self, body, data, receipt):
        if not isinstance(data, dict) or not str(data.get("id", "")).isdigit():
            raise RuntimeError("Successful create lacked a usable ID; preserve evidence, do not retry.")
        eid = str(data["id"])
        if eid in self.state.get("manual_live_entitlement_ids", []) or eid in self.state.get(
            "manual_test_purchase_ids", []
        ):
            raise RuntimeError("Create returned a manual purchase ID; no cleanup ownership inferred.")
        if type(data.get("type")) is not int or data["type"] != 8 or data.get("sku_id") != body.get("sku_id"):
            raise RuntimeError(
                "Create did not identify the requested API test grant; no cleanup ownership inferred."
            )
        if eid in self.state["owned_entitlements"]:
            return
        if eid in self.state.get("baseline_ids", []):
            raise RuntimeError(
                "Create returned a preexisting ID; no cleanup ownership inferred. Inspect the write before resuming."
            )
        users = {str(self.args.user), str(self.args.other_user)} - {"None"}
        guilds = {str(self.args.guild), str(self.args.other_guild)} - {"None"}
        if data.get("application_id") != self.app["id"]:
            raise RuntimeError("Created row's app cannot be verified; preserve the uncertain mutation.")
        if str(data.get("guild_id")) in guilds:
            owner_type, owner = 1, str(data["guild_id"])
        elif str(data.get("user_id")) in users:
            owner_type, owner = 2, str(data["user_id"])
        else:
            raise RuntimeError(
                "Created row's owner is not authorized; stopped rather than delete an unverified row."
            )
        if owner_type != body.get("owner_type") or owner != str(body.get("owner_id")):
            raise RuntimeError(
                "Create returned a different owner than requested; no cleanup ownership inferred."
            )
        self.state["owned_entitlements"][eid] = {
            "sku_id": data.get("sku_id", body.get("sku_id")),
            "owner_id": owner,
            "owner_type": owner_type,
            "deleted": False,
            "create_ref": receipt,
        }

    def settle_write(self, intent, status, data, receipt):
        path, method = intent["path"], intent["method"]
        if status is None:
            return
        if method == "POST" and path == self.base and 200 <= status < 300:
            self.received_create(intent.get("body") or {}, data, receipt)
        elif method == "POST" and path.endswith("/commands") and 200 <= status < 300:
            if not isinstance(data, dict) or not data.get("id"):
                raise RuntimeError("Command create returned no ID; inspect evidence before retrying.")
            guild = path.split("/")[-2]
            self.state["temporary_commands"][guild] = {
                "id": data["id"],
                "create_ref": receipt,
                "deleted": False,
            }
            if guild == str(self.args.guild):
                self.state["temporary_command_id"] = data["id"]
                self.state["created_command_id"] = data["id"]
                self.state["command_create_ref"] = receipt
        elif method == "DELETE" and (
            status == 204 or (status == 404 and isinstance(data, dict) and data.get("code") == 10029)
        ):
            eid = path.rsplit("/", 1)[-1]
            if eid in self.state["owned_entitlements"]:
                self.state["owned_entitlements"][eid]["deleted"] = True
        if method == "DELETE" and "/commands/" in path and status in (204, 404):
            guild, command = path.split("/")[-3], path.split("/")[-1]
            known = self.state["temporary_commands"].get(guild)
            if known and known["id"] == command:
                known["deleted"] = True
            if guild == str(self.args.guild):
                self.state["temporary_command_id"] = None
        self.state["pending_write"] = None

    async def request(self, case, method, path, *, query=None, body=None):
        self.evidence.action = case
        self.evidence.record("actions", event="begin")
        if not case.startswith("pagination.populate"):
            print(case, flush=True)
        before = dict(self.evidence.counts)
        intent = {
            "case": case,
            "method": method,
            "path": path,
            "query": query,
            "body": body,
            "request_start_line": before.get("requests", 0) + 1,
        }
        if method != "GET":
            if self.state.get("uncertain_writes") and not self.cleaning:
                raise RuntimeError("An unresolved mutation exists; do not replay it.")
            if self.state.get("pending_write"):
                if not self.cleaning:
                    raise RuntimeError("An unresolved mutation exists; do not replay it.")
                self.state.setdefault("uncertain_writes", []).append(self.state["pending_write"])
            self.state["pending_write"] = intent
            self.save()
        try:
            await asyncio.sleep(max(0, self.args.request_interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            status, data = await self.rest.request(method, path, query=query, body=body)
            refs = references(self.evidence, before)
            self.state["cases"][case] = {
                **self.state["cases"].get(case, {}),
                "status": "observed",
                **refs,
                "http_status": status,
                "error_code": data.get("code") if isinstance(data, dict) else None,
            }
            if method != "GET":
                self.settle_write(intent, status, data, refs["rest"][-1])
                self.state["cases"][case]["mutation_complete"] = True
                if method == "POST" and path == self.base and 200 <= status < 300:
                    self.state["cases"][case]["created_id"] = str(data["id"])
            self.save()
            return status, data
        except BaseException:
            self.state["cases"][case] = {
                **self.state["cases"].get(case, {}),
                "status": "capture_failed",
                **references(self.evidence, before),
            }
            if method != "GET":
                self.state["status"] = "uncertain_write"
            self.save()
            raise

    async def create(self, case, sku, *, owner_id=None, owner_type=None, extra=None):
        body = {
            "sku_id": sku["id"],
            "owner_id": str(owner_id or self.owner_id),
            "owner_type": self.owner_type if owner_type is None else owner_type,
        }
        body.update(extra or {})
        status, data = await self.request(case, "POST", self.base, body=body)
        return str(data["id"]) if 200 <= status < 300 else None

    async def remove(self, case, entitlement_id):
        if entitlement_id not in self.state["owned_entitlements"]:
            raise RuntimeError("Refusing cleanup without a positive create receipt.")
        return await self.request(case, "DELETE", f"{self.base}/{entitlement_id}")

    def owner_query(self, owner_id=None, owner_type=None):
        owner_type = self.owner_type if owner_type is None else owner_type
        return {"guild_id" if owner_type == 1 else "user_id": str(owner_id or self.owner_id)}

    async def list_rows(self, case, *, query=None):
        status, data = await self.request(case, "GET", self.base, query={"limit": "100", **(query or {})})
        if status != 200 or not isinstance(data, list):
            self.block(case + ".rows", "List did not return a usable page; HTTP outcome retained.")
            return None
        return data

    async def inspect(self, case, entitlement_id, sku, *, owner_id=None, owner_type=None):
        status, data = await self.request(case + ".get", "GET", f"{self.base}/{entitlement_id}")
        query = {**self.owner_query(owner_id, owner_type), "sku_ids": sku["id"]}
        await self.list_rows(case + ".default", query=query)
        await self.list_rows(case + ".deleted", query={**query, "exclude_deleted": "false"})
        await self.list_rows(
            case + ".ended", query={**query, "exclude_deleted": "false", "exclude_ended": "false"}
        )
        return data if status == 200 else None

    async def observe(self, case):
        self.evidence.mark(case)
        print(f"Observing Gateway for {self.args.observe:g} seconds; no action needed.", flush=True)
        before = dict(self.evidence.counts)
        errors, epoch = self.client.health_errors, self.client.connection_epoch
        start = self.evidence.record(
            "health",
            event="observation_start",
            requested_seconds=self.args.observe,
            connected=self.client.is_ready(),
            sequence=self.client.last_sequence,
            connection_epoch=epoch,
            health_errors=errors,
            sequence_contiguous=True,
        )
        await asyncio.sleep(self.args.observe)
        connected = self.client.is_ready() and not self.gateway.done()
        continuous = errors == self.client.health_errors and epoch == self.client.connection_epoch
        end = self.evidence.record(
            "health",
            event="observation_end",
            requested_seconds=self.args.observe,
            connected=connected,
            sequence=self.client.last_sequence,
            connection_epoch=self.client.connection_epoch,
            health_errors=self.client.health_errors,
            sequence_contiguous=continuous,
        )
        self.state["cases"][case] = {
            "status": "observed"
            if connected and continuous and not self.client.capture_failed.is_set()
            else "capture_failed",
            **references(self.evidence, before),
            "health": [start, end],
        }
        self.save()
        if self.client.capture_failed.is_set() or self.gateway.done():
            raise RuntimeError("Gateway capture failed; preserve evidence before recovery.")

    async def probe(self, case, *, actor_id=None, guild_id=None):
        guild_id = str(guild_id or self.args.guild)
        target = self.state.get("probe_channels", {}).get(guild_id)
        command = self.state.get("temporary_commands", {}).get(guild_id)
        if not target or not command or command["deleted"]:
            raise RuntimeError("Requested probe guild has no verified channel and owned command.")
        before = dict(self.evidence.counts)
        previous = (
            self.client.probe_guild,
            self.client.probe_guild_label,
            self.client.channel_id,
            self.client.command_id,
        )
        self.client.probe_guild, self.client.probe_guild_label = guild_id, target["label"]
        self.client.channel_id, self.client.command_id = target["channel_id"], command["id"]
        expected = {"expected_actor_id": str(actor_id or self.args.user), "expected_guild_id": guild_id}
        try:
            payload = await self.client.probe(case, actor_id=actor_id)
            self.state["cases"][case] = {
                "status": "observed",
                **expected,
                **references(self.evidence, before),
            }
            self.save()
            return payload
        except BaseException:
            self.state["cases"][case] = {
                "status": "capture_failed",
                **expected,
                **references(self.evidence, before),
            }
            self.save()
            raise
        finally:
            (
                self.client.probe_guild,
                self.client.probe_guild_label,
                self.client.channel_id,
                self.client.command_id,
            ) = previous

    async def walk(self, case, query):
        rows = await self.list_rows(case + ".000", query=query)
        if rows is None:
            return None
        found = {row["id"]: row for row in rows}
        if len(rows) < 100:
            return list(found.values())
        # Explore both cursor directions; do not assume ascending or descending server order.
        for cursor, choose in (("before", min), ("after", max)):
            page = rows
            for number in range(1, self.args.max_pages + 1):
                boundary = str(choose(int(row["id"]) for row in page))
                page = await self.list_rows(f"{case}.{cursor}.{number:03}", query={**query, cursor: boundary})
                if page is None:
                    return None
                new = {row["id"]: row for row in page if row["id"] not in found}
                found.update(new)
                if not page or len(page) < 100:
                    break
                if not new:
                    self.block(
                        case + ".traversal",
                        "Cursor did not advance; raw pages retained, traversal incomplete.",
                    )
                    return None
            else:
                self.block(
                    case + ".traversal",
                    "Page safety ceiling reached; increase --max-pages only after inspecting evidence.",
                )
                return None
        return list(found.values())

    async def cleanup(self):
        self.cleaning = True
        errors = []
        for eid, owned in list(self.state["owned_entitlements"].items()):
            if not owned["deleted"]:
                try:
                    status, data = await self.remove(f"cleanup.entitlement.{eid}", eid)
                    if not (
                        status == 204
                        or (status == 404 and isinstance(data, dict) and data.get("code") == 10029)
                    ):
                        errors.append(f"Unresolved owned entitlement {eid}")
                except Exception:
                    errors.append(f"Unresolved owned entitlement {eid}")
        for guild, known in self.state["temporary_commands"].items():
            command = known["id"]
            if known["deleted"]:
                continue
            try:
                path = f"/applications/{self.app['id']}/guilds/{guild}/commands/{command}"
                status, _ = await self.request(f"cleanup.command.{guild}", "DELETE", path)
                if status not in (204, 404):
                    errors.append(f"Temporary command {command} in guild {guild} remains")
            except Exception:
                errors.append(f"Temporary command {command} in guild {guild} remains")
        self.state["cleanup_errors"] = errors
        self.cleaning = False
        self.save()


def source_purchases(ctx):
    ids = {}
    root = Path(".discord-reference-captures")
    for path in sorted(root.glob("monetization-*/manifest.json")):
        try:
            data = read_json(path)
        except (ValueError, OSError):
            continue
        if (
            data.get("application_id") == ctx.app["id"]
            and data.get("user_id") == str(ctx.args.user)
            and data.get("status") == "completed"
        ):
            source = str(path.parent.resolve())
            if not any(item["path"] == source for item in ctx.state["source_bundles"]):
                files = [
                    path.parent / name
                    for name in (
                        "manifest.json",
                        "requests.jsonl",
                        "rest.jsonl",
                        "gateway.jsonl",
                        "interactions.jsonl",
                        "health.jsonl",
                        "actions.jsonl",
                    )
                ]
                ctx.state["source_bundles"].append(
                    {
                        "path": source,
                        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "sha256": {
                            item.name: hashlib.sha256(item.read_bytes()).hexdigest()
                            for item in files
                            if item.exists()
                        },
                    }
                )
            if data.get("mode") == "purchases":
                for eid in data.get("purchase_entitlement_ids", []):
                    ids[eid] = source
    ctx.save()
    return ids


async def rejection_matrix(ctx):
    sku = ctx.subscriptions[0]
    unknown_sku = {"id": "1"}

    async def negative(case, item, **kwargs):
        eid = await ctx.create(case, item, **kwargs)
        if eid:
            status, _ = await ctx.remove(case + ".cleanup", eid)
            if status != 204:
                raise RuntimeError(
                    "An unexpectedly accepted negative probe needs cleanup; stopped before further fixtures."
                )

    if any(item["id"] == "1" for item in ctx.skus):
        ctx.block(
            "reject.unknown_sku", "Sentinel SKU unexpectedly exists; no unknown-SKU mutation attempted."
        )
    else:
        await negative("reject.unknown_sku", unknown_sku)
        for label, extra in (
            ("owner_type", {"owner_type": 0}),
            ("owner_id", {"owner_id": "not-a-snowflake"}),
        ):
            await negative(f"reject.precedence.{label}.known", sku, extra=extra)
            await negative(f"reject.precedence.{label}.unknown", unknown_sku, extra=extra)
    status, data = await ctx.request("reject.unknown_entitlement.get", "GET", f"{ctx.base}/1")
    if status == 404 and isinstance(data, dict) and data.get("code") == 10029:
        await ctx.request("reject.unknown_entitlement.consume", "POST", f"{ctx.base}/1/consume")
        await ctx.request("reject.unknown_entitlement.delete", "DELETE", f"{ctx.base}/1")
    else:
        ctx.block(
            "reject.unknown_entitlement.mutations",
            "Sentinel was not positively confirmed unknown; no mutation attempted.",
        )
    for name, kind in ((ctx.args.consumable, 3), (ctx.args.durable, 2)):
        candidates = [item for item in ctx.skus if item["name"] == name and item["type"] == kind]
        if len(candidates) == 1:
            case = f"reject.one_time_test_create.{kind}"
            await negative(case, candidates[0], owner_id=ctx.args.user, owner_type=2)
            if ctx.args.other_user:
                await negative(
                    case + ".second_owner", candidates[0], owner_id=ctx.args.other_user, owner_type=2
                )
            elif ctx.state["cases"][case].get("error_code") == 40074:
                ctx.block(
                    case + ".clean_owner",
                    "Existing ownership confounds SKU-kind rejection; a consenting second owner is needed.",
                )
        else:
            ctx.block(
                f"reject.one_time_test_create.{kind}",
                f"Need one existing SKU named {name!r}; no publication needed.",
            )
    grant = await ctx.create("reject.subscription_fixture", sku)
    if grant:
        await ctx.request("reject.subscription_consume", "POST", f"{ctx.base}/{grant}/consume")
        await ctx.inspect("reject.subscription_after_consume", grant, sku)
        await ctx.remove("reject.subscription_cleanup", grant)
    else:
        ctx.block(
            "reject.subscription_consume",
            "Could not create a subscription test fixture; create response retained.",
        )
    sources = source_purchases(ctx)
    if not sources:
        ctx.block(
            "reject.non_sandbox_delete",
            "No completed purchase bundle for this app/user; existing purchases are not adopted blindly.",
        )
    for eid, source in sources.items():
        case = f"reject.non_sandbox_delete.{eid}"
        status, row = await ctx.request(case + ".before", "GET", f"{ctx.base}/{eid}")
        if (
            status != 200
            or not isinstance(row, dict)
            or row.get("type") != 4
            or row.get("application_id") != ctx.app["id"]
            or row.get("user_id") != str(ctx.args.user)
        ):
            ctx.block(case, "Source test-mode purchase could not be revalidated; no DELETE sent.")
            continue
        sku = next(
            (item for item in ctx.skus if item["id"] == row.get("sku_id") and item["type"] in (2, 3)), None
        )
        if sku is None:
            ctx.block(case, "Purchase SKU unavailable or not one-time; no DELETE sent.")
            continue
        ctx.state["cases"][case] = {
            "status": "blocked",
            "reason": "Authorized measurement pending.",
            "explicit_source_purchase": True,
            "source_bundle": source,
        }
        ctx.save()
        status, _ = await ctx.request(case, "DELETE", f"{ctx.base}/{eid}")
        await ctx.observe(case + ".observation")
        await ctx.inspect(case + ".after", eid, sku, owner_id=ctx.args.user, owner_type=2)
        if status == 204 or row.get("deleted") is True:
            await ctx.probe(case + ".interaction")
            if sku["type"] == 3:
                await ctx.request(case + ".deleted_consumable_consume", "POST", f"{ctx.base}/{eid}/consume")
                await ctx.observe(case + ".consume_observation")
                await ctx.inspect(case + ".after_consume", eid, sku, owner_id=ctx.args.user, owner_type=2)
                ctx.block(
                    "consume.deleted_unconsumed",
                    "DELETE probe used a previously consumed purchase; it does not establish the unconsumed-deleted case.",
                )
    await ctx.observe("reject.matrix.observation")


async def duplicate_matrix(ctx):
    a, b = ctx.subscriptions
    first = await ctx.create("duplicate.initial_a", a)
    if not first:
        ctx.block("duplicate.matrix", "Initial fixture rejected; no preexisting grant will be modified.")
        return
    await ctx.inspect("duplicate.initial_projection", first, a)
    await ctx.create("duplicate.same_owner_same_sku", a)
    await ctx.create("duplicate.same_owner_different_sku", b)
    other = ctx.args.other_user if ctx.owner_type == 2 else ctx.args.other_guild
    if other:
        await ctx.create("duplicate.different_owner_same_sku", a, owner_id=other)
        await ctx.create("duplicate.different_owner_different_sku", b, owner_id=other)
    else:
        ctx.block(
            "duplicate.different_owner",
            "Supply --other-user for a consenting second user, or --other-guild for another authorized guild.",
        )
    await ctx.observe("duplicate.active.observation")
    for eid, owned in list(ctx.state["owned_entitlements"].items()):
        if not owned["deleted"]:
            await ctx.remove(f"duplicate.delete.{eid}", eid)
    await ctx.observe("duplicate.deleted.observation")
    await ctx.inspect("duplicate.deleted_projection", first, a)
    await ctx.remove("duplicate.repeat_delete", first)
    after = await ctx.create("duplicate.after_delete_other_sku", b)
    if after:
        await ctx.create("duplicate.a_while_b_active", a)
        await ctx.remove("duplicate.delete_b", after)
    final = await ctx.create("duplicate.after_delete_same_sku", a)
    if final:
        await ctx.remove("duplicate.final_cleanup", final)
    for eid, owned in list(ctx.state["owned_entitlements"].items()):
        if not owned["deleted"]:
            await ctx.remove(f"duplicate.remaining_cleanup.{eid}", eid)


async def cross_scope(ctx):
    """Capture same-app coexistence without assuming unpublished scopes are incompatible."""
    matches = [
        sku
        for sku in ctx.skus
        if sku["name"] == ctx.args.user_sku_a
        and sku["type"] == 5
        and sku.get("flags", 0) & discord.SKUFlags.user_subscription.flag
    ]
    if len(matches) != 1:
        ctx.block(
            "cross_scope.prerequisite", "Need one compatible personal subscription SKU for the comparison."
        )
        return
    personal, guild_sku = matches[0], ctx.subscriptions[0]
    existing = await ctx.list_rows(
        "cross_scope.personal_baseline",
        query={
            "user_id": str(ctx.args.user),
            "sku_ids": personal["id"],
            "exclude_ended": "true",
        },
    )
    if existing is None or existing:
        ctx.block(
            "cross_scope.prerequisite",
            "Personal comparison SKU has an existing active grant or unreadable baseline; no grant was adopted.",
        )
        return
    for first_kind in ("guild", "user"):
        phase = f"cross_scope.{first_kind}_first"
        fixtures = ((guild_sku, ctx.args.guild, 1), (personal, ctx.args.user, 2))
        if first_kind == "user":
            fixtures = fixtures[::-1]
        created = []
        try:
            for sku, owner, owner_type in fixtures:
                eid = await ctx.create(
                    f"{phase}.create.{owner_type}", sku, owner_id=owner, owner_type=owner_type
                )
                if eid:
                    created.append(eid)
                    await ctx.inspect(
                        f"{phase}.state.{owner_type}", eid, sku, owner_id=owner, owner_type=owner_type
                    )
            await ctx.observe(phase + ".window")
            await ctx.probe(phase + ".primary")
            if ctx.args.other_user:
                await ctx.probe(phase + ".second_member", actor_id=ctx.args.other_user)
            if ctx.args.other_guild:
                await ctx.probe(phase + ".other_guild", guild_id=ctx.args.other_guild)
        finally:
            for eid in dict.fromkeys(created):
                if not ctx.state["owned_entitlements"][eid]["deleted"]:
                    await ctx.remove(f"{phase}.remove.{eid}", eid)
    for label, sku, owner, owner_type in (
        ("guild_sku_user_owner", guild_sku, ctx.args.user, 2),
        ("user_sku_guild_owner", personal, ctx.args.guild, 1),
    ):
        eid = await ctx.create(f"cross_scope.mismatched.{label}", sku, owner_id=owner, owner_type=owner_type)
        if eid:
            owned = ctx.state["owned_entitlements"][eid]
            await ctx.inspect(
                f"cross_scope.mismatched.{label}.state",
                eid,
                sku,
                owner_id=owned["owner_id"],
                owner_type=owned["owner_type"],
            )
            await ctx.remove(f"cross_scope.mismatched.{label}.remove", eid)


async def pagination(ctx):
    sku = ctx.subscriptions[0]
    query = {**ctx.owner_query(), "sku_ids": sku["id"], "exclude_deleted": "false", "exclude_ended": "false"}
    known = await ctx.walk("pagination.baseline", query)
    if known is None:
        ctx.block("pagination.large", "Could not safely enumerate history; no bulk population attempted.")
        return
    target = ctx.args.pagination_target
    if len(known) < target:
        first = await ctx.create("pagination.populate.proof.create", sku)
        if not first:
            ctx.block("pagination.large", "Sandbox create rejected; no bulk population possible.")
            return
        await ctx.remove("pagination.populate.proof.delete", first)
        proof = await ctx.walk("pagination.history_proof", query)
        if proof is None or first not in {row["id"] for row in proof}:
            ctx.block(
                "pagination.large",
                "Deleted history was not listable; no repeated grant/delete loop attempted.",
            )
            return
        known = proof
        for number in range(max(0, target - len(known))):
            if number % 10 == 0:
                print(
                    f"Pagination history: {len(known) + number}/{target} rows; no manual action needed.",
                    flush=True,
                )
            eid = await ctx.create(f"pagination.populate.{number:03}.create", sku)
            if not eid:
                ctx.block("pagination.large", "Population create rejected; partial history retained.")
                break
            status, _ = await ctx.remove(f"pagination.populate.{number:03}.delete", eid)
            if status != 204:
                ctx.block("pagination.large", "Population delete was not confirmed; loop stopped safely.")
                break
    rows = await ctx.walk("pagination.traversal", query)
    if not rows:
        ctx.block("pagination.cursors", "No stable history dataset for cursor comparisons.")
        return
    if len(rows) <= 100:
        ctx.block(
            "pagination.large",
            "Fewer than 101 distinct scoped rows; small pages do not close large traversal.",
        )
    ctx.state["pagination"] = {"scoped_distinct_ids": [row["id"] for row in rows], "query": query}
    ctx.save()
    ordered = sorted(int(row["id"]) for row in rows)
    anchors = sorted({ordered[len(ordered) // 4], ordered[len(ordered) // 2], ordered[3 * len(ordered) // 4]})
    configurations = {
        "unfiltered": {"exclude_deleted": "false"},
        "owner": {**ctx.owner_query(), "exclude_deleted": "false"},
        "sku": {"sku_ids": sku["id"], "exclude_deleted": "false"},
        "owner_sku": query,
        "multi_sku": {
            **ctx.owner_query(),
            "sku_ids": ",".join(item["id"] for item in ctx.subscriptions),
            "exclude_deleted": "false",
        },
        "user_and_guild": {
            "user_id": str(ctx.args.user),
            "guild_id": str(ctx.args.guild),
            "exclude_deleted": "false",
        },
    }
    for label, filters in configurations.items():
        await ctx.walk(f"pagination.{label}.traversal", filters)
        await ctx.list_rows(f"pagination.{label}.small", query={**filters, "limit": "2"})
        for anchor in anchors:
            for cursor in ("before", "after"):
                for limit in (2, 100):
                    await ctx.list_rows(
                        f"pagination.{label}.{cursor}.{anchor}.{limit}",
                        query={**filters, cursor: str(anchor), "limit": str(limit)},
                    )
    await ctx.observe("pagination.final.observation")


async def connect(ctx):
    await ctx.client.login(ctx.evidence.secret)
    ctx.gateway = asyncio.create_task(ctx.client.connect(reconnect=True))
    ready = asyncio.create_task(ctx.client.wait_until_ready())
    try:
        done, _ = await asyncio.wait({ready, ctx.gateway}, timeout=60, return_when=asyncio.FIRST_COMPLETED)
        if ctx.gateway in done:
            await ctx.gateway
            raise RuntimeError("Gateway ended before READY.")
        if ready not in done:
            raise RuntimeError("Gateway READY timed out.")
        await ready
    finally:
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
    ctx.state["probe_channels"] = {}
    targets = [ctx.args.guild] + ([ctx.args.other_guild] if ctx.args.other_guild else [])
    for guild_id in targets:
        guild = ctx.client.get_guild(guild_id)
        if guild is None:
            raise RuntimeError(f"Bot is not installed in authorized server {guild_id}.")
        channels = [channel for channel in guild.text_channels if channel.name == ctx.args.channel]
        if len(channels) != 1:
            raise RuntimeError(f"Need exactly one #{ctx.args.channel} text channel in server {guild_id}.")
        ctx.state["probe_channels"][str(guild_id)] = {
            "channel_id": str(channels[0].id),
            "label": f"{getattr(guild, 'name', guild_id)} ({guild_id})",
        }
        if ctx.args.mode == "guild":
            for actor in (ctx.args.user, ctx.args.other_user):
                if actor:
                    status, member = await ctx.request(
                        f"setup.member.{guild_id}.{actor}", "GET", f"/guilds/{guild_id}/members/{actor}"
                    )
                    if (
                        status != 200
                        or not isinstance(member, dict)
                        or member.get("user", {}).get("id") != str(actor)
                        or member["user"].get("bot")
                    ):
                        raise RuntimeError(
                            f"Consenting user {actor} must be a human member of server {guild_id}."
                        )
    for guild_id in targets:
        commands_path = f"/applications/{ctx.app['id']}/guilds/{guild_id}/commands"
        status, commands = await ctx.request(f"setup.commands.{guild_id}", "GET", commands_path)
        if status != 200 or not isinstance(commands, list):
            raise RuntimeError("Could not inspect existing commands.")
        if any(command["name"] == COMMAND for command in commands):
            raise RuntimeError(
                "Probe command already exists; stopped rather than overwrite it. Recover its original capture first."
            )
        status, _ = await ctx.request(
            f"setup.command_create.{guild_id}",
            "POST",
            commands_path,
            body={
                "name": COMMAND,
                "description": "Record authorized monetization parity evidence",
                "type": 1,
            },
        )
        if not 200 <= status < 300:
            raise RuntimeError("Could not create the temporary command; HTTP evidence retained.")
    primary = ctx.state["probe_channels"][str(ctx.args.guild)]
    ctx.state["channel_id"] = primary["channel_id"]
    ctx.client.channel_id = primary["channel_id"]
    ctx.client.command_id = ctx.state["temporary_commands"][str(ctx.args.guild)]["id"]


def reconcile(ctx):
    """Only recorded HTTP receipts may resolve an interrupted write; never infer provenance from a list."""
    intents = ctx.state.get("uncertain_writes", []) + (
        [ctx.state["pending_write"]] if ctx.state.get("pending_write") else []
    )
    ctx.state["uncertain_writes"] = []
    path = ctx.evidence.directory / "rest.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    request_path = ctx.evidence.directory / "requests.jsonl"
    sent = (
        [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()]
        if request_path.exists()
        else []
    )
    for intent in intents:
        receipts = [
            (index, read)
            for index, line in enumerate(rows, 1)
            if (read := json.loads(line)).get("action") == intent["case"]
            and read.get("method") == intent["method"]
            and urlsplit(read.get("path_query", "")).path.removeprefix("/api/v10") == intent["path"]
            and int(read.get("request_id", "requests.jsonl:0").rsplit(":", 1)[-1])
            >= intent["request_start_line"]
        ]
        if receipts and receipts[-1][1].get("status") is not None:
            index, receipt = receipts[-1]
            ctx.settle_write(intent, receipt["status"], receipt.get("response"), f"rest.jsonl:{index}")
            ctx.state["cases"][intent["case"]] = {
                **ctx.state["cases"].get(intent["case"], {}),
                "status": "observed",
                "rest": [f"rest.jsonl:{index}"],
                "requests": [receipt["request_id"]],
                "mutation_complete": True,
            }
            if intent["method"] == "POST" and intent["path"] == ctx.base and 200 <= receipt["status"] < 300:
                ctx.state["cases"][intent["case"]]["created_id"] = str(receipt["response"]["id"])
        elif not any(
            row.get("action") == intent["case"]
            and row.get("method") == intent["method"]
            and row.get("path") == intent["path"]
            and int(row.get("request_id", "requests.jsonl:0").rsplit(":", 1)[-1])
            >= intent["request_start_line"]
            for row in sent
        ):
            ctx.block(
                intent["case"],
                "Interrupted before HTTP I/O; no write was sent. Only this unfinished section needs recovery.",
            )
        else:
            ctx.state["uncertain_writes"].append(intent)
    ctx.state["pending_write"] = None
    ctx.save()


async def run(args, evidence, state):
    ctx = Capture(args, evidence, state)
    async with aiohttp.ClientSession(
        headers={
            "Authorization": f"Bot {evidence.secret}",
            "User-Agent": "SimCord authorized evidence recorder",
        },
        timeout=aiohttp.ClientTimeout(total=60),
    ) as http:
        ctx.rest = Rest(http, evidence)
        try:
            status, app = await ctx.request("setup.application", "GET", "/oauth2/applications/@me")
            if status != 200 or not isinstance(app, dict):
                raise RuntimeError("Application authentication failed; inspect setup evidence.")
            if state.get("application_id") and state["application_id"] != app["id"]:
                raise RuntimeError("Token belongs to a different application than this recovery journal.")
            ctx.app, ctx.base = app, f"/applications/{app['id']}/entitlements"
            state.update(application_id=app["id"], application_name=app["name"])
            ctx.save()
            reconcile(ctx)
            if state.get("uncertain_writes") and not args.cleanup_only:
                raise RuntimeError(
                    "An unacknowledged write remains. No mutation will be replayed. Preserve the directory; use --cleanup-only for positively owned resources."
                )
            if args.resume:
                await ctx.cleanup()
                if state["cleanup_errors"]:
                    raise RuntimeError("Recovery cleanup is incomplete.")
            if args.cleanup_only:
                state["status"] = "completed_with_blockers"
                ctx.block(
                    "recovery.cleanup_only",
                    "Only positive-provenance cleanup requested; remaining evidence was not captured.",
                )
                return
            if app.get("interactions_endpoint_url"):
                raise RuntimeError("App has an interactions endpoint; no settings changed.")
            status, skus = await ctx.request("setup.skus", "GET", f"/applications/{app['id']}/skus")
            if status != 200 or not isinstance(skus, list):
                raise RuntimeError("Could not inspect the SKU catalog.")
            ctx.skus = skus
            ctx.subscriptions, ctx.owner_type = subscription_catalog(
                skus, (args.sku_a, args.sku_b), args.scope
            )
            ctx.owner_id = str(args.user if ctx.owner_type == 2 else args.guild)
            state.update(owner_id=ctx.owner_id, owner_type=ctx.owner_type, skus=skus)
            if state.get("expiry_source") and not await prepare_expiry(ctx):
                state["status"] = "completed_with_blockers"
                return
            baseline = await ctx.walk(
                "setup.baseline", {"exclude_deleted": "false", "exclude_ended": "false"}
            )
            if baseline is None:
                raise RuntimeError("Cannot establish preexisting IDs safely; no mutation attempted.")
            state["baseline_ids"] = sorted(
                set(state.get("baseline_ids", [])) | {row["id"] for row in baseline}
            )
            ctx.save()
            for owner in (
                ()
                if args.mode in ("watch", "upgrade")
                else (ctx.owner_id, str(args.other_user if ctx.owner_type == 2 else args.other_guild))
            ):
                if owner == "None":
                    continue
                active = await ctx.list_rows(
                    f"setup.active.{owner}",
                    query={
                        **ctx.owner_query(owner),
                        "sku_ids": ",".join(sku["id"] for sku in ctx.subscriptions),
                        "exclude_ended": "true",
                    },
                )
                if active is None or active:
                    raise RuntimeError(
                        "Preexisting active grant or unreadable baseline; it will not be modified. Resolve this prerequisite before proceeding."
                    )
            await connect(ctx)
            if state.get("expiry_source"):
                print(
                    "Connected. EXPIRY-ONLY: reusing your saved before interaction. No checkout, upgrade, or repeat purchase. After the entitlement ends, one probe command is needed; other requests and sandbox cleanup are automatic.",
                    flush=True,
                )
            elif args.mode == "upgrade":
                print(
                    "Connected. UPGRADE SESSION: reuse/resume A or start a free A if needed, upgrade to B, record scheduled downgrade, then cancel. Follow each prompt; do not buy anything beforehand.",
                    flush=True,
                )
            else:
                print(
                    "Connected. HTTP rejections are captured outcomes, not failures. Observation windows show their duration; bulk population needs no manual action.",
                    flush=True,
                )
            if args.mode == "guild":
                jobs = [
                    ("guild", capture_guild),
                    ("guild_duplicates", duplicate_matrix),
                    ("cross_scope", cross_scope),
                    ("guild_pagination", pagination),
                ]
            elif args.mode == "remaining":
                jobs = [
                    ("rejections", rejection_matrix),
                    ("duplicates", duplicate_matrix),
                    ("pagination", pagination),
                    ("guild", capture_guild),
                ]
            else:
                jobs = []
            if getattr(args, "deleted_purchase", False) or getattr(args, "live_upgrade", False):
                jobs.append(("checkouts", capture_checkouts))
            if args.mode != "upgrade":
                jobs.append(("lifecycle", capture_lifecycle))
            for name, job in jobs:
                if name == "checkouts":
                    stages = state.get("checkout", {})
                    if any(
                        getattr(args, flag, False) and not stages.get(flag, {}).get("done")
                        for flag in ("deleted_purchase", "live_upgrade")
                    ):
                        state["sections"].setdefault(name, {})["done"] = False
                if state["sections"].get(name, {}).get("done") and not (
                    args.mode == "watch" and name == "lifecycle"
                ):
                    print(f"Already captured: {name}; preserving prior evidence.", flush=True)
                    continue
                state["sections"].setdefault(name, {})["done"] = False
                ctx.save()
                await job(ctx)
                live = state.get("checkout", {}).get("live_upgrade", {})
                if live.get("cancellation_required") and not live.get("cancellation_verified"):
                    raise RuntimeError(
                        "Real subscription cancellation remains unverified. Cancel in Discord and resume this capture without purchasing again."
                    )
                if any(case["status"] == "capture_failed" for case in state["cases"].values()):
                    raise RuntimeError(
                        "A capture failed; this section is not checkpointed complete. Preserve the journal for recovery."
                    )
                ctx.finish_section(name)
            state["status"] = (
                "completed_with_blockers"
                if any(case["status"] == "blocked" for case in state["cases"].values())
                else "completed"
            )
        except asyncio.CancelledError:
            if state["status"] != "uncertain_write":
                state["status"] = "interrupted"
            raise
        except Exception:
            if state["status"] != "uncertain_write":
                state["status"] = "incomplete"
            raise
        finally:
            if ctx.app:
                await ctx.cleanup()
            if state.get("uncertain_writes") or state.get("pending_write"):
                state["status"] = "uncertain_write"
            elif state["cleanup_errors"]:
                state["status"] = "cleanup_required"
            live = state.get("checkout", {}).get("live_upgrade", {})
            if live.get("cancellation_required") and not live.get("cancellation_verified"):
                print(
                    "ACTION REQUIRED: a REAL KaruMart subscription may still renew. Cancel every subscription started here in Discord User Settings -> Subscriptions. Resume this directory; DO NOT purchase again.",
                    flush=True,
                )
                if state["status"] not in ("uncertain_write", "cleanup_required"):
                    state["status"] = "cancellation_required"
            ctx.save()
            ctx.evidence.action = "cleanup"
            await ctx.client.close()
            if ctx.gateway:
                ctx.gateway.cancel()
                await asyncio.gather(ctx.gateway, return_exceptions=True)
            report = validate_bundle(evidence.directory)
            report["prior_bundles"] = [
                {"path": item["path"], "report": validate_bundle(Path(item["path"]))}
                for item in state["source_bundles"]
            ]
            evidence.directory.joinpath("validation.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            print(f"Session: {state['status']}. Evidence: {evidence.directory.resolve()}", flush=True)
            print(
                f"Evidence valid: {report['valid']}. Pending prerequisites: {len(report.get('pending_prerequisites', []))}.",
                flush=True,
            )
            if not report["valid"]:
                print(
                    "Preserve this directory. Do not start a new capture; inspect validation.json before recovery.",
                    flush=True,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guild", type=int)
    parser.add_argument("--user", type=int)
    parser.add_argument("--other-user", type=int, help="Consenting second test user; optional")
    parser.add_argument(
        "--other-guild", type=int, help="Second authorized guild for guild duplicate comparisons"
    )
    parser.add_argument("--scope", choices=("auto", "user", "guild"), default="auto")
    parser.add_argument("--mode", choices=("remaining", "watch", "guild", "upgrade"))
    parser.add_argument("--sku-a", default="Parity Subscription A")
    parser.add_argument("--sku-b", default="Parity Subscription B")
    parser.add_argument("--user-sku-a", default="Parity Subscription A")
    parser.add_argument("--user-sku-b", default="Parity Subscription B")
    parser.add_argument(
        "--deleted-purchase",
        action="store_true",
        help="Manually buy one new type-4 consumable and delete it before consumption",
    )
    parser.add_argument("--test-mode-confirmed", action="store_true")
    parser.add_argument(
        "--live-upgrade",
        action="store_true",
        help="Operator-only zero-total team subscription upgrade and cancellation capture",
    )
    parser.add_argument("--team-discount-confirmed", action="store_true")
    parser.add_argument("--consumable", default="Parity Consumable")
    parser.add_argument("--durable", default="Parity Durable")
    parser.add_argument("--channel", default="parity")
    parser.add_argument("--observe", type=float, default=60)
    parser.add_argument("--request-interval", type=float, default=0.25)
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=120,
        help="Read-only propagation window after subscription UI actions",
    )
    parser.add_argument(
        "--pagination-target", type=int, default=101, help="Controlled history target, at least 101"
    )
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--watch-entitlement", action="append", default=[])
    parser.add_argument(
        "--expiry-from",
        help="Expiry-only follow-up using a completed canceled-subscription capture; no checkout",
    )
    parser.add_argument(
        "--upgrade-from",
        help="Immediate upgrade/downgrade session bound to a previous verified-canceled A capture",
    )
    parser.add_argument(
        "--probe-timeout", type=float, default=900, help="Seconds allowed for each manual interaction"
    )
    parser.add_argument("--consume-history", action="append", default=[])
    parser.add_argument(
        "--live-lifecycle-confirmed",
        action="store_true",
        help="Attest watched subscriptions are legitimate lifecycle fixtures, not API test grants",
    )
    parser.add_argument("--watch-seconds", type=float, default=3600)
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    state = None
    supplied = {argument.split("=", 1)[0] for argument in sys.argv[1:] if argument.startswith("--")}
    if args.resume:
        state = read_json(args.resume / "manifest.json")
        if state.get("schema_version") != 2:
            parser.error("Only the remaining-evidence runner's schema-2 journals can be resumed.")
        for name in ("guild", "user", "other_user", "other_guild"):
            old = state["config"].get(name)
            new = getattr(args, name)
            if new is not None and old is not None and new != old:
                parser.error(
                    "Recovery owner/server identities cannot change; use the original IDs or a separate capture."
                )
        for name, value in state["config"].items():
            if "--" + name.replace("_", "-") in supplied:
                continue
            setattr(args, name, value)
    try:
        if args.expiry_from and args.upgrade_from:
            parser.error("--expiry-from and --upgrade-from are separate sessions.")
        upgrade = load_expiry_source(args.upgrade_from) if args.upgrade_from else None
        expiry = load_expiry_source(args.expiry_from) if args.expiry_from else None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.error(f"Cannot prepare subscription source: {error}")
    if expiry:
        if args.deleted_purchase or args.live_upgrade or args.consume_history:
            parser.error("--expiry-from cannot run checkout or consumption stages.")
        for name, value in (("guild", int(expiry["guild_id"])), ("user", int(expiry["user_id"]))):
            if getattr(args, name) not in (None, value):
                parser.error("Expiry owner/server must match its source capture.")
            setattr(args, name, value)
        if args.other_user or args.other_guild or args.mode not in (None, "watch"):
            parser.error("Expiry-only capture uses only the original user/server and watch mode.")
        if args.watch_entitlement and args.watch_entitlement != [expiry["entitlement_id"]]:
            parser.error("Expiry entitlement must match its source capture.")
        args.mode, args.scope = "watch", "user"
        args.sku_a, args.sku_b = expiry["sku_a"], expiry["sku_b"]
        args.watch_entitlement, args.live_lifecycle_confirmed = [expiry["entitlement_id"]], True
        args.expiry_from = expiry["path"]
        if "--watch-seconds" not in supplied and not args.resume:
            args.watch_seconds = 14400
        if "--probe-timeout" not in supplied and not args.resume:
            args.probe_timeout = 86400
        if state is not None and state.get("expiry_source") != expiry:
            parser.error("Expiry source changed since this journal was prepared; preserve both for review.")
    if upgrade:
        if (
            args.deleted_purchase
            or args.consume_history
            or args.watch_entitlement
            or args.other_user
            or args.other_guild
        ):
            parser.error(
                "Upgrade-only capture does not run one-time purchases, lifecycle watching, or other owners."
            )
        if args.mode not in (None, "upgrade"):
            parser.error("--upgrade-from requires upgrade mode.")
        for name, value in (("guild", int(upgrade["guild_id"])), ("user", int(upgrade["user_id"]))):
            if getattr(args, name) not in (None, value):
                parser.error("Upgrade owner/server must match its source capture.")
            setattr(args, name, value)
        args.mode, args.scope, args.live_upgrade = "upgrade", "user", True
        args.sku_a = args.user_sku_a = upgrade["sku_a"]
        args.sku_b = args.user_sku_b = upgrade["sku_b"]
        args.channel, args.upgrade_from = upgrade["channel"], upgrade["path"]
        if state is not None and state.get("upgrade_source") != upgrade:
            parser.error("Upgrade source changed since this journal was prepared; preserve both for review.")
    args.mode = args.mode or "remaining"
    if args.mode == "upgrade":
        if not args.upgrade_from:
            parser.error(
                "Upgrade mode requires --upgrade-from DIRECTORY to bind the authorized app and owner."
            )
        args.live_upgrade = True
    if args.mode == "guild":
        if not args.other_guild or not args.other_user:
            parser.error(
                "The full guild pass requires --other-guild and --other-user, with both users in both servers."
            )
        args.scope = "guild"
        if not args.resume:
            if "--sku-a" not in supplied:
                args.sku_a = "Parity Guild Subscription A"
            if "--sku-b" not in supplied:
                args.sku_b = "Parity Guild Subscription B"
    if args.deleted_purchase and not args.test_mode_confirmed:
        parser.error(
            "--deleted-purchase requires --test-mode-confirmed; checkout must be Application Test Mode."
        )
    if args.live_upgrade and not args.team_discount_confirmed:
        parser.error(
            "--live-upgrade requires --team-discount-confirmed; every checkout must show 100% discount and 0.00 total."
        )
    if (
        not args.guild
        or not args.user
        or any(
            value is not None and value <= 0
            for value in (args.guild, args.user, args.other_user, args.other_guild)
        )
    ):
        parser.error("Supply positive authorized --guild and --user IDs.")
    if args.other_user == args.user or args.other_guild == args.guild:
        parser.error("The second owner must differ from the first.")
    if not all(
        math.isfinite(value)
        for value in (
            args.observe,
            args.request_interval,
            args.watch_seconds,
            args.poll_seconds,
            args.probe_timeout,
            args.settle_seconds,
        )
    ):
        parser.error("Observation, pacing, settling and watch durations must be finite.")
    if (
        args.observe < 1
        or args.request_interval < 0.1
        or args.pagination_target < 101
        or args.max_pages < 1
        or args.watch_seconds <= 0
        or args.poll_seconds <= 0
        or args.probe_timeout <= 0
        or args.settle_seconds <= 0
    ):
        parser.error(
            "Use observation >=1s, request interval >=0.1s, target >=101, positive watch/poll/settle times and page cap."
        )
    if args.cleanup_only and not args.resume:
        parser.error("--cleanup-only requires --resume DIRECTORY.")
    if any(not value.isdigit() or int(value) <= 0 for value in args.watch_entitlement + args.consume_history):
        parser.error("Entitlement IDs must be positive numeric snowflakes.")
    if args.watch_entitlement and not args.live_lifecycle_confirmed:
        parser.error(
            "Watching live lifecycles requires --live-lifecycle-confirmed; never substitute API test grants."
        )
    if args.output and args.output.exists() and not args.resume:
        parser.error(f"Output already exists. Preserve it and use --resume {args.output} instead.")
    if not sys.stdin.isatty():
        parser.error("Run in your own terminal for the hidden bot-token prompt.")
    token = getpass.getpass("Bot token (hidden; never saved): ").strip()
    if not token:
        parser.error("Empty bot token.")
    directory = (
        args.resume
        or args.output
        or Path(".discord-reference-captures")
        / ("remaining-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    )
    evidence = Evidence(directory, token, resume=bool(args.resume))
    if state is None:
        config = {
            key: value for key, value in vars(args).items() if key not in {"output", "resume", "cleanup_only"}
        }
        state = {
            "schema_version": 2,
            "mode": args.mode,
            "status": "running",
            "config": config,
            "guild_id": str(args.guild),
            "user_id": str(args.user),
            "other_user_id": str(args.other_user) if args.other_user else None,
            "other_guild_id": str(args.other_guild) if args.other_guild else None,
            "observation_seconds": args.observe,
            "cases": {},
            "sections": {},
            "owned_entitlements": {},
            "temporary_command_id": None,
            "pending_write": None,
            "cleanup_errors": [],
            "source_bundles": [],
            "watch_baselines": {},
            "discord_py": discord.__version__,
            "recorder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    if expiry:
        state["application_id"] = expiry["application_id"]
        state["expiry_source"] = expiry
        state["source_bundles"] = [{"path": expiry["path"], "sha256": expiry["sha256"]}]
    if upgrade:
        state["application_id"] = upgrade["application_id"]
        state["upgrade_source"] = upgrade
        state["source_bundles"] = [{"path": upgrade["path"], "sha256": upgrade["sha256"]}]
    if (
        state.get("config", {}).get("other_user") != args.other_user
        or state.get("config", {}).get("other_guild") != args.other_guild
    ):
        for section in ("duplicates", "guild"):
            state["sections"].setdefault(section, {})["done"] = False
    state["config"] = {
        key: value for key, value in vars(args).items() if key not in {"output", "resume", "cleanup_only"}
    }
    state["mode"] = args.mode
    state["other_user_id"] = str(args.other_user) if args.other_user else None
    state["other_guild_id"] = str(args.other_guild) if args.other_guild else None
    state.setdefault("runs", []).append(
        {
            "utc": datetime.now(UTC).isoformat(),
            "sha256": {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in (
                    "capture_remaining_monetization.py",
                    "capture_monetization_reference.py",
                    "monetization_reference_lifecycle.py",
                    "monetization_reference_checkout.py",
                    "monetization_reference_expiry.py",
                    "monetization_reference_upgrade.py",
                    "validate_monetization_reference.py",
                )
            },
        }
    )
    state["status"] = "running"
    evidence.manifest(**state)
    try:
        asyncio.run(run(args, evidence, state))
    except KeyboardInterrupt:
        print("Interrupted. Evidence and positive ownership journal preserved; do not restart from scratch.")
        raise SystemExit(130) from None
    except Exception as error:
        print(
            "Stopped:", redact(str(error) if isinstance(error, RuntimeError) else type(error).__name__, token)
        )
        print("Preserve evidence:", directory.resolve())
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
