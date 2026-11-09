"""Replay the recorder's exact raw requests through the real backend router."""

from __future__ import annotations

import copy
import io
from types import SimpleNamespace

import discord

from simcord.backend import Backend
from simcord.backend.errors import BackendError
from simcord.gateway import FakeGateway
from simcord.http import dispatch

from .transport import EMOJI_GUILD, LAB_GUILD, Journal, open_evidence, require


class Observer:
    def __init__(self):
        self.events = []

    async def channel_event(self, start, channel_id, event):
        for name, data in self.events[start:]:
            if name == event and str(data.get("id")) == str(channel_id):
                return data
        return {"missing_event": event}


class Parsers(dict):
    def __init__(self, observer):
        super().__init__()
        self.observer = observer

    def __missing__(self, event):
        def parse(payload):
            self.observer.events.append((event, copy.deepcopy(payload)))

        self[event] = parse
        return parse


class Offline:
    kind = "offline"

    def __init__(self, backend, actors, evidence, journal):
        self.backend, self.actors, self.evidence, self.journal = backend, actors, evidence, journal
        self.gateway = Observer()
        gateway = FakeGateway(
            backend, SimpleNamespace(intents=discord.Intents.all(), parsers=Parsers(self.gateway))
        )

        def feed(event, payload):
            previous = backend.bot_user
            backend.bot_user = actors["subject"]
            try:
                gateway.feed(event, payload)
            finally:
                backend.bot_user = previous

        backend.subscribers.append(feed)

    @classmethod
    async def create(cls, directory, provenance=None):
        backend = Backend()
        author = backend.bot_user
        guild = backend.create_guild("lab-offline", id=int(LAB_GUILD))
        # The author is a genuinely separate bot; only this synthetic offline
        # setup grants administration to its existing synthetic integration role.
        member = backend.get_member(guild.id, author.id)
        backend.edit_role(guild.id, member.role_ids[0], {"permissions": discord.Permissions.all().value})
        subject = backend.make_user("Zoku", bot=True)
        backend.add_member(guild.id, subject.id)
        other = backend.create_guild("lab-external", id=int(EMOJI_GUILD))
        backend.add_member(other.id, subject.id)
        backend.create_emoji(other.id, "lab_external", author.id)
        evidence = open_evidence(directory / "evidence", ())
        journal = Journal(evidence, LAB_GUILD)
        journal.state.update(
            subject=str(subject.id),
            author=str(author.id),
            application=str(subject.id),
            command_name="lab-offline-command",
            baseline_member={"nick": None, "communication_disabled_until": None},
        )
        journal.save()
        backend.application_id = subject.id
        adapter = cls(backend, {"admin": author, "subject": subject}, evidence, journal)
        role = await require(
            adapter,
            "admin",
            "POST",
            f"/guilds/{LAB_GUILD}/roles",
            body={"name": "lab-behavior-subject", "permissions": "0", "mentionable": False},
        )
        await require(adapter, "admin", "PUT", f"/guilds/{LAB_GUILD}/members/{subject.id}/roles/{role['id']}")
        journal.state["subject_role"] = str(role["id"])
        journal.save()
        return adapter

    async def request(self, actor, method, path, *, body=None, query=None, file=False, cleanup=False):
        self.journal.authorize(method, path, body)
        if method != "GET":
            self.journal.start(method, path, body, query, cleanup=cleanup)
        reference = f"requests.jsonl:{self.evidence.counts.get('requests', 0) + 1}"
        self.evidence.record(
            "requests", request_id=reference, method=method, path=path, query=query, body=body
        )
        previous = self.backend.bot_user
        self.backend.bot_user = self.actors[actor]
        files = [discord.File(io.BytesIO(b"lab-file\n"), filename="lab.txt")] if file else []
        try:
            response = dispatch(
                self.backend, method, path, json=copy.deepcopy(body), params=copy.deepcopy(query), files=files
            )
            status = success_status(method, path, response)
        except BackendError as error:
            status, response = error.status, error.to_json()
        finally:
            self.backend.bot_user = previous
            for upload in files:
                upload.close()
        receipt = self.evidence.record(
            "rest",
            request_id=reference,
            method=method,
            path_query=path,
            body=body,
            status=status,
            response=response,
        )
        if method != "GET":
            self.journal.finish(status, response, receipt)
        return status, response

    def gateway_channel(self, start, channel_id, event):
        for name, payload in self.gateway.events[start:]:
            if name == event and str(payload.get("id")) == str(channel_id):
                return payload
        return {"missing_event": event}


def success_status(method, path, response):
    # Router returns JSON rather than an HTTP response. Discord's documented
    # endpoint success statuses are transport metadata, not inferred evidence.
    if response is None:
        return 204
    if method == "POST" and (
        path.endswith("/channels") or path.endswith("/threads") or path.endswith("/commands")
    ):
        return 201
    return 200
