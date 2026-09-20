"""Browser action admission, sequencing, dispatch, and settlement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

from ..actors import MemberActor, _find_component, _modal_control_map, _modal_submit_nodes
from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..builders import ChannelHandle, GuildHandle, RoleHandle, UserHandle
from ..components import validate_modal
from ..enums import SELECT_TYPES, ComponentType
from ..results import ResponseMessage

if TYPE_CHECKING:
    from ..backend.models import Interaction, Message
    from ..env import Env
    from ..results import InteractionResult
    from ._pages import _Page


@dataclass(slots=True)
class _Action:
    sequence: int
    request_id: str
    fingerprint: str
    response: dict[str, Any] | None = None
    interaction: Interaction | None = None


class _ActionOps:
    """Ordered action admission: validate first, consume the sequence, settle."""

    env: Env
    _active_action: _Action | None
    _active_task: asyncio.Task[Any] | None
    _action_page: _Page | None
    _pending_page_closes: set[str]
    _close_task: asyncio.Task[None] | None
    _get_page: Callable[[str | None], _Page]
    _close_page: Callable[[str], None]
    close: Callable[[], Coroutine[Any, Any, None]]
    _viewer: Callable[[Any], Any]
    _initial_target: Callable[[Any, int], int | None]
    _target_id: Callable[..., int | None]
    _clear_page_assets: Callable[[_Page], None]
    _publish: Callable[[_Page], None]

    _MUTATING_KINDS: ClassVar[frozenset[str]] = frozenset({"click", "select", "modal_submit"})
    _ACTION_KINDS: ClassVar[frozenset[str]] = frozenset(
        {"click", "select", "modal_submit", "viewer", "focus", "refresh", "close"}
    )

    def _on_dispatch(self, interaction: Interaction) -> None:
        task = asyncio.current_task()
        if self._active_action is not None and task is self._active_task:
            self._active_action.interaction = interaction

    @staticmethod
    def _result(
        page: _Page,
        *,
        request_id: Any,
        sequence: Any,
        rejected: bool,
        dispatch: str,
        acknowledgement: str,
        settlement: str,
        diagnostics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """The one action-result envelope shared by rejects, replays, settles."""
        return {
            "requestId": request_id,
            "sequence": sequence,
            "expectedSequence": page.last_sequence,
            "rejected": rejected,
            "dispatched": dispatch == "dispatched",
            "dispatch": dispatch,
            "acknowledgement": acknowledgement,
            "settlement": settlement,
            "presentation": page.status,
            "revision": page.revision,
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _reject(
        page: _Page,
        code: str,
        message: str,
        *,
        request_id: Any = None,
        sequence: Any = None,
    ) -> dict[str, Any]:
        """A pre-admission rejection: never consumes the per-page sequence."""
        return _ActionOps._result(
            page,
            request_id=request_id,
            sequence=sequence,
            rejected=True,
            dispatch="not_dispatched",
            acknowledgement="pending",
            settlement="rejected",
            diagnostics=[{"code": code, "severity": "error", "message": message}],
        )

    async def _action(self, context_id: str | None, body: Mapping[str, Any]) -> dict[str, Any]:
        page = self._get_page(context_id)
        if not isinstance(body, Mapping):
            return self._reject(page, "bad-envelope", "action must be an object")
        sequence = body.get("sequence")
        request_id = body.get("request_id")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            return self._reject(
                page, "bad-envelope", "sequence must be a positive integer", request_id=request_id
            )
        if not isinstance(request_id, str) or not request_id:
            return self._reject(
                page,
                "bad-envelope",
                "request_id must be a non-empty string",
                sequence=sequence,
            )
        generation = body.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            return self._reject(
                page,
                "bad-envelope",
                "generation must be a positive integer",
                request_id=request_id,
                sequence=sequence,
            )
        bot_generation = body.get("bot_generation")
        if not isinstance(bot_generation, int) or isinstance(bot_generation, bool) or bot_generation < 1:
            return self._reject(
                page,
                "bad-envelope",
                "bot_generation must be a positive integer",
                request_id=request_id,
                sequence=sequence,
            )
        fingerprint = hashlib.sha256(
            json.dumps(dict(body), sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        latest = page.latest_action
        if sequence <= page.last_sequence:
            if sequence == page.last_sequence and latest is not None and latest.request_id == request_id:
                if latest.fingerprint != fingerprint:
                    return self._reject(
                        page,
                        "conflicting-request",
                        "request conflicts with the admitted sequence",
                        request_id=request_id,
                        sequence=sequence,
                    )
                if latest.response is None:
                    return self._result(
                        page,
                        request_id=latest.request_id,
                        sequence=latest.sequence,
                        rejected=False,
                        dispatch="pending",
                        acknowledgement="pending",
                        settlement="pending",
                        diagnostics=[],
                    )
                return dict(latest.response)
            return self._reject(
                page,
                "stale-sequence",
                "action sequence is stale",
                request_id=request_id,
                sequence=sequence,
            )
        if sequence != page.last_sequence + 1:
            return self._reject(
                page,
                "sequence-gap",
                "action sequence has a gap",
                request_id=request_id,
                sequence=sequence,
            )
        if self._active_action is not None:
            return self._reject(
                page,
                "busy",
                "Preview is busy with another action",
                request_id=request_id,
                sequence=sequence,
            )
        if generation != page.generation:
            return self._reject(
                page,
                "stale-context",
                "preview context generation is stale",
                request_id=request_id,
                sequence=sequence,
            )
        if bot_generation != self.env._generation:
            return self._reject(
                page,
                "stale-generation",
                "bot generation is stale",
                request_id=request_id,
                sequence=sequence,
            )
        kind = body.get("kind")
        if kind not in self._ACTION_KINDS:
            return self._reject(
                page,
                "unknown-kind",
                "unknown preview action",
                request_id=request_id,
                sequence=sequence,
            )
        if kind in self._MUTATING_KINDS and body.get("published_revision") != page.revision:
            return self._reject(
                page,
                "stale-revision",
                "published revision is stale",
                request_id=request_id,
                sequence=sequence,
            )
        token = self.env._begin_operation("preview.action")
        try:
            # Kind, control resolution, and values are validated before the
            # sequence is consumed; only a validated plan may be admitted.
            try:
                plan = self._prepare_action(page, kind, body)
            except (SetupError, BackendError, ValueError) as exc:
                return self._reject(
                    page,
                    "validation-failed",
                    str(exc),
                    request_id=request_id,
                    sequence=sequence,
                )
            action = _Action(sequence, request_id, fingerprint)
            page.last_sequence = sequence  # consumed only after full admission
            page.latest_action = action
            self._active_action = action
            self._active_task = asyncio.current_task()
            self._action_page = page
            cursor = self.env.error_cursor
            try:
                result = await plan(action, cursor)
            except asyncio.CancelledError:
                page.status = "stale"
                result = self._finish_action(page, action, "cancelled", cursor)
                action.response = result
                raise
            except TimeoutError:
                page.status = "stale"
                result = self._finish_action(page, action, "timeout", cursor)
            except (SetupError, BackendError, ValueError) as exc:
                # Expected mid-dispatch failures are reported honestly; they
                # may already have mutated the backend, so the page is stale.
                page.status = "stale"
                result = self._finish_action(page, action, "failed", cursor, exc)
            except Exception as exc:
                # Unexpected failures still settle the consumed sequence: the
                # recorded response lets an identical replay return the same
                # outcome instead of wedging on "pending" forever. Recording
                # keeps genuine bugs visible in env.errors for assertions.
                self.env._record_error(exc)
                page.status = "stale"
                result = self._finish_action(page, action, "failed", cursor, exc)
            action.response = result
            return dict(result)
        finally:
            self.env._end_operation(token)
            self._active_action = None
            self._active_task = None
            self._action_page = None
            if page.id in self._pending_page_closes:
                self._pending_page_closes.discard(page.id)
                self._close_page(page.id)

    def _prepare_action(self, page: _Page, kind: str, body: Mapping[str, Any]) -> Any:
        """Validate everything that can fail pre-admission.

        Returns a coroutine function performing the admitted dispatch. No page
        state is mutated here; validation failures become rejections.
        """
        if kind == "close":

            async def run_close(action: _Action, cursor: int) -> dict[str, Any]:
                result = self._finish_action(page, action, "settled", cursor)
                self._close_task = asyncio.create_task(self.close())
                return result

            return run_close
        if kind == "viewer":
            viewer = self._viewer(body.get("viewer_id"))

            async def run_viewer(action: _Action, cursor: int) -> dict[str, Any]:
                self._clear_page_assets(page)
                page.viewer = viewer
                page.generation += 1
                page.target_id = self._initial_target(page.viewer, page.channel_id)
                page.modal = None
                page.modal_handle = None
                page.status = "current"
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page)
                return result

            return run_viewer
        if kind == "focus":
            target = self._target_id(body.get("target_id"), page.viewer)
            if target is None:
                raise SetupError("target message is unavailable")

            async def run_focus(action: _Action, cursor: int) -> dict[str, Any]:
                self._clear_page_assets(page)
                page.target_id = target
                page.generation += 1
                page.modal = None
                page.modal_handle = None
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page)
                return result

            return run_focus
        if kind == "refresh":

            async def run_refresh(action: _Action, cursor: int) -> dict[str, Any]:
                await self.env._settle_internal()
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page)
                return result

            return run_refresh
        actor = page.viewer
        if not can_access_channel(self.env, page.channel_id, actor, history=True):
            raise SetupError("viewer cannot access this channel")
        if kind == "modal_submit":
            modal = page.modal
            if modal is None or body.get("modal_handle") != page.modal_handle:
                raise SetupError("modal is stale or unavailable")
            if modal._interaction.modal_consumed:
                raise SetupError("modal has already been submitted")
            modal_values = self._modal_values(page, body.get("values"), modal)

            async def run_modal(action: _Action, cursor: int) -> dict[str, Any]:
                result = await actor.submit_modal(modal, modal_values)
                page.modal = None
                page.modal_handle = None
                finished = self._finish_action(
                    page, action, "settled", cursor, interaction=result._interaction
                )
                self._publish(page)
                return finished

            return run_modal
        message = self._target_message(page)
        if kind == "click":
            custom_id = body.get("custom_id")
            if not isinstance(custom_id, str):
                raise SetupError("click requires custom_id")
            component = _find_component(
                message.components,
                types=(ComponentType.BUTTON,),
                custom_id=custom_id,
                label=None,
            )
            if component.get("style") in (5, 6) or not component.get("custom_id"):
                raise SetupError("click control is a link or premium button")

            async def run_click(action: _Action, cursor: int) -> dict[str, Any]:
                return await self._run_component_action(
                    page,
                    action,
                    cursor,
                    actor.click(ResponseMessage(self.env, message), custom_id=custom_id),
                )

            return run_click
        custom_id = body.get("custom_id")
        select_values = self._select_values(page, body.get("values"), custom_id)

        async def run_select(action: _Action, cursor: int) -> dict[str, Any]:
            return await self._run_component_action(
                page,
                action,
                cursor,
                actor.select(ResponseMessage(self.env, message), select_values, custom_id=custom_id),
            )

        return run_select

    async def _run_component_action(
        self, page: _Page, action: _Action, cursor: int, awaited: Any
    ) -> dict[str, Any]:
        result = await awaited
        if result.modal is not None:
            if result._interaction.user_id != page.viewer.id:  # pragma: no cover - dispatch invariant
                raise SetupError("modal opener mismatch")
            page.modal = result
            page.modal_handle = "m_" + secrets.token_urlsafe(12)
        finished = self._finish_action(page, action, "settled", cursor, interaction=result._interaction)
        self._publish(page)
        return finished

    def _target_message(self, page: _Page) -> Message:
        if page.target_id is None:
            raise SetupError("no focused message")
        try:
            message = self.env.backend.get_message(page.channel_id, page.target_id)
        except BackendError as exc:
            raise SetupError("focused message is unavailable") from exc
        if not can_access_message(self.env, page.channel_id, message, page.viewer, history=True):
            raise SetupError("focused message is inaccessible")
        return message

    def _select_values(self, page: _Page, values: Any, custom_id: Any) -> list[Any]:
        if not isinstance(values, list):
            raise SetupError("select values must be a list")
        try:
            if len(values) != len(set(values)):
                raise SetupError("select values must be unique")
        except TypeError as exc:
            raise SetupError("select values must be scalar") from exc
        message = self._target_message(page)
        component = _find_component(message.components, types=SELECT_TYPES, custom_id=custom_id, label=None)
        kind = ComponentType(component["type"])
        minimum = component.get("min_values", 1)
        maximum = component.get("max_values", 1)
        if not minimum <= len(values) <= maximum:
            raise SetupError(f"Select expects between {minimum} and {maximum} value(s), got {len(values)}")
        if any(not isinstance(value, str) for value in values):
            raise SetupError("select values must be strings")
        if kind == ComponentType.STRING_SELECT:
            valid = {str(item.get("value")) for item in component.get("options") or []}
            if any(value not in valid for value in values):
                raise SetupError("select option is unavailable")
            return list(values)
        if kind == ComponentType.CHANNEL_SELECT and isinstance(component.get("channel_types"), list):
            allowed = set(component["channel_types"])
            for value in values:  # pragma: no branch - empty selections are validated above
                try:
                    candidate = self.env.backend.channels.get(int(value))
                except (TypeError, ValueError):
                    candidate = None
                if candidate is None or (allowed and candidate.type not in allowed):
                    raise SetupError("select channel is unavailable")
        handles = [self._resolve_entity(page, value, kind) for value in values]
        if any(handle is None for handle in handles):
            raise SetupError("select entity is unavailable")
        return handles  # type: ignore[return-value]

    def _resolve_entity(self, page: _Page, value: str, kind: ComponentType | None = None) -> Any | None:
        try:
            entity_id = int(value)
        except (TypeError, ValueError):
            return None
        channel = self.env.backend.get_channel(page.channel_id)
        if channel.guild_id is None:
            if kind in {ComponentType.USER_SELECT, ComponentType.MENTIONABLE_SELECT}:
                if entity_id in channel.recipient_ids and can_access_channel(
                    self.env, channel.id, page.viewer
                ):
                    return UserHandle(self.env, self.env.backend.get_user(entity_id))
            return None
        guild = self.env.backend.guilds[channel.guild_id]
        if (
            kind in {ComponentType.USER_SELECT, ComponentType.MENTIONABLE_SELECT}
            and entity_id in guild.members
        ):
            return self._member_handle(page, entity_id)
        if (
            kind in {ComponentType.ROLE_SELECT, ComponentType.MENTIONABLE_SELECT}
            and entity_id in guild.roles
            and entity_id != guild.id
        ):
            return RoleHandle(self.env, GuildHandle(self.env, guild), guild.roles[entity_id])
        if kind == ComponentType.CHANNEL_SELECT:
            candidate = self.env.backend.channels.get(entity_id)
            if (
                candidate is not None
                and candidate.guild_id == channel.guild_id
                and can_access_channel(self.env, candidate.id, page.viewer)
            ):
                return ChannelHandle(self.env, GuildHandle(self.env, guild), candidate)
        return None

    def _member_handle(self, page: _Page, user_id: int) -> MemberActor:
        channel = self.env.backend.get_channel(page.channel_id)
        guild = self.env.backend.get_guild(cast(int, channel.guild_id))
        return MemberActor(
            self.env,
            GuildHandle(self.env, guild),
            UserHandle(self.env, self.env.backend.get_user(user_id)),
        )

    def _modal_values(self, page: _Page, values: Any, modal: InteractionResult) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise SetupError("modal values must be an object")
        interaction = modal._interaction
        try:
            spec = validate_modal(interaction.modal)
        except ValueError as exc:
            raise SetupError(str(exc)) from exc
        # The canonical control map rejects duplicate custom_ids up front;
        # walking the raw payload here would silently last-win.
        kinds = {
            custom_id: ComponentType(control["type"])
            for custom_id, control in _modal_control_map(spec).items()
        }
        converted: dict[str, Any] = {}
        entity_types = {
            ComponentType.USER_SELECT,
            ComponentType.ROLE_SELECT,
            ComponentType.CHANNEL_SELECT,
            ComponentType.MENTIONABLE_SELECT,
        }
        for key, value in values.items():
            kind = kinds.get(key)
            if kind is None:
                raise SetupError(f"unknown modal control {key!r}")
            if kind in entity_types:
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise SetupError(f"modal control {key!r} expects entity IDs")
                resolved = [self._resolve_entity(page, item, kind) for item in value]
                if any(item is None for item in resolved):
                    raise SetupError(f"modal entity selection {key!r} is unavailable")
                converted[key] = resolved
            elif kind == ComponentType.FILE_UPLOAD:
                if not isinstance(value, list) or any(
                    not isinstance(item, (list, tuple))
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or not isinstance(item[1], bytes)
                    for item in value
                ):
                    raise SetupError(f"modal file upload {key!r} is invalid")
                converted[key] = [(item[0], item[1]) for item in value]
            else:
                converted[key] = value
        # Run the same per-control validation submit_modal applies at dispatch
        # (required presence, bounds, option membership) so violations reject at
        # admission instead of failing mid-dispatch with a consumed sequence.
        _modal_submit_nodes(
            spec.get("components") or [], page.viewer, converted, {}, interaction.channel_id, []
        )
        return converted

    def _finish_action(
        self,
        page: _Page,
        action: _Action,
        settlement: str,
        cursor: int,
        error: BaseException | None = None,
        *,
        interaction: Interaction | None = None,
    ) -> dict[str, Any]:
        interaction = interaction or action.interaction
        diagnostics = [
            {"type": type(item).__name__, "message": str(item)} for item in self.env.errors_since(cursor)
        ]
        if error is not None:
            diagnostics.append({"type": type(error).__name__, "message": str(error)})
        dispatch = "dispatched" if interaction is not None else "not_dispatched"
        ack = "pending"
        if interaction is not None:
            ack = "acknowledged" if interaction.responded else "unacknowledged"
            if interaction.deferred:
                ack = "deferred"
        page.last_action = {
            "requestId": action.request_id,
            "sequence": action.sequence,
            "dispatch": dispatch,
            "acknowledgement": ack,
            "settlement": settlement,
        }
        return self._result(
            page,
            request_id=action.request_id,
            sequence=action.sequence,
            rejected=False,
            dispatch=dispatch,
            acknowledgement=ack,
            settlement=settlement,
            diagnostics=diagnostics,
        )


__all__ = ["_Action", "_ActionOps"]
