"""Record authorized subscription sandbox or manual test-mode purchase sessions.

Run with --help. Credentials are read only from a hidden local terminal prompt.
--self-test exercises redaction and HTTP capture against a local server, not Discord.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
import discord
from monetization_reference_config import (
    CaptureConfig,
    CaptureIdentity,
    ProbeConfig,
    PurchaseCaptureConfig,
    RequestPolicy,
    SubscriptionCaptureConfig,
)
from monetization_reference_journal import (
    begin_write,
    require_command_ownership,
    require_entitlement_ownership,
)
from monetization_reference_transport import (
    REDACTED,
    Evidence,
    Rest,
    await_gateway_ready,
    redact,
    require_capture_transport,
)

COMMAND = "simcord_parity_probe"


def select_user_subscription(skus, name):
    matches = [
        sku
        for sku in skus
        if sku["name"] == name
        and sku["type"] == discord.SKUType.subscription.value
        and sku.get("flags", 0) & discord.SKUFlags.user_subscription.flag
        and not sku.get("flags", 0) & discord.SKUFlags.guild_subscription.flag
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Need exactly one user-subscription SKU named {name!r}; see rest.jsonl.")
    return matches[0]


def select_purchase_sku(skus, name, kind):
    matches = [sku for sku in skus if sku["name"] == name and sku["type"] == kind.value]
    if len(matches) != 1:
        raise RuntimeError(f"Need exactly one {kind.name} SKU named {name!r}; see rest.jsonl.")
    return matches[0]


def select_new_test_purchase(rows, baseline, sku, application_id, user_id):
    # ponytail: refuse a full page; add calibrated pagination before supporting 100+ rows per SKU/user.
    if len(rows) >= 100:
        raise RuntimeError("Purchase selection reached a full page; stopped rather than guess at history.")
    new = [row for row in rows if row.get("id") not in baseline]
    if len(new) != 1:
        raise RuntimeError(
            "Need exactly one new purchase since session start. Do not purchase again; inspect evidence."
        )
    row = new[0]
    if (
        not row.get("id")
        or row.get("type") != discord.EntitlementType.test_mode_purchase.value
        or row.get("application_id") != application_id
        or row.get("sku_id") != sku["id"]
        or row.get("user_id") != str(user_id)
        or row.get("guild_id") is not None
        or row.get("deleted") is not False
        or row.get("consumed", False) is not False
        or ("consumed" not in row and sku.get("type") != discord.SKUType.durable.value)
    ):
        raise RuntimeError(
            "New entitlement is not an unconsumed test-mode purchase for this app/SKU/user; stopped without consuming it."
        )
    return row


def validate_test_create(row, sku, application_id, user_id):
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or not row["id"].isdigit()
        or int(row["id"]) <= 0
        or type(row.get("type")) is not int
        or row["type"] != discord.EntitlementType.application_subscription.value
        or row.get("application_id") != application_id
        or row.get("sku_id") != sku["id"]
        or row.get("user_id") != str(user_id)
        or row.get("guild_id") is not None
        or row.get("deleted", False) is not False
    ):
        raise RuntimeError(
            "Create receipt did not verify the requested user test grant; no cleanup ownership inferred. Inspect evidence before retrying."
        )


class Recorder(discord.Client):
    def __init__(self, config: ProbeConfig, evidence):
        super().__init__(intents=discord.Intents(guilds=True), enable_debug_events=True)
        self.identity = config.identity
        self.probe_config = config
        self.evidence = evidence
        self.command_id: str | None = None
        self.channel_id: str | None = None
        self.waiting = False
        self.probes = asyncio.Queue(maxsize=1)
        self.capture_failed = asyncio.Event()
        self.authorized_users = {str(self.identity.user)}
        if self.identity.other_user:
            self.authorized_users.add(str(self.identity.other_user))
        self.probe_actor = str(self.identity.user)
        self.probe_guild = str(self.identity.guild)
        self.probe_guild_label = str(self.identity.guild)
        self.last_probe_payload = None
        self.last_sequence = None
        self.connection_epoch = 0
        self.health_errors = 0

    async def on_socket_raw_receive(self, payload):
        try:
            payload = json.loads(payload)
            event = payload.get("t")
            sequence = payload.get("s")
            if event == "READY":
                self.connection_epoch += 1
            elif (
                isinstance(sequence, int)
                and self.last_sequence is not None
                and sequence != self.last_sequence + 1
            ):
                self.health_errors += 1
                self.evidence.record(
                    "health",
                    event="sequence_gap",
                    previous=self.last_sequence,
                    sequence=sequence,
                    connection_epoch=self.connection_epoch,
                )
            if isinstance(sequence, int):
                self.last_sequence = sequence
            if event in {
                "ENTITLEMENT_CREATE",
                "ENTITLEMENT_UPDATE",
                "ENTITLEMENT_DELETE",
                "SUBSCRIPTION_CREATE",
                "SUBSCRIPTION_UPDATE",
                "SUBSCRIPTION_DELETE",
            }:
                if (
                    not event.startswith("SUBSCRIPTION_")
                    or payload.get("d", {}).get("user_id") in self.authorized_users
                ):
                    self.evidence.record(
                        "gateway", shard=0, connection_epoch=self.connection_epoch, payload=payload
                    )
            elif event in {"READY", "RESUMED"}:
                # READY also contains unrelated guild/account state: retain only connection facts.
                self.evidence.record("health", event=event, sequence=payload.get("s"), shard=0)
            elif event == "INTERACTION_CREATE":
                data = payload["d"]
                user = data.get("member", {}).get("user", data.get("user", {}))
                if (
                    data.get("data", {}).get("id") == self.command_id
                    and data.get("guild_id") == self.probe_guild
                    and data.get("channel_id") == self.channel_id
                    and user.get("id") in self.authorized_users
                ):
                    self.evidence.record(
                        "interactions",
                        sequence=payload.get("s"),
                        connection_epoch=self.connection_epoch,
                        payload=data,
                    )
                    if self.waiting and user.get("id") == self.probe_actor and not self.probes.full():
                        self.probes.put_nowait(data)
        except Exception:
            self.capture_failed.set()
            self.health_errors += 1
            raise

    async def on_disconnect(self):
        self.health_errors += 1
        self.evidence.record("health", event="disconnected", shard=0)

    async def on_ready(self):
        self.evidence.record("health", event="client_ready", shard=0)

    async def on_interaction(self, interaction):
        if (
            interaction.data
            and interaction.data.get("id") == self.command_id
            and str(interaction.guild_id) == self.probe_guild
            and str(interaction.channel_id) == self.channel_id
            and str(interaction.user.id) in self.authorized_users
        ):
            await interaction.response.send_message(
                "Probe recorded. Follow the recorder terminal.", ephemeral=True
            )

    async def probe(self, stage, *, actor_id=None):
        self.probe_actor = str(actor_id or self.identity.user)
        if self.probe_actor not in self.authorized_users:
            raise RuntimeError("Probe actor is not an explicitly authorized user.")
        self.evidence.mark(stage)
        while not self.probes.empty():
            self.probes.get_nowait()
        self.waiting = True
        timeout = self.probe_config.probe_timeout
        print(
            f"User {self.probe_actor}: in server {self.probe_guild_label}, reload Discord, then in #{self.probe_config.channel} invoke /{COMMAND}. Waiting up to {timeout / 60:g} minutes.",
            flush=True,
        )
        try:
            payload = await asyncio.wait_for(self.probes.get(), timeout=timeout)
            if self.capture_failed.is_set():
                raise RuntimeError("Raw capture failed; session cannot be marked complete.")
            self.last_probe_payload = payload
            self.evidence.record(
                "actions", event="probe_received", interaction_id=payload["id"], actor_id=self.probe_actor
            )
            return payload
        finally:
            self.waiting = False


class LegacyCapture:
    """Own one schema-1 session's durable writes and Gateway observations."""

    def __init__(self, config, evidence, rest):
        self.identity = config.identity
        self.probe_config = config.probe
        self.policy = config.policy
        self.evidence = evidence
        self.rest = rest
        self.client = Recorder(config.probe, evidence)
        self.gateway = None
        self.app = {}
        self.base = None
        self.commands_path = None
        self.cleaning = False
        self.state = {
            "schema_version": 1,
            "guild_id": str(config.identity.guild),
            "user_id": str(config.identity.user),
            "discord_py": discord.__version__,
            "recorder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "recorder_sources_sha256": {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in (
                    "capture_monetization_reference.py",
                    "monetization_reference_config.py",
                    "monetization_reference_journal.py",
                    "monetization_reference_transport.py",
                )
            },
            "scope": "user subscription sandbox"
            if config.mode == "subscriptions"
            else "manual application-test-mode purchases",
            "mode": config.mode,
            "application_test_mode_confirmed_by_operator": config.mode == "purchases",
            "purchase_entitlement_ids": [],
            "limitations": [
                "No guild subscription, natural expiry, future start, or >100-row calibration.",
                "No consume of historical/deleted consumables or non-sandbox DELETE rejection probes.",
                "No-event conclusions apply only to connected observation windows.",
                "IDs and user/member metadata are retained; review before sharing.",
            ],
        }
        self.state.update(
            status="incomplete",
            created_entitlement_ids=[],
            deleted_entitlement_ids=[],
            owned_entitlements={},
            temporary_commands={},
            cleanup_errors=[],
            pending_write=None,
            baseline_ids=[],
        )
        self.save()

    def save(self):
        self.evidence.manifest(**self.state)

    async def request(self, method, path, *, query=None, body=None):
        if method == "DELETE":
            if path.startswith(self.base + "/"):
                require_entitlement_ownership(
                    self.evidence.directory, self.state, self.app["id"], path.rsplit("/", 1)[-1]
                )
            else:
                require_command_ownership(
                    self.evidence.directory,
                    self.app["id"],
                    str(self.identity.guild),
                    self.state["temporary_commands"][str(self.identity.guild)],
                )
        if method != "GET":
            begin_write(
                self.evidence,
                self.state,
                {
                    "case": self.evidence.action,
                    "method": method,
                    "path": path,
                    "body": body,
                    "query": query,
                    "request_start_line": self.evidence.counts.get("requests", 0) + 1,
                },
                cleaning=self.cleaning,
            )
        status, data = await self.rest.request(method, path, query=query, body=body)
        if method != "GET":
            receipt = f"rest.jsonl:{self.evidence.counts['rest']}"
            if method == "POST" and path == self.base and 200 <= status < 300:
                validate_test_create(data, {"id": body["sku_id"]}, self.app["id"], self.identity.user)
                eid = data["id"]
                if eid in self.state["baseline_ids"] or eid in self.state["created_entitlement_ids"]:
                    raise RuntimeError("Create returned a preexisting ID; no cleanup ownership inferred.")
                self.state["owned_entitlements"][eid] = {
                    "sku_id": body["sku_id"],
                    "owner_id": str(self.identity.user),
                    "owner_type": 2,
                    "deleted": False,
                    "create_ref": receipt,
                }
                self.state["created_entitlement_ids"].append(eid)
            elif method == "POST" and path == self.commands_path and 200 <= status < 300:
                command = {
                    "id": data.get("id") if isinstance(data, dict) else None,
                    "create_ref": receipt,
                    "deleted": False,
                }
                require_command_ownership(
                    self.evidence.directory, self.app["id"], str(self.identity.guild), command
                )
                self.state["temporary_commands"][str(self.identity.guild)] = command
                self.state["temporary_command_id"] = command["id"]
                self.client.command_id = command["id"]
            elif (
                method == "DELETE"
                and (
                    status == 204 or (status == 404 and isinstance(data, dict) and data.get("code") == 10029)
                )
                and path.startswith(self.base + "/")
            ):
                eid = path.rsplit("/", 1)[-1]
                self.state["owned_entitlements"][eid]["deleted"] = True
                if eid not in self.state["deleted_entitlement_ids"]:
                    self.state["deleted_entitlement_ids"].append(eid)
            self.state["pending_write"] = None
            self.save()
        return status, data

    async def require(self, method, path, **kwargs):
        status, data = await self.request(method, path, **kwargs)
        if not 200 <= status < 300:
            raise RuntimeError(f"{method} {path}: HTTP {status}; inspect rest.jsonl.")
        return data

    async def create(self, sku, label, required=False):
        self.evidence.mark(label)
        status, data = await self.request(
            "POST",
            self.base,
            body={
                "sku_id": sku["id"],
                "owner_id": str(self.identity.user),
                "owner_type": 2,
            },
        )
        if 200 <= status < 300:
            return data["id"]
        if required:
            raise RuntimeError(f"Test create returned HTTP {status}; inspect rest.jsonl.")
        return None

    async def observe(self, label):
        self.evidence.mark(label)
        self.evidence.record("health", event="observation_start", connected=self.client.is_ready())
        await asyncio.sleep(self.policy.observe)
        if self.gateway.done():
            await self.gateway
            raise RuntimeError("Gateway closed during observation.")
        if self.client.capture_failed.is_set():
            raise RuntimeError("Raw capture failed.")
        self.evidence.record("health", event="observation_end", connected=self.client.is_ready())

    async def inspect(self, entitlement, sku, label):
        self.evidence.mark(label)
        await self.request("GET", f"{self.base}/{entitlement}")
        query = {"user_id": str(self.identity.user), "sku_ids": sku["id"], "limit": "100"}
        await self.request("GET", self.base, query=query)
        await self.request("GET", self.base, query={**query, "exclude_deleted": "false"})

    async def close(self):
        self.cleaning = True
        self.evidence.mark("cleanup")
        try:
            for eid, owned in self.state["owned_entitlements"].items():
                if not owned["deleted"]:
                    try:
                        await self.require("DELETE", f"{self.base}/{eid}")
                    except Exception:
                        self.state["cleanup_errors"].append(f"Unresolved owned entitlement {eid}")
            command = self.state.get("temporary_command_id")
            known = self.state["temporary_commands"].get(str(self.identity.guild))
            if command and known and not known["deleted"]:
                try:
                    await self.require("DELETE", f"{self.commands_path}/{command}")
                    self.state["temporary_commands"][str(self.identity.guild)]["deleted"] = True
                except Exception:
                    self.state["cleanup_errors"].append(f"Unresolved temporary command {command}")
        finally:
            if self.state.get("pending_write") or self.state.get("uncertain_writes"):
                self.state["status"] = "uncertain_write"
            elif self.state["cleanup_errors"]:
                self.state["status"] = "cleanup_required"
            self.save()
            await self.client.close()
            if self.gateway:
                self.gateway.cancel()
                await asyncio.gather(self.gateway, return_exceptions=True)
            print(
                f"Session: {self.state['status']}. Evidence: {self.evidence.directory.resolve()}", flush=True
            )


