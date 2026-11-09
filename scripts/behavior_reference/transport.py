"""Positive-receipt ownership, two-secret redaction and non-replaying transport."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from scripts.monetization_reference_journal import begin_write, receipt_pair, recover_receipt
from scripts.monetization_reference_transport import Evidence, Rest, redact

LAB_GUILD = "1567657025029021897"
EMOJI_GUILD = "1476747775092719732"


class SafeEvidence(Evidence):
    def __init__(self, directory, secrets, *, resume=False):
        self.secrets = tuple(secret for secret in secrets if secret)
        super().__init__(directory, "", resume=resume)

    def safe(self, data):
        for secret in self.secrets:
            data = redact(data, secret)
        return redact(data)

    def record(self, stream, **data):
        return super().record(stream, **self.safe(data))

    def manifest(self, **data):
        return super().manifest(**self.safe(data))


class Journal:
    def __init__(self, evidence, guild, *, resume=False):
        if str(guild) != LAB_GUILD:
            raise ValueError("Refusing unauthorized guild; only the approved behavior lab is writable")
        self.evidence = evidence
        path = evidence.directory / "manifest.json"
        self.state = (
            json.loads(path.read_text())
            if resume
            else {"schema": 1, "guild": LAB_GUILD, "owned": [], "restores": [], "pending_write": None}
        )
        if self.state.get("guild") != LAB_GUILD or self.state.get("schema") != 1:
            raise ValueError("Journal is not for the approved behavior lab")
        self._delete_refs = None
        if resume and self.state.get("pending_write"):
            self.recover()
        self.save()

    def save(self):
        self.evidence.manifest(**self.state)

    def recover(self):
        intent = self.state["pending_write"]
        attempted, receipt, reference = recover_receipt(self.evidence.directory, intent)
        if not attempted or receipt is None or receipt.get("status") is None or receipt.get("status") == 429:
            self.state.setdefault("uncertain_writes", []).append(intent)
            self.state["pending_write"] = None
            self.save()
            return
        self.finish(receipt["status"], receipt.get("response"), reference)

    def start(self, method, path, body, query, *, cleanup=False):
        begin_write(
            self.evidence,
            self.state,
            {
                "method": method,
                "path": path,
                "body": body,
                "query": query,
                "case": self.evidence.action,
                "request_start_line": self.evidence.counts.get("requests", 0) + 1,
            },
            cleaning=cleanup,
        )

    def finish(self, status, data, reference):
        intent = self.state["pending_write"]
        if 200 <= status < 300 and intent["method"] == "POST" and isinstance(data, dict) and data.get("id"):
            path = intent["path"]
            kind = (
                "channel"
                if path.endswith(("/channels", "/threads"))
                else "role"
                if path.endswith("/roles")
                else "command"
                if path.endswith("/commands")
                else "message"
                if path.endswith("/messages")
                else None
            )
            if kind:
                delete = f"/channels/{data['id']}" if kind == "channel" else f"{path}/{data['id']}"
                self.state["owned"].append(
                    {
                        "id": str(data["id"]),
                        "kind": kind,
                        "path": delete,
                        "create_ref": reference,
                        "deleted": False,
                    }
                )
        if intent["method"] == "DELETE" and (200 <= status < 300 or status == 404):
            for item in self.state["owned"]:
                if item["path"] == intent["path"]:
                    item["deleted"] = True
                    item["delete_ref"] = reference
        self.state["pending_write"] = None
        self.save()

    def owns(self, id, kind=None):
        return any(
            item["id"] == str(id) and not item["deleted"] and (kind is None or item["kind"] == kind)
            for item in self.state["owned"]
        )

    def prove(self, item):
        request, receipt = receipt_pair(self.evidence.directory, item["create_ref"])
        row = receipt.get("response")
        body = request.get("body") or {}
        path = request["path"]
        expected = f"/channels/{item['id']}" if item["kind"] == "channel" else f"{path}/{item['id']}"
        if (
            request["method"] != "POST"
            or not isinstance(row, dict)
            or str(row.get("id")) != item["id"]
            or item["path"] != expected
        ):
            raise RuntimeError("Receipt does not prove resource ownership")
        if item["kind"] in {"role", "command", "channel"} and not str(body.get("name", "")).startswith(
            "lab-"
        ):
            raise RuntimeError("Receipt is not for a lab-owned resource")
        if item["kind"] == "role" and path != f"/guilds/{LAB_GUILD}/roles":
            raise RuntimeError("Role receipt is outside approved guild")
        if (
            item["kind"] == "command"
            and path != f"/applications/{self.state['application']}/guilds/{LAB_GUILD}/commands"
        ):
            raise RuntimeError("Command receipt is outside approved guild application")
        if item["kind"] == "message" or (
            item["kind"] == "channel" and path != f"/guilds/{LAB_GUILD}/channels"
        ):
            parent_id = path.split("/")[2] if path.startswith("/channels/") else ""
            parent = next(
                (
                    owned
                    for owned in self.state["owned"]
                    if owned["id"] == parent_id and owned["kind"] == "channel"
                ),
                None,
            )
            if parent is None or parent is item:
                raise RuntimeError("Parent channel lacks positive ownership")
            self.prove(parent)

    def prove_deleted(self, item):
        self.prove(item)
        if not item["deleted"]:
            raise RuntimeError("No positively deleted parent for cascade cleanup")
        if item.get("cascade_delete_ref"):
            request, _ = receipt_pair(self.evidence.directory, item["create_ref"])
            parent_id = request["path"].split("/")[2]
            parent = next(
                (
                    owned
                    for owned in self.state["owned"]
                    if owned["id"] == parent_id and owned["kind"] == "channel"
                ),
                None,
            )
            if parent is None or parent is item or self.prove_deleted(parent) != item["cascade_delete_ref"]:
                raise RuntimeError("Cascade deletion ancestry lacks positive receipts")
            return item["cascade_delete_ref"]
        reference = item.get("delete_ref")
        if not reference:
            # Older journals predate delete_ref. Recover only a correlated,
            # successful DELETE; a name match or a deleted boolean is not proof.
            if self._delete_refs is None:
                self._delete_refs = {}
                for number, line in enumerate(
                    (self.evidence.directory / "rest.jsonl").read_text().splitlines(), 1
                ):
                    row = json.loads(line)
                    if (
                        row.get("method") == "DELETE"
                        and isinstance(row.get("status"), int)
                        and 200 <= row["status"] < 300
                    ):
                        path = urlsplit(row.get("path_query", "")).path.removeprefix("/api/v10")
                        self._delete_refs[path] = f"rest.jsonl:{number}"
            reference = self._delete_refs.get(item["path"])
        request, _ = receipt_pair(self.evidence.directory, reference or "")
        if request["method"] != "DELETE" or request["path"] != item["path"]:
            raise RuntimeError("Parent deletion receipt does not prove cascade cleanup")
        return reference

    def cascade(self, item):
        request, _ = receipt_pair(self.evidence.directory, item["create_ref"])
        path = request["path"]
        if not path.startswith("/channels/"):
            return False
        parent_id = path.split("/")[2]
        parent = next(
            (
                owned
                for owned in self.state["owned"]
                if owned["id"] == parent_id and owned["kind"] == "channel"
            ),
            None,
        )
        if parent is None or not parent["deleted"]:
            return False
        item["cascade_delete_ref"] = self.prove_deleted(parent)
        item["deleted"] = True
        self.save()
        return True

    def authorize(self, method, path, body):
        if method == "GET":
            return
        parts = path.strip("/").split("/")
        if parts[:2] == ["guilds", LAB_GUILD]:
            if (
                len(parts) == 3
                and parts[2] in {"roles", "channels"}
                and method == "POST"
                and str((body or {}).get("name", "")).startswith("lab-")
            ):
                return
            if len(parts) == 4 and parts[2] == "roles" and self.owns(parts[3], "role"):
                return
            if len(parts) >= 4 and parts[2] == "members" and parts[3] in {self.state.get("subject"), "@me"}:
                if (
                    len(parts) == 6
                    and parts[4] == "roles"
                    and self.owns(parts[5], "role")
                    and method in {"PUT", "DELETE"}
                ):
                    return
                if (
                    len(parts) == 4
                    and method == "PATCH"
                    and set(body or {}) <= {"nick", "communication_disabled_until"}
                ):
                    return
            if parts[2:] == ["members", "@me", "nick"] and method == "PATCH" and set(body or {}) == {"nick"}:
                return
        if len(parts) >= 2 and parts[0] == "channels" and self.owns(parts[1], "channel"):
            if len(parts) >= 4 and parts[2] == "permissions" and parts[3] != self.state.get("subject"):
                raise RuntimeError("Only the lab subject's member overwrite may be edited")
            return
        prefix = ["applications", self.state.get("application"), "guilds", LAB_GUILD, "commands"]
        if parts[:5] == prefix:
            if (
                len(parts) == 5
                and method == "POST"
                and (body or {}).get("name") == self.state.get("command_name")
            ):
                return
            if len(parts) == 6 and self.owns(parts[5], "command") and method in {"PATCH", "DELETE"}:
                return
        raise RuntimeError("Refusing mutation outside positively owned lab resources")


class Live:
    def __init__(self, sessions, evidence, journal, *, pace=0.12):
        self.rest = {actor: Rest(session, evidence) for actor, session in sessions.items()}
        self.evidence, self.journal, self.pace = evidence, journal, pace
        self.last = 0.0

    async def request(self, actor, method, path, *, body=None, query=None, file=False, cleanup=False):
        self.journal.authorize(method, path, body)
        await asyncio.sleep(max(0, self.last + self.pace - time.monotonic()))
        self.last = time.monotonic()
        if method != "GET":
            self.journal.start(method, path, body, query, cleanup=cleanup)
        if file:
            # Rest's session wrapper sends the same payload_json + file semantics as offline.
            session = self.rest[actor].session
            session.upload = True
        try:
            status, data = await self.rest[actor].request(method, path, body=body, query=query)
        finally:
            if file:
                session.upload = False
        if method != "GET":
            self.journal.finish(status, data, f"rest.jsonl:{self.evidence.counts['rest']}")
        return status, data


class MultipartSession:
    """Public aiohttp session adapter; keep the existing paced/429/no-retry Rest."""

    def __init__(self, session):
        self.session, self.upload = session, False

    def request(self, method, url, **kwargs):
        if self.upload:
            body = kwargs.pop("json")
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps(body), content_type="application/json")
            form.add_field("files[0]", b"lab-file\n", filename="lab.txt", content_type="text/plain")
            kwargs["data"] = form
        return self.session.request(method, url, **kwargs)


async def require(adapter, actor, method, path, **kwargs):
    status, data = await adapter.request(actor, method, path, **kwargs)
    if not 200 <= status < 300:
        code = data.get("code") if isinstance(data, dict) else None
        raise RuntimeError(
            f"Setup/cleanup refused: {method} HTTP {status} code {code}; see redacted receipts"
        )
    return data


async def cleanup(adapter):
    journal = adapter.journal
    # Revoke only our own temporary grants before restoring member state; an
    # administrator subject cannot legally be timed out, even during recovery.
    for item in journal.state["owned"]:
        if item["kind"] == "role" and not item["deleted"]:
            journal.prove(item)
            uncertain = journal.state.get("uncertain_writes", []) + (
                [journal.state["pending_write"]] if journal.state.get("pending_write") else []
            )
            # DELETE is not replaying the uncertain PATCH. The role's create
            # receipt authorizes its removal, safely revoking any possible grant.
            if any(intent["method"] == "PATCH" and intent["path"] == item["path"] for intent in uncertain):
                await require(adapter, "admin", "DELETE", item["path"], cleanup=True)
            else:
                await require(
                    adapter, "admin", "PATCH", item["path"], body={"permissions": "0"}, cleanup=True
                )
    for restore in reversed(journal.state["restores"]):
        if restore.get("done"):
            continue
        parts = restore["path"].strip("/").split("/")
        if restore["method"] == "DELETE" and len(parts) == 6 and parts[4] == "roles":
            role = next(
                (
                    item
                    for item in journal.state["owned"]
                    if item["id"] == parts[5] and item["kind"] == "role"
                ),
                None,
            )
            if role is not None and role["deleted"]:
                journal.prove_deleted(role)
                restore["done"] = True
                journal.save()
                continue
        await require(
            adapter, "admin", restore["method"], restore["path"], body=restore.get("body"), cleanup=True
        )
        restore["done"] = True
        journal.save()
    # Channels first: their positive DELETE receipts prove message/thread
    # cascades, avoiding both stale-parent writes and per-message delete churn.
    ordered = [item for item in reversed(journal.state["owned"]) if item["kind"] == "channel"]
    ordered += [item for item in reversed(journal.state["owned"]) if item["kind"] != "channel"]
    for item in ordered:
        if item["deleted"]:
            continue
        journal.prove(item)
        if journal.cascade(item):
            continue
        status, _ = await adapter.request(
            "subject" if item["kind"] == "command" else "admin", "DELETE", item["path"], cleanup=True
        )
        if status not in {200, 204, 404}:
            raise RuntimeError(f"Cleanup refused HTTP {status}; ownership journal preserved")
        item["deleted"] = True
        journal.save()
    if journal.state.get("pending_write") or journal.state.get("uncertain_writes"):
        raise RuntimeError(
            "Known owned resources cleaned; an ambiguous write remains. Inspect preserved receipts manually; never replay it."
        )


def open_evidence(directory, secrets, *, resume=False):
    return SafeEvidence(Path(directory), secrets, resume=resume)
