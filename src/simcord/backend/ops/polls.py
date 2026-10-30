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
        poll = message.poll
        if poll is not None and not poll.finalized:
            # ponytail: O(history) on explicit expiry; index aliases only if this becomes hot.
            for messages in self.messages.values():
                for alias in messages.values():
                    if alias.poll is poll:
                        self._poll_deadlines.set((alias.channel_id, alias.id), None, ())
            self._finalize_poll(message)
        return message

    def _finalize_poll(self, message: Message) -> None:
        assert message.poll is not None
        message.poll.finalized = True
        self._schedule_poll(message)
        self.emit("MESSAGE_UPDATE", dict(serializers.message_payload(self, message)))

    def next_poll_deadline(self) -> datetime.datetime | None:
        return self._poll_deadlines.next()

    def expire_due_polls(self) -> None:
        """Finalize any polls whose expiry has passed (driven by the virtual clock)."""
        now = datetime.datetime.fromisoformat(self.now_iso())
        with self._poll_deadlines.due(now) as due:
            for channel_id, message_id in due:
                message = self.messages.get(channel_id, {}).get(message_id)
                if message is None or message.poll is None or message.poll.finalized:
                    self._poll_deadlines.set((channel_id, message_id), None, ())
                    continue
                if datetime.datetime.fromisoformat(message.poll.expiry) <= now:
                    self._finalize_poll(message)