async def prepare_legacy_catalog(capture, config):
    app = await capture.require("GET", "/oauth2/applications/@me")
    capture.app = app
    purchase_baselines = {}
    baseline_ids = set()
    capture.state.update(application_id=app["id"], application_name=app["name"])
    if app.get("interactions_endpoint_url"):
        raise RuntimeError("App has an interactions endpoint; stopped without changing it.")
    capture.base = f"/applications/{app['id']}/entitlements"
    skus = await capture.require("GET", f"/applications/{app['id']}/skus")
    if config.mode == "subscriptions":
        selected = [select_user_subscription(skus, name) for name in config.sku_names]
        if selected[0]["id"] == selected[1]["id"]:
            raise RuntimeError("Choose two different subscription SKUs.")
    else:
        selected = [
            select_purchase_sku(skus, config.consumable, discord.SKUType.consumable),
            select_purchase_sku(skus, config.durable, discord.SKUType.durable),
        ]
    capture.state["skus"] = selected
    capture.save()
    print(
        f"Reference app: {app['name']} ({app['id']}). SKUs: {', '.join(sku['name'] for sku in selected)}.",
        flush=True,
    )
    for sku in selected:
        rows = await capture.require(
            "GET",
            capture.base,
            query={
                "user_id": str(config.identity.user),
                "sku_ids": sku["id"],
                "limit": "100",
                "exclude_deleted": "false",
                "exclude_ended": "false",
            },
        )
        if not isinstance(rows, list) or len(rows) >= 100:
            raise RuntimeError(
                "Baseline is unreadable or reached a full page; stopped rather than guess at history."
            )
        baseline_ids.update(row["id"] for row in rows)
        if config.mode == "purchases":
            purchase_baselines[sku["id"]] = {row["id"] for row in rows}
        elif any(row.get("deleted") is not True for row in rows):
            raise RuntimeError(
                "Existing nondeleted grant found for a selected SKU/user; stopped without changing it."
            )
    if config.mode == "purchases":
        capture.state["preexisting_purchase_ids"] = {
            sku_id: sorted(ids) for sku_id, ids in purchase_baselines.items()
        }
        capture.save()
    capture.state["baseline_ids"] = sorted(baseline_ids)
    capture.save()
    return selected, purchase_baselines


