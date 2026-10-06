"""Lazy, pre-admission plans for navigation, commands, and message actions."""

from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from ..actors import MemberActor, _autocomplete_result, _slash_resolved
from ..backend.access import can_access_channel, can_access_message
from ..backend.errors import BackendError, SetupError
from ..builders import UserHandle
from ..enums import SELECT_TYPES, ComponentType, OptionType
from ..interactions import OptionError, check_options
from ..results import ResponseMessage
from ._diagnostics import make_diagnostic
from ._pages import HISTORY_STEP, HISTORY_WINDOW_SIZE

if TYPE_CHECKING:
    from ..backend.models import Message
    from . import Preview
    from ._actions import _Action
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


def _prepare_page_action(self: Preview, page: _Page, kind: str, body: Mapping[str, Any]) -> Any:
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

        self._require_current_history(page)
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

        self._require_current_history(page)
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
                not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 20
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
            if modal_handle is None and not control_key.startswith("command:"):
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
        self._require_current_history(page)
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
        start = max(0, end - HISTORY_WINDOW_SIZE)
        if direction == "older":
            if start == 0:
                raise SetupError("there is no earlier authorized history")
            next_end = min(end, start + HISTORY_STEP)
            next_anchor = ids[next_end - 1]
        elif direction == "newer":
            if end == len(ids):
                raise SetupError("there is no newer authorized history")
            next_start = min(len(ids) - 1, start + HISTORY_STEP)
            next_end = min(len(ids), next_start + HISTORY_WINDOW_SIZE)
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


def _prepare_send_action(self: Preview, page: _Page, body: Mapping[str, Any]) -> Any:
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


def _prepare_command_action(
    self: Preview, page: _Page, kind: str, body: Mapping[str, Any], actor: Any
) -> Any:
    from ._commands import command_permission, entry_for_leaf, visible_leaf
    from ._snapshot import _QueryError

    if page.layout != "channel" or page.status != "current":
        raise _QueryError("command-unavailable")
    if not command_permission(cast("Preview", self), page):
        raise _QueryError("command-unavailable")
    resolved = visible_leaf(cast("Preview", self), page, body.get("command_id"), body.get("path"))
    if resolved is None:
        raise _QueryError("command-unavailable")
    root, path, leaf = resolved
    entry = entry_for_leaf(root, path, leaf)
    if body.get("schema_fingerprint") != entry["schemaFingerprint"]:
        raise _QueryError("command-changed")
    invocation = entry["invocation"]
    if kind == "autocomplete_command":
        focused = body.get("focused")
        value = body.get("value")
        raw_options = body.get("options")
        if not isinstance(focused, str) or not isinstance(value, str) or not isinstance(raw_options, Mapping):
            raise SetupError("autocomplete command input is unavailable")
        declared = {option["name"]: option for option in leaf.get("options") or []}
        focused_option = declared.get(focused)
        if focused_option is None or not focused_option.get("autocomplete"):
            raise _QueryError("command-unavailable")
        filled: dict[str, Any] = {}
        for name, raw in raw_options.items():
            option = declared.get(name) if isinstance(name, str) else None
            if option is None or name == focused:
                continue
            try:
                filled[name] = self._command_option_value(page, invocation, option, raw)
            except (OptionError, SetupError, BackendError, ValueError):
                continue

        async def run_autocomplete(action: _Action, cursor: int) -> dict[str, Any]:
            self.env._begin_operation("autocomplete")
            try:
                result = await _autocomplete_result(
                    actor,
                    self.channel,
                    invocation,
                    focused,
                    value,
                    filled,
                    root=root,
                )
            finally:
                self.env._end_operation()
            offered = result.autocomplete_choices
            answered = offered is not None
            action.autocomplete_answered = answered
            choices = [
                {"name": item["name"], "value": item["value"]}
                for item in (offered or [])[:25]
                if isinstance(item, Mapping)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("value"), (str, int, float, bool))
            ]
            action.record_activity = not answered
            finished = self._finish_action(
                page,
                action,
                "settled" if answered else "failed",
                cursor,
                interaction=result._interaction,
                extra_diagnostics=(
                    []
                    if answered
                    else [make_diagnostic("autocomplete-unanswered", correlation=action.correlation)]
                ),
            )
            finished["result"] = {
                "command": dict(action.command or {}),
                "focused": focused,
                "answered": answered,
                "choices": choices,
            }
            action.response = finished
            return finished

        return run_autocomplete

    raw_options = body.get("options")
    if not isinstance(raw_options, Mapping):
        raise SetupError("command options must be an object")
    parsed: dict[str, Any] = {}
    upload_bytes = 0
    declared = {option["name"]: option for option in leaf.get("options") or []}
    for name, raw in raw_options.items():
        if not isinstance(name, str) or name not in declared:
            raise OptionError("option-unknown", str(name), f"Command '{invocation}' has no such option")
        value = self._command_option_value(page, invocation, declared[name], raw)
        if OptionType(declared[name]["type"]) == OptionType.ATTACHMENT:
            upload_bytes += len(value[1])
            if upload_bytes > 25 * 1024 * 1024:
                raise OptionError(
                    "option-type", name, "command attachments exceed the 25 MiB aggregate limit"
                )
        parsed[name] = value
    parsed = check_options(invocation, leaf, parsed)
    if isinstance(actor, MemberActor) or (
        isinstance(actor, UserHandle) and actor._env.backend.dm_channels.get(actor.id) == page.channel_id
    ):

        async def run_command(action: _Action, cursor: int) -> dict[str, Any]:
            return await self._run_interaction_action(
                page,
                action,
                cursor,
                _slash_resolved(actor, self.channel, invocation, parsed, root),
                republish_all=True,
            )
    else:
        raise _QueryError("command-unavailable")
    return run_command


def _prepare_modal_action(self: Preview, page: _Page, body: Mapping[str, Any], actor: Any) -> Any:
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
        finished = self._finish_action(page, action, "settled", cursor, interaction=result._interaction)
        self._publish(page)
        return finished

    return run_modal


def _prepare_message_action(
    self: Preview, page: _Page, kind: str, body: Mapping[str, Any], actor: Any, message: Message
) -> Any:
    if kind == "edit_message":
        content = body.get("content")
        if not isinstance(content, str) or len(content) > 2000:
            raise SetupError("message content must be a string of at most 2000 characters")

        async def run_edit(action: _Action, cursor: int) -> dict[str, Any]:
            await actor.edit(ResponseMessage(self.env, message), content)
            return self._finish_message_edit(page, action, cursor, message)

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
            return self._finish_message_edit(page, action, cursor, message)

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
            return self._finish_message_edit(page, action, cursor, message)

        return run_poll
    if kind == "set_pinned":
        pinned = body.get("pinned")
        if not isinstance(pinned, bool) or not isinstance(actor, MemberActor):
            raise SetupError("pinning is unavailable")

        async def run_pin(action: _Action, cursor: int) -> dict[str, Any]:
            await actor.set_pinned(ResponseMessage(self.env, message), pinned)
            return self._finish_message_edit(page, action, cursor, message)

        return run_pin


def _prepare_component_action(
    self: Preview, page: _Page, kind: str, body: Mapping[str, Any], actor: Any, message: Message
) -> Any:
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
            return await self._run_interaction_action(
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
        return await self._run_interaction_action(
            page,
            action,
            cursor,
            actor.select(ResponseMessage(self.env, message), select_values, custom_id=custom_id),
        )

    return run_select
