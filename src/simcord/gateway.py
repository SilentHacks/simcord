"""Feeds raw gateway payloads into discord.py's real event parsers.

This mirrors what ``DiscordWebSocket.received_message`` does after the
transport layer (decompression, sequence tracking): look up the parser for the
event type on the connection state and call it. Everything downstream —
parsing, caching, dispatching to user handlers — is real discord.py code.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import Any

_log = logging.getLogger(__name__)


class FakeGateway:
    def __init__(self, connection_state: Any) -> None:
        self._state = connection_state

    def feed(self, event: str, payload: Mapping[str, Any]) -> None:
        try:
            parser = self._state.parsers[event]
        except KeyError:
            _log.debug("Ignoring unknown gateway event %s", event)
            return
        # Deep copy: discord.py may mutate the payload, and the original is
        # backend state shared with other subscribers.
        parser(copy.deepcopy(payload))
