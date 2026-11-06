"""Small public-protocol observer; never logs IDENTIFY or session credentials."""

from __future__ import annotations

import asyncio
import contextlib

from .transport import LAB_GUILD


class Gateway:
    def __init__(self, session, token, evidence):
        self.session, self.token, self.evidence = session, token, evidence
        self.sequence = None
        self.events = []
        self.ready = asyncio.Event()
        self.changed = asyncio.Event()
        self.task = self.heartbeat = self.socket = None

    async def __aenter__(self):
        self.socket = await self.session.ws_connect(
            "wss://gateway.discord.gg/?v=10&encoding=json", autoping=True
        )
        hello = await self.socket.receive_json(timeout=30)
        if hello.get("op") != 10:
            raise RuntimeError("Gateway did not send HELLO")
        await self.socket.send_json(
            {
                "op": 2,
                "d": {
                    "token": self.token,
                    "intents": 1 | 2 | 128 | 512 | 1024 | 32768,
                    "capabilities": 1 << 15,
                    "properties": {
                        "os": "linux",
                        "browser": "simcord-behavior-reference",
                        "device": "simcord-behavior-reference",
                    },
                },
            }
        )
        self.heartbeat = asyncio.create_task(self.beat(hello["d"]["heartbeat_interval"] / 1000))
        self.task = asyncio.create_task(self.receive())
        try:
            await asyncio.wait_for(self.ready.wait(), 35)
            self.check()
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def beat(self, interval):
        while True:
            await self.socket.send_json({"op": 1, "d": self.sequence})
            await asyncio.sleep(interval)

    async def receive(self):
        try:
            while True:
                packet = await self.socket.receive_json()
                if packet.get("s") is not None:
                    self.sequence = packet["s"]
                if packet.get("op") in {7, 9}:
                    raise RuntimeError("Gateway reconnect/invalid session; no automatic replay or recapture")
                if packet.get("op") == 1:
                    await self.socket.send_json({"op": 1, "d": self.sequence})
                if packet.get("op") != 0:
                    continue
                event, data = packet["t"], packet["d"]
                # READY contains session data; never retain it. Other guilds are not evidence.
                if event == "GUILD_CREATE" and str(data.get("id")) == LAB_GUILD:
                    self.ready.set()
                if str(data.get("guild_id", data.get("id") if event == "GUILD_CREATE" else "")) != LAB_GUILD:
                    continue
                self.evidence.record("gateway", event=event, data=data)
                self.events.append((event, data))
                self.changed.set()
        finally:
            self.ready.set()
            self.changed.set()

    def check(self):
        for task in (self.task, self.heartbeat):
            if task and task.done():
                task.result()
                raise RuntimeError("Gateway closed before observation completed")

    async def channel_event(self, start, channel_id, event):
        async with asyncio.timeout(8):
            while True:
                self.check()
                for name, data in self.events[start:]:
                    if name == event and str(data.get("id")) == str(channel_id):
                        return data
                self.changed.clear()
                await self.changed.wait()

    async def __aexit__(self, *exc):
        for task in (self.task, self.heartbeat):
            if task:
                task.cancel()
        for task in (self.task, self.heartbeat):
            if task:
                with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                    await task
        if self.socket:
            await self.socket.close()