async def connect_legacy(capture):
    await capture.client.login(capture.evidence.secret)
    capture.gateway = asyncio.create_task(capture.client.connect(reconnect=True))
    await await_gateway_ready(capture.client, capture.gateway)
    guild = capture.client.get_guild(capture.identity.guild)
    if guild is None:
        raise RuntimeError("Bot is not installed in the specified server.")
    channels = [channel for channel in guild.text_channels if channel.name == capture.probe_config.channel]
    if len(channels) != 1:
        raise RuntimeError("Need exactly one text channel with the selected name.")
    capture.state["channel_id"] = str(channels[0].id)
    capture.client.channel_id = capture.state["channel_id"]
    member = await capture.require("GET", f"/guilds/{capture.identity.guild}/members/{capture.identity.user}")
    if (
        not isinstance(member, dict)
        or member.get("user", {}).get("id") != str(capture.identity.user)
        or member["user"].get("bot")
    ):
        raise RuntimeError("Consenting probe user must be a human member of the authorized server.")
    capture.commands_path = f"/applications/{capture.app['id']}/guilds/{capture.identity.guild}/commands"
    commands = await capture.require("GET", capture.commands_path)
    if any(command["name"] == COMMAND for command in commands):
        raise RuntimeError(f"Command /{COMMAND} already exists; stopped rather than overwrite it.")
    command = await capture.require(
        "POST",
        capture.commands_path,
        body={
            "name": COMMAND,
            "description": "Record an authorized SimCord entitlement parity observation",
            "type": 1,
        },
    )
    command_id = command["id"]
    capture.client.command_id = command_id
    capture.state["temporary_command_id"] = command_id
    capture.save()


