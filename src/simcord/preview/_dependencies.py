"""Request-local authorization facts for frozen preview domains."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError

if TYPE_CHECKING:
    from . import Preview
    from ._pages import _Page


class _DependencyRead:
    """Memoize this read only; loss means a published dependency disappeared."""

    def __init__(self, preview: Preview, page: _Page) -> None:
        self.preview = preview
        self.page = page
        self.lost = False
        self._entities: dict[tuple[str, str], bool] = {}
        self._messages: dict[tuple[int, int], bool] = {}
        self._assets: dict[str, bool] = {}

    def retain(self, allowed: bool) -> bool:
        self.lost |= not allowed
        return allowed

    def entity(self, kind: str, identity: Any) -> bool:
        from ._component_projection import _known_emoji
        from ._messages import _role_allowed, _user_allowed

        key = (kind, str(identity))
        if key not in self._entities:
            try:
                value = 0 if kind == "everyone" else int(identity)
                if kind == "everyone":
                    allowed = True
                elif kind in {"channel", "history_channel"}:
                    allowed = can_access_channel(
                        self.preview.env, value, self.page.viewer, history=kind == "history_channel"
                    )
                elif kind == "role":
                    allowed = _role_allowed(self.preview, self.page, value)
                elif kind == "emoji":
                    allowed = _known_emoji(self.page, str(value)) is not None
                elif kind == "command":
                    from ._commands import command_permission

                    allowed = command_permission(self.preview, self.page) and any(
                        str(command.get("id")) == str(value)
                        for command in self.preview.env.backend.visible_commands(
                            user_id=self.page.viewer.id, channel_id=self.page.channel_id
                        )
                    )
                else:
                    allowed = _user_allowed(self.preview, self.page, value)
            except (BackendError, TypeError, ValueError):
                allowed = False
            self._entities[key] = allowed
        return self.retain(self._entities[key])

    def message(self, channel_id: Any, message_id: Any) -> bool:
        try:
            key = (int(channel_id), int(message_id))
        except (TypeError, ValueError):
            return self.retain(False)
        if key not in self._messages:
            try:
                message = self.preview.env.backend.get_message(*key)
                allowed = can_access_message(
                    self.preview.env, key[0], message, self.page.viewer, history=True
                )
            except BackendError:
                allowed = False
            self._messages[key] = allowed
        return self.retain(self._messages[key])

    def asset(self, identity: str | None) -> bool:
        if identity is None:
            return True
        if identity not in self._assets:
            try:
                self.preview._authorize_asset(self.page, identity, content=False)
                allowed = True
            except SetupError:
                allowed = False
            self._assets[identity] = allowed
        return self.retain(self._assets[identity])
