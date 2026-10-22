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
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
import discord

COMMAND = "simcord_parity_probe"
REDACTED = "[REDACTED]"


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
        or type(row.get("type")) is not int
        or row["type"] != discord.EntitlementType.application_subscription.value
        or row.get("application_id") != application_id
        or row.get("sku_id") != sku["id"]
        or row.get("user_id") != str(user_id)
        or row.get("guild_id") is not None
    ):
        raise RuntimeError(
            "Create receipt did not verify the requested user test grant; no cleanup ownership inferred. Inspect evidence before retrying."
        )


def redact(value, secret=""):
    if isinstance(value, dict):
        return {
            key: REDACTED
            if key.lower()
            in {"token", "authorization", "session_id", "access_token", "refresh_token", "client_secret"}
            else redact(item, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, REDACTED)
    return value


class Evidence:
    def __init__(self, directory, secret, *, resume=False):
        directory.mkdir(mode=0o700, parents=True, exist_ok=resume)
        self.directory = directory
        self.secret = secret
        self.action = "setup"
        self.counts = {}
        for stream in ("requests", "rest", "gateway", "interactions", "health", "actions"):
            path = directory / f"{stream}.jsonl"
            if path.exists():
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                for line in lines:
                    if not line.endswith("\n"):
                        raise RuntimeError(
                            f"Truncated evidence in {stream}.jsonl; preserve it before recovery."
                        )
                    json.loads(line)
                self.counts[stream] = len(lines)
        self.revision = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def record(self, stream, **data):
        row = {
            "utc": datetime.now(UTC).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "action": self.action,
            **data,
        }
        with (self.directory / f"{stream}.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(redact(row, self.secret), ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        self.counts[stream] = self.counts.get(stream, 0) + 1
        return f"{stream}.jsonl:{self.counts[stream]}"

    def mark(self, action):
        self.action = action
        self.record("actions", event="begin")
        print(action, flush=True)

    def manifest(self, **data):
        target = self.directory / "manifest.json"
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(json.dumps(redact(data, self.secret), indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Rest:
    def __init__(self, session, evidence, base="https://discord.com/api/v10"):
        self.session = session
        self.evidence = evidence
        self.base = base

    async def request(self, method, path, *, query=None, body=None) -> tuple[int, Any]:
        for attempt in range(6):
            started = datetime.now(UTC).isoformat()
            request_id = f"requests.jsonl:{self.evidence.counts.get('requests', 0) + 1}"
            self.evidence.record(
                "requests",
                request_id=request_id,
                method=method,
                path=path,
                query=query,
                body=body,
                attempt=attempt + 1,
            )
            # Never replay a write on a transport error: its outcome may be unknown.
            try:
                async with self.session.request(
                    method, self.base + path, params=query, json=body, allow_redirects=False
                ) as response:
                    text = await response.text()
                    try:
                        data = json.loads(text) if text else None
                    except ValueError:
                        data = text
                    self.evidence.record(
                        "rest",
                        request_id=request_id,
                        started_utc=started,
                        method=method,
                        path_query=response.url.raw_path_qs,
                        body=body,
                        status=response.status,
                        response=data,
                        attempt=attempt + 1,
                        rate_limit={
                            key: value
                            for key, value in response.headers.items()
                            if key.lower().startswith("x-ratelimit") or key.lower() in {"retry-after", "date"}
                        },
                    )
                    if response.status != 429:
                        return response.status, data
                    if not isinstance(data, dict) or not isinstance(data.get("retry_after"), (int, float)):
                        raise RuntimeError("Rate limit response lacked retry_after; stopped.")
            except (aiohttp.ClientError, TimeoutError, asyncio.CancelledError) as error:
                self.evidence.record(
                    "rest",
                    request_id=request_id,
                    started_utc=started,
                    method=method,
                    path_query=path,
                    body=body,
                    status=None,
                    response=None,
                    error=type(error).__name__,
                    outcome_unknown=method != "GET",
                )
                raise
            await asyncio.sleep(max(0.1, data["retry_after"]))
        raise RuntimeError("Repeated rate limits; stopped. See rest.jsonl.")

    async def require(self, method, path, **kwargs):
        status, data = await self.request(method, path, **kwargs)
        if not 200 <= status < 300:
            raise RuntimeError(f"{method} {path}: HTTP {status}; see rest.jsonl. Nothing was published.")
        return data


class Recorder(discord.Client):
    def __init__(self, args, evidence):
        super().__init__(intents=discord.Intents(guilds=True), enable_debug_events=True)
        self.args = args
        self.evidence = evidence
        self.command_id: str | None = None
        self.channel_id: str | None = None
        self.waiting = False
        self.probes = asyncio.Queue(maxsize=1)
        self.capture_failed = asyncio.Event()
        self.authorized_users = {str(args.user)}
        if getattr(args, "other_user", None):
            self.authorized_users.add(str(args.other_user))
        self.probe_actor = str(args.user)
        self.probe_guild = str(args.guild)
        self.probe_guild_label = str(args.guild)
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
        self.probe_actor = str(actor_id or self.args.user)
        if self.probe_actor not in self.authorized_users:
            raise RuntimeError("Probe actor is not an explicitly authorized user.")
        self.evidence.mark(stage)
        while not self.probes.empty():
            self.probes.get_nowait()
        self.waiting = True
        timeout = getattr(self.args, "probe_timeout", 900)
        print(
            f"User {self.probe_actor}: in server {self.probe_guild_label}, reload Discord, then in #{self.args.channel} invoke /{COMMAND}. Waiting up to {timeout / 60:g} minutes.",
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


async def session(args, token, evidence):
    client = Recorder(args, evidence)
    created = set()
    deleted = set()
    command_id = None
    gateway = None
    base = None
    cleanup_errors = []
    status = "incomplete"
    purchase_baselines = {}
    metadata = {
        "schema_version": 1,
        "guild_id": str(args.guild),
        "user_id": str(args.user),
        "discord_py": discord.__version__,
        "recorder_sha256": evidence.revision,
        "scope": "user subscription sandbox"
        if args.mode == "subscriptions"
        else "manual application-test-mode purchases",
        "mode": args.mode,
        "application_test_mode_confirmed_by_operator": args.test_mode_confirmed,
        "purchase_entitlement_ids": [],
        "limitations": [
            "No guild subscription, natural expiry, future start, or >100-row calibration.",
            "No consume of historical/deleted consumables or non-sandbox DELETE rejection probes.",
            "No-event conclusions apply only to connected observation windows.",
            "IDs and user/member metadata are retained; review before sharing.",
        ],
    }

    def save():
        evidence.manifest(
            **metadata,
            status=status,
            created_entitlement_ids=sorted(created),
            deleted_entitlement_ids=sorted(deleted),
            cleanup_errors=cleanup_errors,
        )

    save()
    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bot {token}", "User-Agent": "SimCord authorized parity recorder"},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as http:
        rest = Rest(http, evidence)
        try:
            app = await rest.require("GET", "/oauth2/applications/@me")
            metadata.update(application_id=app["id"], application_name=app["name"])
            if app.get("interactions_endpoint_url"):
                raise RuntimeError("App has an interactions endpoint; stopped without changing it.")
            base = f"/applications/{app['id']}/entitlements"
            skus = await rest.require("GET", f"/applications/{app['id']}/skus")
            if args.mode == "subscriptions":
                selected = [select_user_subscription(skus, name) for name in (args.sku_a, args.sku_b)]
                if selected[0]["id"] == selected[1]["id"]:
                    raise RuntimeError("Choose two different subscription SKUs.")
            else:
                selected = [
                    select_purchase_sku(skus, args.consumable, discord.SKUType.consumable),
                    select_purchase_sku(skus, args.durable, discord.SKUType.durable),
                ]
            metadata["skus"] = selected
            save()
            print(
                f"Reference app: {app['name']} ({app['id']}). SKUs: {', '.join(sku['name'] for sku in selected)}.",
                flush=True,
            )
            for sku in selected:
                rows = await rest.require(
                    "GET", base, query={"user_id": str(args.user), "sku_ids": sku["id"], "limit": "100"}
                )
                if args.mode == "purchases":
                    if len(rows) >= 100:
                        raise RuntimeError(
                            "Purchase baseline reached a full page; stopped rather than guess at history."
                        )
                    purchase_baselines[sku["id"]] = {row["id"] for row in rows}
                elif rows:
                    raise RuntimeError(
                        "Existing nondeleted grant found for a selected SKU/user; stopped without changing it."
                    )
            if args.mode == "purchases":
                metadata["preexisting_purchase_ids"] = {
                    sku_id: sorted(ids) for sku_id, ids in purchase_baselines.items()
                }
                save()
            await client.login(token)
            gateway = asyncio.create_task(client.connect(reconnect=True))
            ready = asyncio.create_task(client.wait_until_ready())
            try:
                done, _ = await asyncio.wait(
                    {ready, gateway}, timeout=60, return_when=asyncio.FIRST_COMPLETED
                )
                if gateway in done:
                    await gateway
                    raise RuntimeError("Gateway closed before READY.")
                if ready not in done:
                    raise RuntimeError("Gateway READY timed out.")
                await ready
            finally:
                ready.cancel()
            guild = client.get_guild(args.guild)
            if guild is None:
                raise RuntimeError("Bot is not installed in the specified server.")
            channels = [channel for channel in guild.text_channels if channel.name == args.channel]
            if len(channels) != 1:
                raise RuntimeError("Need exactly one text channel with the selected name.")
            metadata["channel_id"] = str(channels[0].id)
            client.channel_id = metadata["channel_id"]
            commands_path = f"/applications/{app['id']}/guilds/{args.guild}/commands"
            commands = await rest.require("GET", commands_path)
            if any(command["name"] == COMMAND for command in commands):
                raise RuntimeError(f"Command /{COMMAND} already exists; stopped rather than overwrite it.")
            command = await rest.require(
                "POST",
                commands_path,
                body={
                    "name": COMMAND,
                    "description": "Record an authorized SimCord entitlement parity observation",
                    "type": 1,
                },
            )
            command_id = command["id"]
            client.command_id = command_id
            metadata["temporary_command_id"] = command_id
            save()

            async def create(sku, label, required=False):
                evidence.mark(label)
                response_status, data = await rest.request(
                    "POST",
                    base,
                    body={
                        "sku_id": sku["id"],
                        "owner_id": str(args.user),
                        "owner_type": 2,
                    },
                )
                if 200 <= response_status < 300:
                    validate_test_create(data, sku, app["id"], args.user)
                    created.add(data["id"])
                    save()  # Durable journal before any later await or mutation.
                    return data["id"]
                if required:
                    raise RuntimeError(
                        f"Test create returned HTTP {response_status}; inspect rest.jsonl. Leave SKUs unpublished."
                    )
                return None

            async def observe(label):
                evidence.mark(label)
                evidence.record("health", event="observation_start", connected=client.is_ready())
                await asyncio.sleep(args.observe)
                if gateway.done():
                    await gateway
                    raise RuntimeError("Gateway closed during observation.")
                if client.capture_failed.is_set():
                    raise RuntimeError("Raw capture failed.")
                evidence.record("health", event="observation_end", connected=client.is_ready())

            async def inspect(entitlement, sku, label):
                evidence.mark(label)
                await rest.request("GET", f"{base}/{entitlement}")
                query = {"user_id": str(args.user), "sku_ids": sku["id"], "limit": "100"}
                await rest.request("GET", base, query=query)
                await rest.request("GET", base, query={**query, "exclude_deleted": "false"})

            if args.mode == "purchases":
                print(f"Store: https://discord.com/application-directory/{app['id']}/store", flush=True)
                print(
                    "No purchases yet. Confirm the orange Application Test Mode banner names this app after every reload.",
                    flush=True,
                )
                await client.probe("P01 baseline interaction (no purchases yet)")
                for number, sku in enumerate(selected, start=2):
                    prefix = f"P{number:02}"
                    print(
                        f"MANUAL ACTION: Verify the orange test-mode banner, buy exactly ONE {sku['name']} in the Store, "
                        f"then run /{COMMAND} in #{args.channel}. Stop if test mode is unclear or the item is unavailable. "
                        "Do not send payment screenshots or buy anything else.",
                        flush=True,
                    )
                    await client.probe(f"{prefix} purchased {sku['name']}, before consume")
                    evidence.mark(f"{prefix} identify new test purchase")
                    rows = await rest.require(
                        "GET",
                        base,
                        query={
                            "user_id": str(args.user),
                            "sku_ids": sku["id"],
                            "limit": "100",
                        },
                    )
                    row = select_new_test_purchase(
                        rows, purchase_baselines[sku["id"]], sku, app["id"], args.user
                    )
                    entitlement = row["id"]
                    metadata["purchase_entitlement_ids"].append(entitlement)
                    save()
                    # Re-read before the first write: never act on a live, preexisting or already-consumed row.
                    fetched = await rest.require("GET", f"{base}/{entitlement}")
                    checked = select_new_test_purchase(
                        [fetched], purchase_baselines[sku["id"]], sku, app["id"], args.user
                    )
                    if checked["id"] != entitlement:
                        raise RuntimeError("GET returned a different purchase ID; stopped without consuming.")
                    await observe(f"{prefix} observe purchase")
                    await inspect(entitlement, sku, f"{prefix} before consume GET and list")
                    attempts = 2 if sku["type"] == discord.SKUType.consumable.value else 1
                    for attempt in range(1, attempts + 1):
                        label = f"{prefix}.{attempt} consume request"
                        evidence.mark(label)
                        await rest.request("POST", f"{base}/{entitlement}/consume")
                        await observe(f"{label} observe")
                        await inspect(entitlement, sku, f"{label} GET and list")
                        await client.probe(f"{label} interaction")
                print(
                    "Test-mode purchase rows are retained, not deleted by cleanup. Their IDs are in manifest.json.",
                    flush=True,
                )
            else:
                await client.probe("S01 baseline interaction")
                first = await create(selected[0], "S02 create A", required=True)
                await observe("S03 observe A create")
                await inspect(first, selected[0], "S04 active GET and list")
                await client.probe("S05 active interaction")
                await create(selected[0], "S06 duplicate A")
                await observe("S07 observe duplicate")
                await create(selected[1], "S08 same owner different SKU B")
                await observe("S09 observe B create")
                evidence.mark("S10 delete all session-created active grants")
                for entitlement in sorted(created):
                    await rest.require("DELETE", f"{base}/{entitlement}")
                    deleted.add(entitlement)
                    save()
                await observe("S11 observe deletion")
                await inspect(first, selected[0], "S12 deleted GET and both list modes")
                evidence.mark("S13 repeat DELETE A")
                await rest.request("DELETE", f"{base}/{first}")
                await observe("S14 observe repeat DELETE")
                await client.probe("S15 deleted interaction")
                await create(selected[0], "S16 recreate A after deletion")
                await observe("S17 observe recreated grant")
            status = "completed"
        finally:
            evidence.mark("cleanup")
            if base:
                for entitlement in sorted(created - deleted):
                    try:
                        await rest.require("DELETE", f"{base}/{entitlement}")
                        deleted.add(entitlement)
                    except Exception:
                        cleanup_errors.append(
                            f"Delete test entitlement {entitlement}; consult REST evidence before retrying."
                        )
            if command_id:
                try:
                    await rest.require("DELETE", f"{commands_path}/{command_id}")
                except Exception:
                    cleanup_errors.append(f"Remove temporary guild command {command_id}.")
            if cleanup_errors:
                status = "cleanup_required"
            save()
            await client.close()
            if gateway:
                gateway.cancel()
                await asyncio.gather(gateway, return_exceptions=True)
            print(f"Session: {status}. Evidence: {evidence.directory.resolve()}", flush=True)
            for error in cleanup_errors:
                print(error, flush=True)


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
            client = Recorder(argparse.Namespace(guild=1, user=2, channel="parity"), evidence)
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
    if args.self_test:
        asyncio.run(self_test())
        return
    if not args.guild or not args.user or args.guild <= 0 or args.user <= 0 or args.observe < 1:
        parser.error("Supply positive --guild and --user IDs, and --observe >= 1.")
    if args.mode == "purchases" and not args.test_mode_confirmed:
        parser.error(
            "Enable Application Test Mode, verify its orange banner, then supply --test-mode-confirmed. No live purchases."
        )
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
        asyncio.run(session(args, token, evidence))
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