async def capture_purchases(capture, selected, purchase_baselines):
    print(f"Store: https://discord.com/application-directory/{capture.app['id']}/store", flush=True)
    print(
        "No purchases yet. Confirm the orange Application Test Mode banner names this app after every reload.",
        flush=True,
    )
    await capture.client.probe("P01 baseline interaction (no purchases yet)")
    for number, sku in enumerate(selected, start=2):
        prefix = f"P{number:02}"
        print(
            f"MANUAL ACTION: Verify the orange test-mode banner, buy exactly ONE {sku['name']} in the Store, then run /{COMMAND} in #{capture.probe_config.channel}. Stop if test mode is unclear or the item is unavailable. Do not send payment screenshots or buy anything else.",
            flush=True,
        )
        await capture.client.probe(f"{prefix} purchased {sku['name']}, before consume")
        capture.evidence.mark(f"{prefix} identify new test purchase")
        rows = await capture.require(
            "GET",
            capture.base,
            query={"user_id": str(capture.identity.user), "sku_ids": sku["id"], "limit": "100"},
        )
        row = select_new_test_purchase(
            rows, purchase_baselines[sku["id"]], sku, capture.app["id"], capture.identity.user
        )
        entitlement = row["id"]
        capture.state["purchase_entitlement_ids"].append(entitlement)
        capture.save()
        fetched = await capture.require("GET", f"{capture.base}/{entitlement}")
        checked = select_new_test_purchase(
            [fetched], purchase_baselines[sku["id"]], sku, capture.app["id"], capture.identity.user
        )
        if checked["id"] != entitlement:
            raise RuntimeError("GET returned a different purchase ID; stopped without consuming.")
        await capture.observe(f"{prefix} observe purchase")
        await capture.inspect(entitlement, sku, f"{prefix} before consume GET and list")
        attempts = 2 if sku["type"] == discord.SKUType.consumable.value else 1
        for attempt in range(1, attempts + 1):
            label = f"{prefix}.{attempt} consume request"
            capture.evidence.mark(label)
            await capture.request("POST", f"{capture.base}/{entitlement}/consume")
            await capture.observe(f"{label} observe")
            await capture.inspect(entitlement, sku, f"{label} GET and list")
            await capture.client.probe(f"{label} interaction")
    print(
        "Test-mode purchase rows are retained, not deleted by cleanup. Their IDs are in manifest.json.",
        flush=True,
    )


