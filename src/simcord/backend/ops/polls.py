"""Poll voting and expiry."""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import Any

from .. import errors, serializers
from ..models import Message
from .base import BackendBase


class PollMixin(BackendBase):
    def set_poll_votes(
        self, channel_id: int, message_id: int, answer_ids: Sequence[int], user_id: int
    ) -> None:
        if isinstance(answer_ids, (str, bytes, bytearray)) or not isinstance(answer_ids, Sequence):
            raise errors.invalid_form_body("poll answer ids must be a sequence")
        selected: set[int] = set()
        for answer_id in answer_ids:
            if isinstance(answer_id, bool) or not isinstance(answer_id, int):
                raise errors.invalid_form_body("poll answer ids must be integers")
            if answer_id in selected:
                raise errors.invalid_form_body("poll answer ids must be unique")
            selected.add(answer_id)

        message = self.get_message(channel_id, message_id)
        poll = message.poll
        if poll is None:
            raise errors.invalid_form_body("message has no poll")
        if any(poll.answer(answer_id) is None for answer_id in selected):
            raise errors.invalid_form_body("poll answer does not exist")
        if not poll.allow_multiselect and len(selected) > 1:
            raise errors.invalid_form_body("poll accepts one answer")

        try:
            expired = datetime.datetime.fromisoformat(poll.expiry) <= datetime.datetime.fromisoformat(
                self.now_iso()
            )
        except (TypeError, ValueError) as exc:
            raise errors.invalid_form_body("poll expiry is invalid") from exc
        if poll.finalized or expired:
            if expired and not poll.finalized:
                self.expire_poll(channel_id, message_id)
            raise errors.invalid_form_body("poll is finalized")

        current = {answer_id for answer_id, voters in poll.votes.items() if user_id in voters}
        removed = sorted(current - selected)
        added = sorted(selected - current)
        for answer_id in removed:
            poll.votes[answer_id].discard(user_id)
        for answer_id in added:
            poll.votes.setdefault(answer_id, set()).add(user_id)
        for answer_id in removed:
            self._emit_poll_vote("MESSAGE_POLL_VOTE_REMOVE", message, answer_id, user_id)
        for answer_id in added:
            self._emit_poll_vote("MESSAGE_POLL_VOTE_ADD", message, answer_id, user_id)

    def add_poll_vote(self, channel_id: int, message_id: int, answer_id: int, user_id: int) -> None:
        message = self.get_message(channel_id, message_id)
        if message.poll is None:
            raise errors.invalid_form_body("message has no poll")
        current = {existing for existing, voters in message.poll.votes.items() if user_id in voters}
        selected = current | {answer_id} if message.poll.allow_multiselect else {answer_id}
        self.set_poll_votes(channel_id, message_id, sorted(selected), user_id)

    def remove_poll_vote(self, channel_id: int, message_id: int, answer_id: int, user_id: int) -> None:
        message = self.get_message(channel_id, message_id)
        if message.poll is None:
            raise errors.invalid_form_body("message has no poll")
        if message.poll.answer(answer_id) is None:
            raise errors.invalid_form_body("poll answer does not exist")
        current = {existing for existing, voters in message.poll.votes.items() if user_id in voters}
        self.set_poll_votes(channel_id, message_id, sorted(current - {answer_id}), user_id)

    def _emit_poll_vote(self, event: str, message: Message, answer_id: int, user_id: int) -> None:
        channel = self.get_channel(message.channel_id)
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "channel_id": str(message.channel_id),
            "message_id": str(message.id),
            "answer_id": answer_id,
        }
        if channel.guild_id is not None:
            payload["guild_id"] = str(channel.guild_id)
        self.emit(event, payload)

    def expire_poll(self, channel_id: int, message_id: int) -> Message:
        message = self.get_message(channel_id, message_id)
        if message.poll is not None and not message.poll.finalized:
            message.poll.finalized = True
            self.emit("MESSAGE_UPDATE", dict(serializers.message_payload(self, message)))
        return message

    def expire_due_polls(self) -> None:
        """Finalize any polls whose expiry has passed (driven by the virtual clock)."""
        now = datetime.datetime.fromisoformat(self.now_iso())
        for channel_messages in self.messages.values():
            for message in channel_messages.values():
                poll = message.poll
                if poll is None or poll.finalized:
                    continue
                if datetime.datetime.fromisoformat(poll.expiry) <= now:
                    self.expire_poll(message.channel_id, message.id)
