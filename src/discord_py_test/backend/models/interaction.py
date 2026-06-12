from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ResponseKind(StrEnum):
    """How the bot answered an interaction (Discord callback semantics)."""

    MESSAGE = "message"  # type 4: channel message with source
    DEFERRED = "deferred"  # type 5: deferred channel message (a later edit materialises it)
    DEFERRED_UPDATE = "deferred_update"  # type 6: deferred update of the component's message
    UPDATE = "update"  # type 7: update the component's message
    MODAL = "modal"  # type 9
    AUTOCOMPLETE = "autocomplete"  # type 8
    PONG = "pong"  # type 1


@dataclass
class Interaction:
    """A single interaction and the evolving state of the bot's response to it.

    The response lifecycle ("respond once", "a deferred response materialises on
    edit") lives here as methods so the invariants are in one place rather than
    re-derived in each route handler. ``responded`` is only set by the marker
    methods, which route handlers call after the callback has been validated.
    """

    id: int
    token: str
    type: int
    channel_id: int
    guild_id: int | None
    user_id: int
    responded: bool = False
    response_kind: ResponseKind | None = None
    message_id: int | None = None
    source_message_id: int | None = None
    ephemeral: bool = False
    followup_ids: list[int] = field(default_factory=list)
    modal: dict[str, Any] | None = None
    autocomplete_choices: list[dict[str, Any]] | None = None

    # ----------------------------------------------------- response lifecycle

    def respond_with_message(self, message_id: int, *, ephemeral: bool) -> None:
        self.response_kind = ResponseKind.MESSAGE
        self.message_id = message_id
        self.ephemeral = ephemeral
        self.responded = True

    def defer(self, *, ephemeral: bool) -> None:
        self.response_kind = ResponseKind.DEFERRED
        self.ephemeral = ephemeral
        self.responded = True

    def defer_update(self) -> None:
        # @original is the clicked message; a later edit edits it in place.
        self.response_kind = ResponseKind.DEFERRED_UPDATE
        self.message_id = self.source_message_id
        self.responded = True

    def update_source(self, message_id: int | None) -> None:
        self.response_kind = ResponseKind.UPDATE
        self.message_id = message_id
        self.responded = True

    def show_modal(self, modal: dict[str, Any]) -> None:
        self.response_kind = ResponseKind.MODAL
        self.modal = modal
        self.responded = True

    def complete_autocomplete(self, choices: list[dict[str, Any]]) -> None:
        self.response_kind = ResponseKind.AUTOCOMPLETE
        self.autocomplete_choices = choices
        self.responded = True

    def pong(self) -> None:
        self.response_kind = ResponseKind.PONG
        self.responded = True

    def materialise_deferred(self, message_id: int) -> None:
        """Turn a deferred (type 5) response into its real message on first edit."""
        self.response_kind = ResponseKind.MESSAGE
        self.message_id = message_id

    # ----------------------------------------------------------- derived view

    @property
    def deferred(self) -> bool:
        return self.response_kind in (ResponseKind.DEFERRED, ResponseKind.DEFERRED_UPDATE)

    @property
    def loading(self) -> bool:
        """Whether Discord would show the 'thinking…' state (a pending type-5 defer)."""
        return self.response_kind == ResponseKind.DEFERRED