async def capture_subscriptions(capture, selected):
    await capture.client.probe("S01 baseline interaction")
    first = await capture.create(selected[0], "S02 create A", required=True)
    await capture.observe("S03 observe A create")
    await capture.inspect(first, selected[0], "S04 active GET and list")
    await capture.client.probe("S05 active interaction")
    await capture.create(selected[0], "S06 duplicate A")
    await capture.observe("S07 observe duplicate")
    await capture.create(selected[1], "S08 same owner different SKU B")
    await capture.observe("S09 observe B create")
    capture.evidence.mark("S10 delete all session-created active grants")
    for entitlement in sorted(capture.state["created_entitlement_ids"]):
        await capture.require("DELETE", f"{capture.base}/{entitlement}")
        capture.save()
    await capture.observe("S11 observe deletion")
    await capture.inspect(first, selected[0], "S12 deleted GET and both list modes")
    capture.evidence.mark("S13 repeat DELETE A")
    await capture.request("DELETE", f"{capture.base}/{first}")
    await capture.observe("S14 observe repeat DELETE")
    await capture.client.probe("S15 deleted interaction")
    await capture.create(selected[0], "S16 recreate A after deletion")
    await capture.observe("S17 observe recreated grant")


async def session(config: SubscriptionCaptureConfig | PurchaseCaptureConfig, token, evidence):
    require_capture_transport()
    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bot {token}", "User-Agent": "SimCord authorized parity recorder"},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as http:
        capture = LegacyCapture(config, evidence, Rest(http, evidence))
        try:
            selected, baselines = await prepare_legacy_catalog(capture, config)
            await connect_legacy(capture)
            if config.mode == "purchases":
                await capture_purchases(capture, selected, baselines)
            else:
                await capture_subscriptions(capture, selected)
            capture.state["status"] = "completed"
        finally:
            await capture.close()


