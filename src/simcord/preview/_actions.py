"""Browser action admission, sequencing, dispatch, and settlement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from bisect import bisect_right
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from ..actors import MemberActor, _modal_control_map, _modal_submit_nodes
from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..builders import ChannelHandle, GuildHandle, RoleHandle, UserHandle
from ..components import validate_modal
from ..enums import SELECT_TYPES, ComponentType
from ..results import ResponseMessage
from ._diagnostics import make_diagnostic

if TYPE_CHECKING:
    from ..backend.models import Interaction, Message
    from ..env import Env
    from ..results import InteractionResult
    from . import Preview
    from ._pages import _Page


def _component_key(component: Mapping[str, Any], path: str, message_id: int) -> str | None:
    if component.get("type") not in {
        int(ComponentType.BUTTON),
        int(ComponentType.STRING_SELECT),
        int(ComponentType.USER_SELECT),
        int(ComponentType.ROLE_SELECT),
        int(ComponentType.MENTIONABLE_SELECT),
        int(ComponentType.CHANNEL_SELECT),
    }:
        return None
    wire_id = component.get("id")
    identity = (
        str(wire_id) if isinstance(wire_id, int) and not isinstance(wire_id, bool) and wire_id > 0 else path
    )
    return f"message:{message_id}:component:{identity}"


def _component_paths(value: Any, path: str = "0") -> Any:
    if not isinstance(value, dict):
        return
    yield path, value
    children = value.get("components")
    if isinstance(children, list):
        for index, child in enumerate(children):
            yield from _component_paths(child, f"{path}.components.{index}")
    for child_name in ("accessory", "component"):
        child = value.get(child_name)
        if isinstance(child, dict):
            yield from _component_paths(child, f"{path}.{child_name}")


def _find_scoped_component(
    message: Message,
    control_key: Any,
    *,
    types: tuple[int, ...],
) -> dict[str, Any]:
    if not isinstance(control_key, str):
        raise SetupError("control_key is required")
    for root_index, root in enumerate(message.components):
        for path, component in _component_paths(root, str(root_index)):
            if component.get("type") not in types:
                continue
            if _component_key(component, path, message.id) == control_key:
                if component.get("disabled"):
                    raise SetupError("control is unavailable")
                return component
    raise SetupError("control is unavailable")


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
        {
            "click",
            "select",
            "modal_submit",
            "history",
            "send_message",
            "edit_message",
            "delete_message",
            "set_reaction",
            "set_poll_votes",
            "set_pinned",
        }
    )
    _REVISION_KINDS: ClassVar[frozenset[str]] = _MUTATING_KINDS | frozenset(
        {"browse_messages", "browse_candidates", "configure_presentation"}
    )
    _ACTION_KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "click",
            "select",
            "modal_submit",
            "viewer",
            "focus",
            "history",
            "send_message",
            "edit_message",
            "delete_message",
            "set_reaction",
            "set_poll_votes",
            "set_pinned",
            "refresh",
            "close",
            "browse_messages",
            "browse_candidates",
            "configure_presentation",
        }
    )

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
    ) -> dict[str, Any]:
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
            "target": target,
            "uncertain": uncertain,
            "correlation": correlation or "c_" + secrets.token_urlsafe(12),
            "outcomes": outcomes or [],
        }

    @staticmethod
    def _append_activity(page: _Page, receipt: dict[str, Any], action: _Action | None) -> None:
        page.activity.append({key: value for key, value in receipt.items() if key != "result"})
        page.activity_actions.append(action)
        if len(page.activity) > 20:
            del page.activity[:-20]
            del page.activity_actions[:-20]

    def _reject(
        self,
        page: _Page,
        code: str,
        *,
        request_id: Any = None,
        sequence: Any = None,
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
            diagnostics=[make_diagnostic(code, correlation=correlation)],
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
            from ._snapshot import _candidate_descriptor, candidate_control

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
        if kind in self._REVISION_KINDS:
            published_revision = body.get("published_revision")
            if (
                isinstance(published_revision, bool)
                or not isinstance(published_revision, int)
                or published_revision != page.revision
            ):
                return self._reject(page, "stale-revision", request_id=request_id, sequence=sequence)
        if kind in {
            "click",
            "select",
            "edit_message",
            "delete_message",
            "set_reaction",
            "set_poll_votes",
            "set_pinned",
        } and (isinstance(body.get("target_id"), bool) or not isinstance(body.get("target_id"), (str, int))):
            return self._reject(page, "target-unavailable", request_id=request_id, sequence=sequence)
        token = self.env._begin_operation("preview.action")
        try:
            # Kind, control resolution, and values are validated before the
            # sequence is consumed; only a validated plan may be admitted.
            try:
                plan = self._prepare_action(page, kind, body)
            except (SetupError, BackendError, ValueError) as exc:
                from ._snapshot import _QueryError

                return self._reject(
                    page,
                    exc.code if isinstance(exc, _QueryError) else "validation-failed",
                    request_id=request_id,
                    sequence=sequence,
                )
            action = _Action(sequence, request_id, fingerprint, kind)
            if kind in {
                "click",
                "select",
                "focus",
                "edit_message",
                "delete_message",
                "set_reaction",
                "set_poll_votes",
                "set_pinned",
            } and isinstance(body.get("target_id"), (str, int)):
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
        if kind == "configure_presentation":
            layout, display = body.get("layout"), body.get("display")
            if (
                not isinstance(layout, str)
                or layout not in {"message", "channel"}
                or not isinstance(display, str)
                or display not in {"responsive", "fixed"}
            ):
                raise SetupError("presentation configuration is unavailable")
            from ._capture import ManagedCapture

            width, height = ManagedCapture._validate_dimensions(body.get("width"), body.get("height"))
            host_width, host_height = ManagedCapture._validate_dimensions(
                body.get("host_width"), body.get("host_height")
            )

            async def run_presentation(action: _Action, cursor: int) -> dict[str, Any]:
                if page.layout != layout:
                    page.generation += 1
                    page.navigation_cursor = None
                    page.candidate_queries.clear()
                    if layout == "channel":
                        page.window_end_id = page.target_id
                page.layout = layout
                page.display = display
                page.width, page.height = width, height
                page.host_width, page.host_height = host_width, host_height
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page, reason="presentation")
                result["result"] = {"presentation": page.snapshot["presentation"]}
                return result

            return run_presentation
        if kind == "browse_messages":
            from ._snapshot import validate_message_query

            if page.status != "current" or not can_access_channel(
                self.env, page.channel_id, page.viewer, history=True
            ):
                raise SetupError("viewer cannot access current channel history")
            query, cursor_value = validate_message_query(
                page, body.get("query"), body.get("filter"), body.get("cursor")
            )

            async def run_browse_messages(action: _Action, cursor: int) -> dict[str, Any]:
                page.navigation_query = query
                page.navigation_cursor = cursor_value
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page, reason="query")
                result["result"] = {
                    "navigation": page.snapshot["navigation"],
                    "messageIndex": page.snapshot["messageIndex"],
                }
                return result

            return run_browse_messages
        if kind == "browse_candidates":
            from ._snapshot import validate_candidate_query

            if page.status != "current" or not can_access_channel(
                self.env, page.channel_id, page.viewer, history=True
            ):
                raise SetupError("viewer cannot access current channel history")
            control_key = body.get("control_key")
            query, cursor_value, modal_handle = validate_candidate_query(
                cast("Preview", self),
                page,
                control_key,
                body.get("modal_handle"),
                body.get("query"),
                body.get("cursor"),
            )
            control_key = cast(str, control_key)
            selected_values = body.get("selected_values")
            if selected_values is not None and (
                not isinstance(selected_values, list)
                or len(selected_values) > 25
                or any(
                    not isinstance(value, str)
                    or not value.isascii()
                    or not value.isdecimal()
                    or len(value) > 20
                    for value in selected_values
                )
                or len(set(selected_values)) != len(selected_values)
            ):
                from ._snapshot import _QueryError

                raise _QueryError("query-invalid")

            async def run_browse_candidates(action: _Action, cursor: int) -> dict[str, Any]:
                page.candidate_queries[control_key] = {
                    "query": query,
                    "cursor": cursor_value,
                    "modal_handle": modal_handle,
                    "selected_values": selected_values,
                }
                if modal_handle is None:
                    target_id = control_key.split(":", 2)[1]
                    action.target = {"messageId": target_id, "controlKey": control_key}
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page, reason="query")
                result["result"] = {
                    "control_key": control_key,
                    "candidate": page.snapshot["candidates"].get(control_key),
                }
                return result

            return run_browse_candidates
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
                self._reset_queries(page)
                page.target_id = self._initial_target(page.viewer, page.channel_id)
                page.window_end_id = page.target_id if page.layout == "channel" else page.window_end_id
                page.modal = None
                page.modal_handle = None
                page.status = "current"
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page, reason="navigation")
                return result

            return run_viewer
        if kind == "focus":
            target = self._target_id(body.get("target_id"), page.viewer)
            if target is None:
                raise SetupError("target message is unavailable")

            async def run_focus(action: _Action, cursor: int) -> dict[str, Any]:
                self._clear_page_assets(page)
                page.target_id = target
                page.window_end_id = target if page.layout == "channel" else page.window_end_id
                page.generation += 1
                page.navigation_cursor = None
                page.candidate_queries.clear()
                page.modal = None
                page.modal_handle = None
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page, reason="navigation")
                return result

            return run_focus
        if kind == "refresh":

            async def run_refresh(action: _Action, cursor: int) -> dict[str, Any]:
                await self.env._settle_internal()
                self._advance_presentation_time()
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page, reason="refresh")
                return result

            return run_refresh
        if kind == "history":
            if page.layout != "channel":
                raise SetupError("history navigation requires channel layout")
            if page.status != "current" or not can_access_channel(
                self.env, page.channel_id, page.viewer, history=True
            ):
                raise SetupError("viewer cannot access current channel history")
            direction = body.get("direction")
            if not isinstance(direction, str) or direction not in {"older", "newer", "latest"}:
                raise SetupError("history direction is unavailable")
            visible = [
                item
                for item in sorted(
                    self.env.backend.messages.get(page.channel_id, {}).values(), key=lambda item: item.id
                )
                if can_access_message(self.env, page.channel_id, item, page.viewer, history=True)
            ]
            ids = [item.id for item in visible]
            end = len(ids) if page.window_end_id is None else bisect_right(ids, page.window_end_id)
            start = max(0, end - 50)
            if direction == "older":
                if start == 0:
                    raise SetupError("there is no earlier authorized history")
                next_end = min(end, start + 25)
                next_anchor = ids[next_end - 1]
            elif direction == "newer":
                if end == len(ids):
                    raise SetupError("there is no newer authorized history")
                next_start = min(len(ids) - 1, start + 25)
                next_end = min(len(ids), next_start + 50)
                next_anchor = None if next_end == len(ids) else ids[next_end - 1]
            else:
                next_anchor = None

            async def run_history(action: _Action, cursor: int) -> dict[str, Any]:
                page.window_end_id = next_anchor
                page.target_id = None
                page.generation += 1
                page.navigation_cursor = None
                page.candidate_queries.clear()
                result = self._finish_action(page, action, "settled", cursor)
                self._publish(page, reason="navigation")
                return result

            return run_history
        if kind == "send_message":
            actor = page.viewer
            if (
                page.layout != "channel"
                or page.status != "current"
                or not can_access_channel(self.env, page.channel_id, actor, history=True)
            ):
                raise SetupError("sending requires current channel access and channel layout")
            content = body.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 2000:
                raise SetupError("message content must contain 1 to 2000 characters")
            reply_to = None
            reply_id = body.get("reply_to_id")
            if reply_id is not None:
                if isinstance(reply_id, bool) or not isinstance(reply_id, (str, int)):
                    raise SetupError("reply target is unavailable")
                reply_id = self._target_id(reply_id, page.viewer)
                if reply_id is None:
                    raise SetupError("reply target is unavailable")
                reply_to = self._target_message(page, reply_id)
            if not isinstance(actor, (MemberActor, UserHandle)) or (
                isinstance(actor, UserHandle) and actor.dm_channel.id != page.channel_id
            ):
                raise SetupError("viewer cannot send to this channel")

            async def run_send(action: _Action, cursor: int) -> dict[str, Any]:
                if isinstance(actor, MemberActor):
                    response = await actor.send(
                        self.channel,
                        content,
                        reply_to=ResponseMessage(self.env, reply_to) if reply_to is not None else None,
                    )
                else:
                    reference = (
                        {"channel_id": str(page.channel_id), "message_id": str(reply_to.id)}
                        if reply_to is not None
                        else None
                    )
                    response = (
                        await actor.send_dm(content, reference=reference)
                        if reference
                        else await actor.send_dm(content)
                    )
                try:
                    self.env.backend.get_message(page.channel_id, response.id)
                except BackendError as exc:
                    raise SetupError("message was not accepted by the channel") from exc
                action.target = {"messageId": str(response.id), "controlKey": None}
                action.outcomes = [{"kind": "message", "messageId": str(response.id)}]
                result = self._finish_action(page, action, "settled", cursor)
                self._publish_message_pages()
                return result

            return run_send
        actor = page.viewer
        if page.status != "current" or not can_access_channel(self.env, page.channel_id, actor, history=True):
            raise SetupError("viewer cannot access current channel history")
        if kind == "modal_submit":
            modal = page.modal
            if modal is None or body.get("modal_handle") != page.modal_handle:
                raise SetupError("modal is stale or unavailable")
            if modal._interaction.modal_consumed:
                raise SetupError("modal has already been submitted")
            modal_values = self._modal_values(page, body.get("values"), modal)

            async def run_modal(action: _Action, cursor: int) -> dict[str, Any]:
                admitted = next(
                    (
                        prior
                        for prior in reversed(page.activity_actions)
                        if prior is not None and prior.interaction is modal._interaction
                    ),
                    None,
                )
                if admitted is not None and admitted.target is not None:
                    action.target = dict(admitted.target)
                result = await actor.submit_modal(modal, modal_values)
                page.modal = None
                page.modal_handle = None
                finished = self._finish_action(
                    page, action, "settled", cursor, interaction=result._interaction
                )
                self._publish(page)
                return finished

            return run_modal
        target_id = self._target_id(body.get("target_id"), page.viewer)
        if target_id is None:
            raise SetupError("authorized target is unavailable")
        message = self._target_message(page, target_id)
        if kind == "edit_message":
            content = body.get("content")
            if not isinstance(content, str) or len(content) > 2000:
                raise SetupError("message content must be a string of at most 2000 characters")

            async def run_edit(action: _Action, cursor: int) -> dict[str, Any]:
                await actor.edit(ResponseMessage(self.env, message), content)
                action.outcomes = [{"kind": "source_edit", "messageId": str(message.id)}]
                result = self._finish_action(page, action, "settled", cursor)
                self._publish_message_pages()
                return result

            return run_edit
        if kind == "delete_message":
            if body.get("confirmed") is not True:
                raise SetupError("message deletion requires confirmation")

            async def run_delete(action: _Action, cursor: int) -> dict[str, Any]:
                await actor.delete(ResponseMessage(self.env, message))
                action.outcomes = [{"kind": "no_output"}]
                result = self._finish_action(page, action, "settled", cursor)
                self._publish_message_pages()
                return result

            return run_delete
        if kind == "set_reaction":
            emoji = body.get("emoji")
            reacted = body.get("reacted")
            if not isinstance(emoji, str) or not emoji or not isinstance(reacted, bool):
                raise SetupError("reaction requires an emoji and desired membership")
            if message.reaction_for(emoji) is None:
                raise SetupError("reaction is unavailable")

            async def run_reaction(action: _Action, cursor: int) -> dict[str, Any]:
                await actor.set_reaction(ResponseMessage(self.env, message), emoji, reacted=reacted)
                action.outcomes = [{"kind": "source_edit", "messageId": str(message.id)}]
                result = self._finish_action(page, action, "settled", cursor)
                self._publish_message_pages()
                return result

            return run_reaction
        if kind == "set_poll_votes":
            raw_answers = body.get("answer_ids")
            if not isinstance(raw_answers, list):
                raise SetupError("poll answer ids must be a list")
            answers: list[int] = []
            for answer_id in raw_answers:
                if isinstance(answer_id, bool) or not isinstance(answer_id, (str, int)):
                    raise SetupError("poll answer ids must be integers")
                if isinstance(answer_id, str):
                    if not answer_id.isascii() or not answer_id.isdecimal():
                        raise SetupError("poll answer ids must be integers")
                    answer = int(answer_id)
                else:
                    answer = answer_id
                if answer in answers:
                    raise SetupError("poll answer ids must be unique")
                answers.append(answer)

            async def run_poll(action: _Action, cursor: int) -> dict[str, Any]:
                await actor.set_poll_votes(ResponseMessage(self.env, message), answers=answers)
                action.outcomes = [{"kind": "source_edit", "messageId": str(message.id)}]
                result = self._finish_action(page, action, "settled", cursor)
                self._publish_message_pages()
                return result

            return run_poll
        if kind == "set_pinned":
            pinned = body.get("pinned")
            if not isinstance(pinned, bool) or not isinstance(actor, MemberActor):
                raise SetupError("pinning is unavailable")

            async def run_pin(action: _Action, cursor: int) -> dict[str, Any]:
                await actor.set_pinned(ResponseMessage(self.env, message), pinned)
                action.outcomes = [{"kind": "source_edit", "messageId": str(message.id)}]
                result = self._finish_action(page, action, "settled", cursor)
                self._publish_message_pages()
                return result

            return run_pin
        if kind == "click":
            control_key = body.get("control_key")
            component = _find_scoped_component(
                message,
                control_key,
                types=(int(ComponentType.BUTTON),),
            )
            if component.get("style") in (5, 6) or not component.get("custom_id"):
                raise SetupError("control is unavailable")
            custom_id = str(component["custom_id"])

            async def run_click(action: _Action, cursor: int) -> dict[str, Any]:
                return await self._run_component_action(
                    page,
                    action,
                    cursor,
                    actor.click(ResponseMessage(self.env, message), custom_id=custom_id),
                )

            return run_click
        control_key = body.get("control_key")
        select_values = self._select_values(page, message, body.get("values"), control_key)
        custom_id = str(
            _find_scoped_component(message, control_key, types=tuple(int(item) for item in SELECT_TYPES))[
                "custom_id"
            ]
        )

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

    def _select_values(
        self,
        page: _Page,
        message: Message,
        values: Any,
        control_key: Any,
    ) -> list[Any]:
        if not isinstance(values, list):
            raise SetupError("select values must be a list")
        try:
            if len(values) != len(set(values)):
                raise SetupError("select values must be unique")
        except TypeError as exc:
            raise SetupError("select values must be scalar") from exc
        component = _find_scoped_component(
            message,
            control_key,
            types=tuple(int(item) for item in SELECT_TYPES),
        )
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
        )
        page.last_action = result
        action.response = result
        self._append_activity(page, result, action)
        page.pending_receipt_revision = settlement == "settled" and action.kind != "close"
        return result


__all__ = ["_Action", "_ActionOps"]
