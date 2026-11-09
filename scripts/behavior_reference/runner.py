"""Live and offline runs share every request path, payload and setup transition."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import discord

from .transport import EMOJI_GUILD, require

BASE_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    read_message_history=True,
    add_reactions=True,
    embed_links=True,
    attach_files=True,
    external_emojis=True,
    send_tts_messages=True,
    mention_everyone=True,
    connect=True,
    speak=True,
    change_nickname=True,
    manage_messages=True,
    create_public_threads=True,
    create_private_threads=True,
    send_polls=True,
    pin_messages=True,
).value
MANAGER = discord.Permissions(manage_channels=True, manage_threads=True).value


def expand(value, symbols):
    if isinstance(value, dict):
        return {key: expand(item, symbols) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, symbols) for item in value]
    if isinstance(value, str):
        # Longest first avoids substituting $thread inside $thread-message.
        for key in sorted(symbols, key=len, reverse=True):
            value = value.replace("$" + key, str(symbols[key]))
    return value


class Runner:
    def __init__(self, adapter, *, gateway=None):
        self.adapter, self.gateway = adapter, gateway
        state = adapter.journal.state
        self.symbols = {
            "guild": state["guild"],
            "subject": state["subject"],
            "author": state["author"],
            "application": state["application"],
            "subject-role": state["subject_role"],
            "command-name": state["command_name"],
            "missing": "1",
        }
        self.channels = {}
        self.overwrites = {}
        self.role_permissions = None

    async def call(self, actor, method, path, body=None, **kwargs):
        return await self.adapter.request(
            actor, method, expand(path, self.symbols), body=expand(body, self.symbols), **kwargs
        )

    async def need(self, actor, method, path, body=None, **kwargs):
        return await require(
            self.adapter, actor, method, expand(path, self.symbols), body=expand(body, self.symbols), **kwargs
        )

    async def role(self, state="active", *, target="text"):
        manager = (
            discord.Permissions(manage_threads=True).value
            if target in {"thread", "own-thread", "news-thread"}
            else discord.Permissions(manage_channels=True).value
        )
        permissions = (
            BASE_PERMISSIONS
            | (manager if "manager" in state or state == "self-hide" else 0)
            | (8 if state == "administrator" else 0)
        )
        if permissions != self.role_permissions:
            await self.need(
                "admin", "PATCH", "/guilds/$guild/roles/$subject-role", {"permissions": str(permissions)}
            )
            self.role_permissions = permissions

    def initial_overwrites(self):
        return [
            {
                "id": self.symbols["guild"],
                "type": 0,
                "allow": "0",
                "deny": str(discord.Permissions(view_channel=True).value),
            },
            {"id": self.symbols["author"], "type": 1, "allow": str(BASE_PERMISSIONS | MANAGER), "deny": "0"},
            {"id": self.symbols["subject"], "type": 1, "allow": str(BASE_PERMISSIONS), "deny": "0"},
        ]

    async def setup(self):
        state = self.adapter.journal.state
        # Restore snapshots are durable BEFORE the first nickname/timeout write.
        for key in ("nick", "communication_disabled_until"):
            state["restores"].append(
                {
                    "method": "PATCH",
                    "path": f"/guilds/{self.symbols['guild']}/members/{self.symbols['subject']}",
                    "body": {key: state["baseline_member"].get(key)},
                    "done": False,
                }
            )
        self.adapter.journal.save()
        await self.role()
        for name, kind in (
            ("category", 4),
            ("text", 0),
            ("news", 5),
            ("voice", 2),
            ("stage", 13),
            ("forum", 15),
        ):
            body = {
                "name": "lab-behavior-" + name,
                "type": kind,
                "permission_overwrites": self.initial_overwrites(),
            }
            if name != "category":
                body["parent_id"] = self.symbols["category"]
            if name == "forum":
                body["available_tags"] = [{"name": "lab-tag", "moderated": False}]
            channel = await self.need("admin", "POST", "/guilds/$guild/channels", body)
            self.symbols[name] = str(channel["id"])
            self.channels[name] = channel
            self.overwrites[name] = ()
            if name == "forum":
                self.symbols["tag"] = str(channel["available_tags"][0]["id"])
        mention_role = await self.need(
            "admin",
            "POST",
            "/guilds/$guild/roles",
            {"name": "lab-unmentionable", "permissions": "0", "mentionable": False},
        )
        self.symbols["mention-role"] = str(mention_role["id"])
        for name in ("thread", "own-thread", "news-thread"):
            await self.create_thread(name)
        # All probe messages are freshly seeded before denials/archival.

    async def create_thread(self, name):
        actor = "subject" if name == "own-thread" else "admin"
        parent = self.parent(name)
        if name == "news-thread":
            message = await self.need(
                "admin",
                "POST",
                "/channels/$news/messages",
                {"content": "lab-thread-starter", "allowed_mentions": {"parse": []}},
            )
            path = f"/channels/$news/messages/{message['id']}/threads"
        else:
            path = f"/channels/${parent}/threads"
        thread = await self.need(
            actor,
            "POST",
            path,
            {
                "name": "lab-behavior-" + name,
                "auto_archive_duration": 1440,
                **({"type": 11} if name != "news-thread" else {}),
            },
        )
        self.symbols[name] = str(thread["id"])
        self.channels[name] = thread

    def parent(self, target):
        return "news" if target == "news-thread" else "text" if target in {"thread", "own-thread"} else target

    async def overwrite(self, target, deny):
        target = self.parent(target)
        if self.overwrites.get(target) == deny:
            return
        mask = discord.Permissions(**dict.fromkeys(deny, True)).value
        allow = BASE_PERMISSIONS & ~mask
        await self.need(
            "admin",
            "PUT",
            f"/channels/${target}/permissions/$subject",
            {"type": 1, "allow": str(allow), "deny": str(mask)},
        )
        self.overwrites[target] = deny

    async def prepare(self, case):
        await self.role(case.state, target=case.target)
        parent = self.parent(case.target)
        # First reset a reused channel; never seed while the preceding denial persists.
        await self.overwrite(parent, ())
        if case.target in {"thread", "own-thread", "news-thread"}:
            await self.need(
                "admin", "PATCH", f"/channels/${case.target}", {"archived": False, "locked": False}
            )
            await self.need("admin", "PUT", f"/channels/${case.target}/thread-members/$subject")
        if case.target == "forum":
            await self.need(
                "admin", "PATCH", "/channels/$forum", {"flags": 16 if case.state == "require-tag" else 0}
            )
        if case.operation in {
            "reply",
            "fetch",
            "pin",
            "unpin",
            "react",
            "react-existing",
            "unreact",
            "edit-message",
            "delete-message",
            "bulk-delete",
            "crosspost",
            "crosspost-other",
            "history",
            "pins",
        }:
            actor = "admin" if case.operation in {"reply", "crosspost-other", "react-existing"} else "subject"
            message = await self.need(
                actor,
                "POST",
                f"/channels/${case.target}/messages",
                {"content": "lab-seed", "allowed_mentions": {"parse": []}},
            )
            self.symbols["message"] = str(message["id"])
            if case.operation == "bulk-delete":
                second = await self.need(
                    "subject",
                    "POST",
                    f"/channels/${case.target}/messages",
                    {"content": "lab-seed-second", "allowed_mentions": {"parse": []}},
                )
                self.symbols["message2"] = str(second["id"])
            if case.operation == "unpin" or (case.operation == "pins" and case.state == "pinned"):
                await self.need("admin", "PUT", f"/channels/${case.target}/messages/pins/$message")
            if case.operation in {"react-existing", "unreact"}:
                await self.need(
                    "admin" if case.operation == "react-existing" else "subject",
                    "PUT",
                    f"/channels/${case.target}/messages/$message/reactions/%F0%9F%94%A5/@me",
                )
        if case.target in {"thread", "own-thread", "news-thread"} and case.state.startswith(
            ("archived", "locked")
        ):
            await self.need(
                "admin",
                "PATCH",
                f"/channels/${case.target}",
                {"archived": True, "locked": case.state.startswith("locked")},
            )
        if case.target in {"thread", "own-thread", "news-thread"} and case.state == "active-locked":
            await self.need(
                "admin", "PATCH", f"/channels/${case.target}", {"archived": False, "locked": True}
            )
        await self.overwrite(parent, case.deny)
        backend = getattr(self.adapter, "backend", None)
        now = backend.clock.now() if backend is not None else datetime.now(UTC)
        self.symbols.update(
            {
                "future-minute": (now + timedelta(minutes=1)).isoformat(),
                "future-29days": (now + timedelta(days=29)).isoformat(),
                "past-minute": (now - timedelta(minutes=1)).isoformat(),
            }
        )
        if case.operation == "external-emoji" and "external-emoji" not in self.symbols:
            # Read-only secondary guild: no emoji creation, no command changes.
            emojis = await self.need("admin", "GET", f"/guilds/{EMOJI_GUILD}/emojis")
            usable = [emoji for emoji in emojis if not emoji.get("roles") and emoji.get("available", True)]
            if not usable:
                raise RuntimeError(
                    "External emoji capture requires an existing unrestricted emoji in the approved secondary guild"
                )
            self.symbols["external-emoji"] = str(usable[0]["id"])
            self.symbols["external-emoji-name"] = usable[0]["name"]

    def request(self, case):
        target = "$" + case.target
        op, body = case.operation, copy.deepcopy(case.body)
        method, path, query, file = "POST", f"/channels/{target}/messages", None, False
        if op in {"send", "file", "reply", "external-emoji"}:
            file = op == "file"
            if file:
                body["attachments"] = [{"id": "0", "filename": "lab.txt"}]
            if op == "reply":
                body = {
                    "content": "lab-reply",
                    "message_reference": {
                        "message_id": "$message",
                        "channel_id": target,
                        "guild_id": "$guild",
                    },
                }
            if op == "external-emoji":
                body = {"content": "<:$external-emoji-name:$external-emoji>"}
            body.setdefault("allowed_mentions", {"parse": []})
        elif op in {"history", "fetch", "pins"}:
            method = "GET"
            path += "/$message" if op == "fetch" else "/pins" if op == "pins" else ""
            query = {"limit": "1"} if op in {"history", "pins"} else None
        elif op in {"pin", "unpin"}:
            method, path = ("PUT" if op == "pin" else "DELETE"), f"/channels/{target}/messages/pins/$message"
        elif op in {"crosspost", "crosspost-other"}:
            path += "/$message/crosspost"
        elif op in {"react", "react-existing", "unreact"}:
            method = "DELETE" if op == "unreact" else "PUT"
            path += "/$message/reactions/%F0%9F%94%A5/@me"
        elif op in {"edit-message", "delete-message"}:
            method = "DELETE" if op == "delete-message" else "PATCH"
            path += "/$message"
        elif op == "bulk-delete":
            path += "/bulk-delete"
            body = {"messages": ["$message", "$message2"]}
        elif op in {"edit-channel", "delete-channel"}:
            method, path = "DELETE" if op == "delete-channel" else "PATCH", f"/channels/{target}"
        elif op in {"leave", "join"}:
            method, path = "DELETE" if op == "leave" else "PUT", f"/channels/{target}/thread-members/@me"
        elif op == "nickname":
            method, path = "PATCH", "/guilds/$guild/members/@me"
        elif op == "timeout":
            method, path = "PATCH", "/guilds/$guild/members/$subject"
        elif op == "forum-post":
            path = f"/channels/{target}/threads"
        elif op == "command":
            path = "/applications/$application/guilds/$guild/commands"
        elif op == "channel":
            method, path = "GET", f"/channels/{target}"
        elif op == "guild-channels":
            method, path = "GET", "/guilds/$guild/channels"
        elif op in {"create-channel", "gateway-create"}:
            path = "/guilds/$guild/channels"
            kind = (
                {"voice": 2, "forum": 15, "category": 4}.get(case.target, 0) if op == "gateway-create" else 0
            )
            body = {
                "name": "lab-self-hidden" if op == "create-channel" else "lab-gateway-hidden",
                "type": kind,
                "permission_overwrites": self.initial_overwrites(),
            }
            if kind != 4:
                body["parent_id"] = "$category"
            if op == "create-channel":
                for overwrite in body["permission_overwrites"]:
                    if overwrite["id"] == self.symbols["author"]:
                        overwrite["allow"] = str(BASE_PERMISSIONS)
            for overwrite in body["permission_overwrites"]:
                if overwrite["id"] == self.symbols["subject"]:
                    overwrite.update(allow="0", deny="1024")
        elif op == "gateway-update":
            method, path = "PATCH", "/channels/$text"
            body = {"permission_overwrites": self.initial_overwrites()}
            for overwrite in body["permission_overwrites"]:
                if overwrite["id"] == self.symbols["subject"]:
                    overwrite.update(allow="0", deny="1024")
        else:
            raise ValueError(f"Unknown operation {op}")
        return (
            method,
            expand(path, self.symbols),
            expand(body, self.symbols) if method not in {"GET", "DELETE", "PUT"} or body else None,
            query,
            file,
        )

    async def run(self, case):
        from .ledger import observation

        self.adapter.evidence.mark(case.id)
        self.symbols.pop("result", None)
        await self.prepare(case)
        method, path, body, query, file = self.request(case)
        sending = case.operation in {"send", "file", "reply", "external-emoji"}
        before = (
            await self.call("admin", "GET", f"/channels/${case.target}/messages", query={"limit": "1"})
            if sending
            else None
        )
        gateway_start = len(self.gateway.events) if self.gateway else 0
        actor = "admin" if case.operation.startswith("gateway-") else case.actor
        status, response = await self.adapter.request(actor, method, path, body=body, query=query, file=file)
        effect = {}
        if sending:
            after = await self.call(
                "admin", "GET", f"/channels/${case.target}/messages", query={"limit": "1"}
            )
            effect["history_probe"] = {"before_status": before[0], "after_status": after[0]}
            if before[0] == after[0] == 200:
                before_id = before[1][0]["id"] if before[1] else None
                after_id = after[1][0]["id"] if after[1] else None
                effect["history_probe"]["new_message"] = before_id != after_id
        if 200 <= status < 300:
            if isinstance(response, dict) and response.get("id"):
                self.symbols["result"] = str(response["id"])
            if case.operation in {
                "send",
                "file",
                "reply",
                "external-emoji",
                "edit-message",
                "pin",
                "unpin",
                "react",
                "react-existing",
                "unreact",
                "crosspost",
                "crosspost-other",
            }:
                message_id = (
                    str(response["id"])
                    if isinstance(response, dict) and response.get("id")
                    else self.symbols["message"]
                )
                effect_status, effect_message = await self.call(
                    "admin", "GET", f"/channels/${case.target}/messages/{message_id}"
                )
                effect["message"] = {"status": effect_status, "body": effect_message}
            if case.operation in {"create-channel", "gateway-create", "gateway-update"}:
                channel_id = (
                    response["id"]
                    if isinstance(response, dict) and response.get("id")
                    else self.symbols["text"]
                )
                if self.gateway:
                    event = await self.gateway.channel_event(
                        gateway_start,
                        channel_id,
                        "CHANNEL_UPDATE" if case.operation == "gateway-update" else "CHANNEL_CREATE",
                    )
                else:
                    event = self.adapter.gateway_channel(
                        gateway_start,
                        channel_id,
                        "CHANNEL_UPDATE" if case.operation == "gateway-update" else "CHANNEL_CREATE",
                    )
                effect["gateway_channel"] = event
            if case.operation == "command":
                _, stored = await self.call(
                    "subject", "GET", f"/applications/$application/guilds/$guild/commands/{response['id']}"
                )
                effect["stored_command"] = stored
        if case.target in {"thread", "own-thread", "news-thread"}:
            effect_status, effect_channel = await self.call("admin", "GET", f"/channels/${case.target}")
            effect["thread"] = {"status": effect_status, "body": effect_channel}
        if case.operation in {"delete-message", "bulk-delete"} or (
            status >= 300
            and case.operation
            in {
                "edit-message",
                "pin",
                "unpin",
                "react",
                "react-existing",
                "unreact",
                "crosspost",
                "crosspost-other",
            }
        ):
            effect_status, effect_message = await self.call(
                "admin", "GET", f"/channels/${case.target}/messages/$message"
            )
            effect["message"] = {"status": effect_status, "body": effect_message}
        if case.operation == "bulk-delete":
            effect_status, effect_message = await self.call(
                "admin", "GET", f"/channels/${case.target}/messages/$message2"
            )
            effect["second_message"] = {"status": effect_status, "body": effect_message}
        row = observation(
            case,
            method,
            case.operation,
            status,
            response,
            effect,
            self.symbols,
            request={"actor": actor, "path": path, "body": body, "query": query, "file": file},
            source_kind=getattr(self.adapter, "kind", "live"),
        )
        # Persist each completed observation even if a later case is interrupted.
        self.adapter.evidence.record("observations", row=row)
        await self.reset(case)
        return row

    async def reset(self, case):
        # Never leave a timeout, nickname, admin grant or denied reused parent between rows.
        await self.role()
        if case.operation in {"timeout", "nickname"}:
            field = "communication_disabled_until" if case.operation == "timeout" else "nick"
            await self.need(
                "admin",
                "PATCH",
                "/guilds/$guild/members/$subject",
                {field: self.adapter.journal.state["baseline_member"].get(field)},
            )
        await self.overwrite(case.target, ())
        if case.operation == "gateway-update":
            # PATCH changed overwrites outside the cached member-overwrite transition.
            self.overwrites["text"] = None
            await self.overwrite("text", ())
        if case.operation == "delete-channel" and not self.adapter.journal.owns(
            self.symbols[case.target], "channel"
        ):
            await self.create_thread(case.target)
        # Delete per-row creates immediately; pooled fixtures live until final cleanup.
        if case.operation in {"command", "forum-post", "create-channel", "gateway-create"}:
            for item in reversed(self.adapter.journal.state["owned"]):
                if item["id"] == self.symbols.get("result") and not item["deleted"]:
                    self.adapter.journal.prove(item)
                    await self.need(
                        "subject" if item["kind"] == "command" else "admin", "DELETE", item["path"]
                    )
                    item["deleted"] = True
                    self.adapter.journal.save()
                    break


async def record(adapter, cases, token, session):
    from .gateway import Gateway

    async with Gateway(session, token, adapter.evidence) as gateway:
        runner = Runner(adapter, gateway=gateway)
        await runner.setup()
        return [await runner.run(case) for case in cases]