async def self_test():
    """Runnable boundary check: secret removal, null/omission, and HTTP rate limiting."""
    from aiohttp import web
    from discord.gateway import DiscordWebSocket

    catalog = [
        {"id": "1", "name": "A", "type": 5, "flags": 256},
        {"id": "2", "name": "B", "type": 5, "flags": 260},
        {"id": "3", "name": "A", "type": 6, "flags": 256},
        {"id": "4", "name": "A", "type": 5, "flags": 128},
    ]
    assert select_user_subscription(catalog, "A")["id"] == "1"
    assert select_user_subscription(catalog, "B")["id"] == "2"
    for invalid in ([catalog[3]], [catalog[0], dict(catalog[0], id="5")], [catalog[2]]):
        try:
            select_user_subscription(invalid, "A")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Guild-only, duplicate or group SKUs must not be selected.")

    purchase_sku = {"id": "10", "name": "Consumable", "type": 3}
    durable_sku = {"id": "11", "name": "Durable", "type": 2}
    assert (
        select_purchase_sku([durable_sku, purchase_sku], "Consumable", discord.SKUType.consumable)["id"]
        == "10"
    )
    purchase = {
        "id": "new",
        "type": 4,
        "application_id": "app",
        "sku_id": "10",
        "user_id": "2",
        "deleted": False,
        "consumed": False,
    }
    baseline = {"old"}
    assert (
        select_new_test_purchase([dict(purchase, id="old"), purchase], baseline, purchase_sku, "app", 2)["id"]
        == "new"
    )
    durable_purchase = {key: value for key, value in purchase.items() if key != "consumed"}
    durable_purchase["sku_id"] = durable_sku["id"]
    assert (
        select_new_test_purchase(
            [dict(durable_purchase, id="old"), durable_purchase], baseline, durable_sku, "app", 2
        )["id"]
        == "new"
    )
    invalid_purchases = [
        [],
        [dict(purchase, id="old")],
        [purchase, dict(purchase, id="other")],
        [purchase] * 100,
        *[
            [dict(purchase, **change)]
            for change in (
                {"type": 1},
                {"type": 8},
                {"application_id": "other"},
                {"sku_id": "11"},
                {"user_id": "other"},
                {"guild_id": "guild"},
                {"deleted": True},
                {"consumed": True},
                {"consumed": None},
                {"id": None},
            )
        ],
    ]
    invalid_purchases.append([{key: value for key, value in purchase.items() if key != "consumed"}])
    for rows in invalid_purchases:
        try:
            select_new_test_purchase(rows, baseline, purchase_sku, "app", 2)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Unsafe or ambiguous purchase selection must stop before consumption.")

    grant = {"id": "123", "type": 8, "application_id": "app", "sku_id": "10", "user_id": "2"}
    validate_test_create(grant, purchase_sku, "app", 2)
    for change in (
        {"id": None},
        {"type": 4},
        {"application_id": "other"},
        {"sku_id": "11"},
        {"user_id": "other"},
        {"guild_id": "guild"},
    ):
        try:
            validate_test_create({**grant, **change}, purchase_sku, "app", 2)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Unverified create receipts must not authorize cleanup.")

    requests = []

    async def endpoint(request):
        requests.append(request.query_string)
        if len(requests) == 1:
            return web.json_response({"retry_after": 0.01}, status=429)
        return web.json_response(
            {"ends_at": None, "token": "interaction-secret", "nested": {"authorization": "secret"}}
        )

    async def gateway_endpoint(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        for sequence, (user, channel, interaction) in enumerate(
            (("99", "4", "6"), ("2", "99", "7"), ("2", "4", "5")), 1
        ):
            await socket.send_json(
                {
                    "op": 0,
                    "t": "INTERACTION_CREATE",
                    "s": sequence,
                    "d": {
                        "id": interaction,
                        "guild_id": "1",
                        "channel_id": channel,
                        "data": {"id": "3"},
                        "member": {"user": {"id": user}},
                        "token": "interaction-secret",
                    },
                }
            )
        await socket.close()
        return socket

    app = web.Application()
    app.router.add_get("/entitlements", endpoint)
    app.router.add_get("/gateway", gateway_endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    try:
        port = runner.addresses[0][1]
        with tempfile.TemporaryDirectory() as root:
            evidence = Evidence(Path(root) / "capture", "bot-secret")
            evidence.record(
                "gateway",
                token="interaction-secret",
                payload={
                    "session_id": "session-secret",
                    "text": "bot-secret",
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "client_secret": "client-secret",
                },
            )
            async with aiohttp.ClientSession() as http:
                status, data = await Rest(http, evidence, f"http://127.0.0.1:{port}").request(
                    "GET",
                    "/entitlements",
                    query={"exclude_deleted": "false"},
                )
            assert status == 200 and data["ends_at"] is None and "starts_at" not in data
            rows = [json.loads(line) for line in (evidence.directory / "rest.jsonl").read_text().splitlines()]
            assert [row["status"] for row in rows] == [429, 200]
            assert requests == ["exclude_deleted=false", "exclude_deleted=false"]
            assert rows[1]["response"]["ends_at"] is None and "starts_at" not in rows[1]["response"]
            assert rows[1]["response"]["token"] == REDACTED
            assert rows[1]["response"]["nested"]["authorization"] == REDACTED
            client = Recorder(
                CaptureConfig.from_namespace(argparse.Namespace(guild=1, user=2, channel="parity")).probe,
                evidence,
            )
            client.command_id, client.channel_id = "3", "4"
            async with client, aiohttp.ClientSession() as http:
                async with http.ws_connect(f"http://127.0.0.1:{port}/gateway") as socket:
                    gateway = DiscordWebSocket(socket, loop=asyncio.get_running_loop())
                    gateway._dispatch = client.dispatch
                    gateway.shard_id = 0
                    # Exercise SDK frame decoding and event dispatch, not SDK model parsing.
                    gateway._discord_parsers = {}
                    if client._enable_debug_events:
                        gateway.log_receive = gateway.debug_log_receive
                    probe = asyncio.create_task(client.probe("self-test baseline"))
                    try:
                        await asyncio.sleep(0)
                        for _ in range(3):
                            message = await socket.receive(timeout=5)
                            assert message.type == aiohttp.WSMsgType.TEXT
                            await gateway.received_message(message.data)
                        await asyncio.wait_for(probe, timeout=5)
                    finally:
                        probe.cancel()
                        await asyncio.gather(probe, return_exceptions=True)
                captured = json.loads((evidence.directory / "interactions.jsonl").read_text())
                assert captured["payload"]["id"] == "5"
                assert captured["payload"]["token"] == REDACTED
                actions = [
                    json.loads(line)
                    for line in (evidence.directory / "actions.jsonl").read_text().splitlines()
                ]
                assert actions[-1]["event"] == "probe_received" and actions[-1]["interaction_id"] == "5"
            text = "".join(path.read_text() for path in evidence.directory.iterdir())
            assert not any(
                secret in text
                for secret in (
                    "bot-secret",
                    "interaction-secret",
                    "session-secret",
                    "access-secret",
                    "refresh-secret",
                    "client-secret",
                )
            )
            evidence.manifest(status="incomplete", created_entitlement_ids=["123"])
            assert json.loads((evidence.directory / "manifest.json").read_text())[
                "created_entitlement_ids"
            ] == ["123"]
        print(
            "PASS: local HTTP retry, false query, null/omission, secret redaction, SDK WebSocket probe advancement and authorization filtering, recovery journal."
        )
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guild", type=int, help="Authorized test server ID")
    parser.add_argument("--user", type=int, help="Consenting test user ID")
    parser.add_argument("--mode", choices=("subscriptions", "purchases"), default="subscriptions")
    parser.add_argument(
        "--test-mode-confirmed",
        action="store_true",
        help="Attest that the orange Application Test Mode banner names this app; required for purchases",
    )
    parser.add_argument("--consumable", default="Parity Consumable")
    parser.add_argument("--durable", default="Parity Durable")
    parser.add_argument("--channel", default="parity")
    parser.add_argument("--sku-a", default="Parity Subscription A")
    parser.add_argument("--sku-b", default="Parity Subscription B")
    parser.add_argument(
        "--observe", type=int, default=60, help="Seconds to observe events after each mutation (default 60)"
    )
    parser.add_argument(
        "--output", type=Path, help="New directory; defaults to an ignored local capture directory"
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Run local boundary checks without credentials or Discord"
    )
    args = parser.parse_args()
    os.umask(0o077)
    try:
        require_capture_transport()
    except RuntimeError as error:
        parser.error(str(error))
    if args.self_test:
        asyncio.run(self_test())
        return
    if not args.guild or not args.user or args.guild <= 0 or args.user <= 0 or args.observe < 1:
        parser.error("Supply positive --guild and --user IDs, and --observe >= 1.")
    if args.mode == "purchases" and not args.test_mode_confirmed:
        parser.error(
            "Enable Application Test Mode, verify its orange banner, then supply --test-mode-confirmed. No live purchases."
        )
    identity = CaptureIdentity(args.user, args.guild)
    probe = ProbeConfig(identity, args.channel)
    policy = RequestPolicy(observe=args.observe)
    config = (
        SubscriptionCaptureConfig(identity, probe, policy, (args.sku_a, args.sku_b))
        if args.mode == "subscriptions"
        else PurchaseCaptureConfig(identity, probe, policy, args.consumable, args.durable)
    )
    if args.output and args.output.exists():
        parser.error("Output already exists; preserve the original capture.")
    if not sys.stdin.isatty():
        parser.error("Run in your own interactive terminal for the hidden token prompt.")
    token = getpass.getpass("KaruMart bot token (hidden; never saved): ").strip()
    if not token:
        parser.error("Token was empty.")
    directory = args.output or Path(".discord-reference-captures") / (
        "monetization-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    evidence = Evidence(directory, token)
    try:
        asyncio.run(session(config, token, evidence))
    except KeyboardInterrupt:
        print("Interrupted. Check manifest.json for cleanup status before restarting.")
        raise SystemExit(130) from None
    except Exception as error:
        # Never print SDK/transport exceptions: they can contain credential-bearing URLs.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"Stopped: {redact(message, token)}. Evidence: {directory.resolve()}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
