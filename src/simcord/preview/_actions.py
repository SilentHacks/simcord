"""Browser action admission, sequencing, dispatch, and settlement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, cast

from .._modal import _modal_control_map, _modal_submit_nodes
from ..actors import MemberActor
from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..builders import ChannelHandle, GuildHandle, RoleHandle, UserHandle
from ..components import validate_modal
from ..enums import SELECT_TYPES, ComponentType, OptionType
from ..interactions import OptionError, parse_option_input, validate_option_value
from ._action_plans import (
    _find_scoped_component,
    _prepare_command_action,
    _prepare_component_action,
    _prepare_message_action,
    _prepare_modal_action,
    _prepare_page_action,
    _prepare_send_action,
)
from ._diagnostics import make_diagnostic

if TYPE_CHECKING:
    from ..backend.models import Interaction, Message
    from ..env import Env
    from ..results import InteractionResult
    from . import Preview
    from ._pages import _Page

_ACTIVITY_LIMIT = 20


@dataclass(frozen=True)
class ActionSpec:
    revision_bound: bool = False
    mutating: bool = False
    requires_target: bool = False
    records_target: bool = False
    page_intent: bool = False


ACTION_SPECS: Mapping[str, ActionSpec] = MappingProxyType(
    {
        "click": ActionSpec(revision_bound=True, mutating=True, requires_target=True, records_target=True),
        "select": ActionSpec(revision_bound=True, mutating=True, requires_target=True, records_target=True),
        "modal_submit": ActionSpec(revision_bound=True, mutating=True),
        "autocomplete_command": ActionSpec(revision_bound=True),
        "run_command": ActionSpec(revision_bound=True, mutating=True),
        "viewer": ActionSpec(page_intent=True),
        "focus": ActionSpec(records_target=True, page_intent=True),
        "history": ActionSpec(revision_bound=True, mutating=True),
        "send_message": ActionSpec(revision_bound=True, mutating=True),
        "edit_message": ActionSpec(
            revision_bound=True, mutating=True, requires_target=True, records_target=True
        ),
        "delete_message": ActionSpec(
            revision_bound=True, mutating=True, requires_target=True, records_target=True
        ),
        "set_reaction": ActionSpec(
            revision_bound=True, mutating=True, requires_target=True, records_target=True
        ),
        "set_poll_votes": ActionSpec(
            revision_bound=True, mutating=True, requires_target=True, records_target=True
        ),
        "set_pinned": ActionSpec(
            revision_bound=True, mutating=True, requires_target=True, records_target=True
        ),
        "refresh": ActionSpec(page_intent=True),
        "close": ActionSpec(page_intent=True),
        "browse_messages": ActionSpec(revision_bound=True),
        "browse_candidates": ActionSpec(revision_bound=True),
        "configure_presentation": ActionSpec(revision_bound=True, page_intent=True),
    }
)


@dataclass(slots=True)
class _Action:
    sequence: int
    request_id: str
    fingerprint: str
    kind: str
    response: dict[str, Any] | None = None
    interaction: Interaction | None = None
    correlation: str = field(default_factory=lambda: "c_" + secrets.token_urlsafe(12))
    target: dict[str, str | None] | None = None
    command: dict[str, str] | None = None
    autocomplete_focused: str | None = None
    autocomplete_answered: bool | None = None
    record_activity: bool = True
    dispatched: bool = False
    uncertain: bool = False
    outcomes: list[dict[str, Any]] = field(default_factory=list)


class _ActionOps:
    """Ordered action admission: validate first, consume the sequence, settle."""

    env: Env
    channel: ChannelHandle
    layout: str
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
    _redact_page_receipts: Callable[[_Page], None]
    _publish: Callable[..., None]
    _pages: dict[str, _Page]
    _receipt_payload: Callable[..., Any]
    _advance_presentation_time: Callable[[], None]

    _MUTATING_KINDS: ClassVar[frozenset[str]] = frozenset(
        kind for kind, spec in ACTION_SPECS.items() if spec.mutating
    )
    _REVISION_KINDS: ClassVar[frozenset[str]] = frozenset(
        kind for kind, spec in ACTION_SPECS.items() if spec.revision_bound
    )
    _ACTION_KINDS: ClassVar[frozenset[str]] = frozenset(ACTION_SPECS)

    def _on_dispatch(self, interaction: Interaction) -> None:
        task = asyncio.current_task()
        if self._active_action is not None and task is self._active_task:
            self._active_action.interaction = interaction
            self._active_action.dispatched = True

    def _mark_action_dispatched(self) -> None:
        if self._active_action is not None and asyncio.current_task() is self._active_task:
            self._active_action.dispatched = True

    def _publish_message_pages(self, *, reason: str = "action") -> None:
        for page in tuple(self._pages.values()):
            if page.id in self._pages:
                self._publish(page, reason=reason)

    def _require_current_history(self, page: _Page) -> None:
        if page.status != "current" or not can_access_channel(
            self.env, page.channel_id, page.viewer, history=True
        ):
            raise SetupError("viewer cannot access current channel history")

    def _finish_message_edit(
        self, page: _Page, action: _Action, cursor: int, message: Message
    ) -> dict[str, Any]:
        action.outcomes = [{"kind": "source_edit", "messageId": str(message.id)}]
        result = self._finish_action(page, action, "settled", cursor)
        self._publish_message_pages()
        return result

    @staticmethod
    def _reset_queries(page: _Page) -> None:
        page.navigation_query = ""
        page.navigation_cursor = None
        page.candidate_queries.clear()

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
        target: dict[str, str | None] | None = None,
        uncertain: bool = False,
        correlation: str | None = None,
        outcomes: list[dict[str, Any]] | None = None,
        command: dict[str, str] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
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
            "target": target,
            "command": command,
            "uncertain": uncertain,
            "correlation": correlation or "c_" + secrets.token_urlsafe(12),
            "outcomes": outcomes or [],
        }
        if result is not None:
            payload["result"] = result
        return payload

    @staticmethod
    def _append_activity(page: _Page, receipt: dict[str, Any], action: _Action | None) -> None:
        page.activity.append({key: value for key, value in receipt.items() if key != "result"})
        page.activity_actions.append(action)
        if len(page.activity) > _ACTIVITY_LIMIT:
            del page.activity[:-_ACTIVITY_LIMIT]
            del page.activity_actions[:-_ACTIVITY_LIMIT]

    def _reject(
        self,
        page: _Page,
        code: str,
        *,
        request_id: Any = None,
        sequence: Any = None,
        subject: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """A pre-admission rejection: never consumes the per-page sequence."""
        correlation = "c_" + secrets.token_urlsafe(12)
        result = self._result(
            page,
            request_id=request_id,
            sequence=sequence,
            rejected=True,
            dispatch="not_dispatched",
            acknowledgement="pending",
            settlement="rejected",
            diagnostics=[make_diagnostic(code, correlation=correlation, subject=subject)],
            correlation=correlation,
        )
        page.last_action = result
        self._append_activity(page, result, None)
        return result

    def _replay_response(self, page: _Page, action: _Action) -> dict[str, Any]:
        response = json.loads(json.dumps(action.response or {}))
        allowed = can_access_channel(self.env, page.channel_id, page.viewer, history=True)
        response.update(self._receipt_payload(page, response, allowed=allowed))
        result = response.get("result")
        if not isinstance(result, Mapping):
            return response
        if not allowed:
            response.pop("result", None)
        elif action.kind == "browse_messages":
            result = dict(result)
            rows = []
            for summary in result.get("messageIndex", []):
                if not isinstance(summary, Mapping):
                    continue
                message_id = summary.get("id")
                if not isinstance(message_id, str):
                    continue
                try:
                    message = self.env.backend.get_message(page.channel_id, int(message_id))
                except (BackendError, TypeError, ValueError):
                    continue
                if can_access_message(self.env, page.channel_id, message, page.viewer, history=True):
                    rows.append(dict(summary))
            result["messageIndex"] = rows
            response["result"] = result
        elif action.kind == "browse_candidates":
            from ._queries import _candidate_descriptor, candidate_control

            control_key = result.get("control_key")
            state = page.candidate_queries.get(control_key) if isinstance(control_key, str) else None
            if state is None or not isinstance(control_key, str):
                response.pop("result", None)
            else:
                try:
                    component, modal_handle = candidate_control(
                        cast("Preview", self), page, control_key, state.get("modal_handle")
                    )
                except SetupError:
                    response.pop("result", None)
                else:
                    response["result"] = {
                        "control_key": control_key,
                        "candidate": _candidate_descriptor(
                            cast("Preview", self),
                            page,
                            component,
                            control_key,
                            modal_handle=modal_handle,
                        ),
                    }
        return response

    async def _action(self, context_id: str | None, body: Mapping[str, Any]) -> dict[str, Any]:
        page = self._get_page(context_id)
        if not isinstance(body, Mapping):
            return self._reject(page, "bad-envelope")
        if body.get("protocol_version") != 3:
            return self._reject(page, "unsupported-protocol")
        sequence = body.get("sequence")
        request_id = body.get("request_id")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            return self._reject(page, "bad-envelope", request_id=request_id)
        if not isinstance(request_id, str) or not request_id:
            return self._reject(page, "bad-envelope", sequence=sequence)
        generation = body.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            return self._reject(page, "bad-envelope", request_id=request_id, sequence=sequence)
        bot_generation = body.get("bot_generation")
        if not isinstance(bot_generation, int) or isinstance(bot_generation, bool) or bot_generation < 1:
            return self._reject(page, "bad-envelope", request_id=request_id, sequence=sequence)
        fingerprint = hashlib.sha256(
            json.dumps(dict(body), sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        latest = page.latest_action
        if sequence <= page.last_sequence:
            if sequence == page.last_sequence and latest is not None and latest.request_id == request_id:
                if latest.fingerprint != fingerprint:
                    return self._reject(page, "conflicting-request", request_id=request_id, sequence=sequence)
                if latest.response is None:
                    return self._result(
                        page,
                        request_id=latest.request_id,
                        sequence=latest.sequence,
                        rejected=False,
                        dispatch="dispatched" if latest.dispatched else "pending",
                        acknowledgement="pending",
                        settlement="pending",
                        diagnostics=[],
                        target=latest.target,
                        uncertain=latest.uncertain,
                        correlation=latest.correlation,
                        outcomes=latest.outcomes,
                        command=latest.command,
                    )
                return self._replay_response(page, latest)
            return self._reject(page, "stale-sequence", request_id=request_id, sequence=sequence)
        if sequence != page.last_sequence + 1:
            return self._reject(page, "sequence-gap", request_id=request_id, sequence=sequence)
        if self._active_action is not None:
            return self._reject(page, "busy", request_id=request_id, sequence=sequence)
        if generation != page.generation:
            return self._reject(page, "stale-context", request_id=request_id, sequence=sequence)
        if bot_generation != self.env._generation:
            return self._reject(page, "stale-generation", request_id=request_id, sequence=sequence)
        kind = body.get("kind")
        if not isinstance(kind, str) or kind not in self._ACTION_KINDS:
            return self._reject(page, "unknown-kind", request_id=request_id, sequence=sequence)
        spec = ACTION_SPECS[kind]
        if spec.revision_bound:
            published_revision = body.get("published_revision")
            if (
                isinstance(published_revision, bool)
                or not isinstance(published_revision, int)
                or published_revision != page.revision
            ):
                return self._reject(page, "stale-revision", request_id=request_id, sequence=sequence)
        if spec.requires_target and (
            isinstance(body.get("target_id"), bool) or not isinstance(body.get("target_id"), (str, int))
        ):
            return self._reject(page, "target-unavailable", request_id=request_id, sequence=sequence)
        self.env._begin_operation("preview.action")
        try:
            # Kind, control resolution, and values are validated before the
            # sequence is consumed; only a validated plan may be admitted.
            try:
                plan = self._prepare_action(page, kind, body)
            except (SetupError, BackendError, ValueError) as exc:
                from ._queries import _QueryError

                code = (
                    exc.code
                    if isinstance(exc, _QueryError)
                    else "command-option-invalid"
                    if kind == "run_command" and isinstance(exc, OptionError)
                    else "validation-failed"
                )
                subject = (
                    {"commandOption": exc.option}
                    if kind == "run_command" and isinstance(exc, OptionError) and exc.option is not None
                    else None
                )
                return self._reject(
                    page,
                    code,
                    request_id=request_id,
                    sequence=sequence,
                    subject=subject,
                )
            action = _Action(sequence, request_id, fingerprint, kind)
            if kind in {"autocomplete_command", "run_command"}:
                path = cast(list[str], body["path"])
                action.command = {
                    "commandId": cast(str, body["command_id"]),
                    "invocation": " ".join(path),
                }
                if kind == "autocomplete_command":
                    action.autocomplete_focused = cast(str, body["focused"])
            if spec.records_target and isinstance(body.get("target_id"), (str, int)):
                target_id = self._target_id(body.get("target_id"), page.viewer)
                if target_id is not None:
                    control_key = body.get("control_key")
                    action.target = {
                        "messageId": str(target_id),
                        "controlKey": control_key if isinstance(control_key, str) else None,
                    }
            if kind == "viewer":
                # Redact the previous latest action before admission replaces it.
                self._redact_page_receipts(page)
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
            self.env._end_operation()
            self._active_action = None
            self._active_task = None
            self._action_page = None
            if page.id in self._pending_page_closes:
                self._pending_page_closes.discard(page.id)
                self._close_page(page.id)

    def _prepare_action(self, page: _Page, kind: str, body: Mapping[str, Any]) -> Any:
        """Validate before admission; return a lazy dispatch coroutine function."""
        if kind in {
            "configure_presentation",
            "browse_messages",
            "browse_candidates",
            "close",
            "viewer",
            "focus",
            "refresh",
            "history",
        }:
            return _prepare_page_action(cast("Preview", self), page, kind, body)
        if kind == "send_message":
            return _prepare_send_action(cast("Preview", self), page, body)
        actor = page.viewer
        if kind in {"autocomplete_command", "run_command"}:
            return _prepare_command_action(cast("Preview", self), page, kind, body, actor)
        self._require_current_history(page)
        if kind == "modal_submit":
            return _prepare_modal_action(cast("Preview", self), page, body, actor)
        target_id = self._target_id(body.get("target_id"), page.viewer)
        if target_id is None:
            raise SetupError("authorized target is unavailable")
        message = self._target_message(page, target_id)
        if kind in {"edit_message", "delete_message", "set_reaction", "set_poll_votes", "set_pinned"}:
            return _prepare_message_action(cast("Preview", self), page, kind, body, actor, message)
        return _prepare_component_action(cast("Preview", self), page, kind, body, actor, message)

    async def _run_interaction_action(
        self,
        page: _Page,
        action: _Action,
        cursor: int,
        awaited: Any,
        *,
        republish_all: bool = False,
    ) -> dict[str, Any]:
        result = await awaited
        if result.modal is not None:
            if result._interaction.user_id != page.viewer.id:  # pragma: no cover - dispatch invariant
                raise SetupError("modal opener mismatch")
            page.modal = result
            page.modal_handle = "m_" + secrets.token_urlsafe(12)
        finished = self._finish_action(page, action, "settled", cursor, interaction=result._interaction)
        if republish_all:
            self._publish_message_pages()
            if page not in self._pages.values():
                self._publish(page)
        else:
            self._publish(page)
        return finished

    def _target_message(self, page: _Page, target_id: int | None = None) -> Message:
        target = page.target_id if target_id is None else target_id
        if target is None:
            raise SetupError("authorized target is unavailable")
        try:
            message = self.env.backend.get_message(page.channel_id, target)
        except BackendError as exc:
            raise SetupError("authorized target is unavailable") from exc
        if not can_access_message(self.env, page.channel_id, message, page.viewer, history=True):
            raise SetupError("authorized target is unavailable")
        return message

    def _command_option_value(self, page: _Page, command_name: str, option: dict[str, Any], raw: Any) -> Any:
        option_type = OptionType(option["type"])
        if option_type in {
            OptionType.USER,
            OptionType.ROLE,
            OptionType.CHANNEL,
            OptionType.MENTIONABLE,
        }:
            if not isinstance(raw, str):
                raise OptionError("option-type", option["name"], "expects an entity ID string")
            component_type = {
                OptionType.USER: ComponentType.USER_SELECT,
                OptionType.ROLE: ComponentType.ROLE_SELECT,
                OptionType.MENTIONABLE: ComponentType.MENTIONABLE_SELECT,
                OptionType.CHANNEL: ComponentType.CHANNEL_SELECT,
            }[option_type]
            component: dict[str, Any] = {
                "type": int(component_type),
                "min_values": 1,
                "max_values": 1,
            }
            if option_type == OptionType.CHANNEL and option.get("channel_types"):
                component["channel_types"] = list(option["channel_types"])
            try:
                values = self._select_values(
                    page,
                    None,
                    [raw],
                    None,
                    component_override=component,
                )
            except (SetupError, BackendError, ValueError):
                raise OptionError(
                    "option-entity", option["name"], "does not reference an authorized entity"
                ) from None
            return validate_option_value(command_name, option, values[0])
        if option_type == OptionType.ATTACHMENT:
            if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], bytes):
                if len(raw[1]) > 10 * 1024 * 1024:
                    raise OptionError("option-type", option["name"], "exceeds the 10 MiB per-file limit")
            return validate_option_value(command_name, option, raw)
        return parse_option_input(command_name, option, raw)

    def _select_values(
        self,
        page: _Page,
        message: Message | None,
        values: Any,
        control_key: Any,
        *,
        component_override: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        if not isinstance(values, list):
            raise SetupError("select values must be a list")
        try:
            if len(values) != len(set(values)):
                raise SetupError("select values must be unique")
        except TypeError as exc:
            raise SetupError("select values must be scalar") from exc
        if component_override is not None:
            component = dict(component_override)
        elif message is not None:
            component = _find_scoped_component(
                message,
                control_key,
                types=tuple(int(item) for item in SELECT_TYPES),
            )
        else:
            raise SetupError("select control is unavailable")
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
            for value in values:
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
        total_upload_bytes = 0
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
                for _, blob in value:
                    if len(blob) > 10 * 1024 * 1024:
                        raise SetupError(f"modal upload {key!r} exceeds the 10 MiB per-file limit")
                    total_upload_bytes += len(blob)
                    if total_upload_bytes > 25 * 1024 * 1024:
                        raise SetupError("modal uploads exceed the 25 MiB aggregate limit")
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

    @staticmethod
    def _outcomes(action: _Action, interaction: Interaction | None) -> list[dict[str, Any]]:
        if interaction is None:
            return list(action.outcomes)
        response_kind = getattr(interaction.response_kind, "value", interaction.response_kind)
        if response_kind == "message":
            outcomes = (
                [{"kind": "response", "messageId": str(interaction.message_id)}]
                if interaction.message_id is not None
                else [{"kind": "no_output"}]
            )
        elif response_kind in {"deferred", "deferred_update"}:
            outcomes = [{"kind": "deferred"}]
        elif response_kind == "update":
            outcomes = (
                [{"kind": "source_edit", "messageId": str(interaction.message_id)}]
                if interaction.message_id is not None
                else [{"kind": "no_output"}]
            )
        elif response_kind == "modal":
            outcomes = [{"kind": "modal"}]
        else:
            outcomes = [{"kind": "no_output"}]
        outcomes.extend({"kind": "followup", "messageId": str(value)} for value in interaction.followup_ids)
        return outcomes

    def _refresh_action_receipts(self, page: _Page) -> None:
        latest = page.latest_action
        for index, action in enumerate(page.activity_actions):
            if action is None or index >= len(page.activity):
                continue
            outcomes = self._outcomes(action, action.interaction)
            page.activity[index]["outcomes"] = outcomes
            if action.response is not None:
                action.response["outcomes"] = outcomes
        if latest is not None and latest.response is not None:
            latest.response["outcomes"] = self._outcomes(latest, latest.interaction)
            if (
                isinstance(page.last_action, dict)
                and page.last_action.get("correlation") == latest.correlation
            ):
                page.last_action["outcomes"] = latest.response["outcomes"]

    def _finish_action(
        self,
        page: _Page,
        action: _Action,
        settlement: str,
        cursor: int,
        error: BaseException | None = None,
        *,
        interaction: Interaction | None = None,
        extra_diagnostics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        interaction = interaction or action.interaction
        errors = self.env.errors_since(cursor)
        diagnostics = []
        if errors or error is not None:
            diagnostics.append(make_diagnostic("action-callback-error", correlation=action.correlation))
            if settlement == "settled":
                settlement = "failed"
        if settlement == "timeout":
            diagnostics.append(make_diagnostic("action-timeout", correlation=action.correlation))
        elif settlement == "cancelled":
            diagnostics.append(make_diagnostic("action-cancelled", correlation=action.correlation))
        diagnostics.extend(extra_diagnostics or [])
        if action.kind == "autocomplete_command":
            if action.autocomplete_answered is False or errors or error is not None:
                if not any(item.get("code") == "autocomplete-unanswered" for item in diagnostics):
                    diagnostics.append(
                        make_diagnostic("autocomplete-unanswered", correlation=action.correlation)
                    )
            if errors or error is not None:
                action.record_activity = True
        action.dispatched = action.dispatched or interaction is not None
        dispatch = "dispatched" if action.dispatched else "not_dispatched"
        ack = (
            "not_applicable"
            if action.kind not in {"click", "select", "modal_submit"} and interaction is None
            else "pending"
        )
        if interaction is not None:
            ack = "acknowledged" if interaction.responded else "unacknowledged"
            if interaction.deferred:
                ack = "deferred"
        action.interaction = interaction
        action.uncertain = settlement in {"failed", "timeout", "cancelled"} and dispatch == "dispatched"
        action.outcomes = self._outcomes(action, interaction)
        result = self._result(
            page,
            request_id=action.request_id,
            sequence=action.sequence,
            rejected=False,
            dispatch=dispatch,
            acknowledgement=ack,
            settlement=settlement,
            diagnostics=diagnostics,
            target=action.target,
            uncertain=action.uncertain,
            correlation=action.correlation,
            outcomes=action.outcomes,
            command=action.command,
        )
        if action.kind == "autocomplete_command":
            result["result"] = {
                "command": dict(action.command or {}),
                "focused": action.autocomplete_focused or "",
                "answered": action.autocomplete_answered is True,
                "choices": [],
            }
        page.last_action = result
        action.response = result
        if action.record_activity:
            self._append_activity(page, result, action)
        page.pending_receipt_revision = settlement == "settled" and action.kind not in {
            "close",
            "autocomplete_command",
        }
        return result


__all__ = ["_Action", "_ActionOps"]
