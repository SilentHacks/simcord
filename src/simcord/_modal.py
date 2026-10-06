"""Modal control validation, submission serialization, and deferred uploads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import interactions as _interactions
from .actors import (
    _as_values,
    _check_select_handles,
    _check_user_dm_channel,
    _dispatch_actor_interaction,
    _ensure_unique,
)
from .backend import serializers
from .backend.access import can_access_channel
from .backend.errors import SetupError
from .builders import ChannelHandle
from .components import validate_modal
from .enums import SELECT_TYPES, ComponentType, InteractionType
from .results import InteractionResult


def _component_interaction_data_id(component: dict[str, Any], data: dict[str, Any]) -> None:
    if component.get("id") is not None:
        data["id"] = component["id"]


def _modal_controls(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return interactive controls without their layout wrappers."""
    supported = {
        ComponentType.TEXT_INPUT,
        *SELECT_TYPES,
        ComponentType.FILE_UPLOAD,
        ComponentType.RADIO_GROUP,
        ComponentType.CHECKBOX_GROUP,
        ComponentType.CHECKBOX,
    }
    leaves: list[dict[str, Any]] = []
    for component in spec.get("components") or []:
        if component.get("type") == ComponentType.TEXT_DISPLAY:
            continue
        if component.get("type") == ComponentType.ACTION_ROW:
            for child in component.get("components") or []:
                if child.get("type") in supported:
                    leaves.append(child)
        elif component.get("type") == ComponentType.LABEL:
            child = component.get("component")
            if child and child.get("type") in supported:
                leaves.append(child)
        elif component.get("type") in supported:
            leaves.append(component)
    return leaves


def _modal_control_map(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    controls = {}
    for control in _modal_controls(spec):
        custom_id = control.get("custom_id")
        if not custom_id:
            raise SetupError("Modal control is missing custom_id")
        if custom_id in controls:
            raise SetupError(f"Modal custom_id {custom_id!r} is ambiguous")
        controls[custom_id] = control
    return controls


def _modal_files(value: Any) -> list[tuple[str, bytes]]:
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], bytes)
    ):
        return [value]
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise SetupError("FileUpload expects (filename, bytes) tuples")
    files = list(value)
    if not all(
        isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], bytes)
        for item in files
    ):
        raise SetupError("FileUpload expects (filename, bytes) tuples")
    return files


def _modal_bounds(
    component: dict[str, Any],
    *,
    default_min: int,
    default_max: int,
    maximum_limit: int,
) -> tuple[int, int]:
    lo = component.get("min_values")
    hi = component.get("max_values")
    lo = default_min if lo is None else lo
    hi = default_max if hi is None else hi
    if (
        isinstance(lo, bool)
        or isinstance(hi, bool)
        or not isinstance(lo, int)
        or not isinstance(hi, int)
        or lo < 0
        or hi < 1
        or hi < lo
        or hi > maximum_limit
    ):
        raise SetupError(f"Invalid modal value bounds for {component.get('custom_id')!r}")
    return lo, hi


def _modal_leaf(
    actor: Any,
    component: dict[str, Any],
    values: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    channel_id: int,
    pending_uploads: list[tuple[dict[str, Any], str, bytes]],
) -> dict[str, Any] | None:
    typ = ComponentType(component["type"])
    custom_id = component["custom_id"]
    supplied = custom_id in values
    value = values.get(custom_id)
    required = bool(component.get("required", typ in (ComponentType.TEXT_INPUT, *SELECT_TYPES)))
    data: dict[str, Any] = {"type": typ, "custom_id": custom_id}
    _component_interaction_data_id(component, data)

    if typ == ComponentType.TEXT_INPUT:
        if not supplied:
            if required:
                raise SetupError(f"Required modal control {custom_id!r} was not supplied")
            value = ""
        if not isinstance(value, str):
            raise SetupError(f"Text input {custom_id!r} expects a string")
        if required and not value:
            raise SetupError(f"Required modal control {custom_id!r} cannot be empty")
        minimum = component.get("min_length")
        maximum = component.get("max_length")
        if minimum is not None and (value or required) and len(value) < minimum:
            raise SetupError(f"Text input {custom_id!r} is shorter than min_length={minimum}")
        if maximum is not None and len(value) > maximum:
            raise SetupError(f"Text input {custom_id!r} exceeds max_length={maximum}")
        data["value"] = value
    elif typ in SELECT_TYPES:
        chosen = [] if not supplied else _as_values(value)
        _ensure_unique(chosen)
        lo, hi = _modal_bounds(component, default_min=1, default_max=1, maximum_limit=25)
        if required and not chosen:
            raise SetupError(f"Required modal control {custom_id!r} was not supplied")
        if chosen and not lo <= len(chosen) <= hi:
            raise SetupError(f"Modal control {custom_id!r} expects between {lo} and {hi} values")
        if typ == ComponentType.STRING_SELECT:
            if not all(isinstance(item, str) for item in chosen):
                raise SetupError(f"Select {custom_id!r} expects string values")
            options = {option["value"] for option in component.get("options") or []}
            unknown = [item for item in chosen if item not in options]
            if unknown:
                raise SetupError(f"Select option {unknown[0]!r} does not exist")
        else:
            _check_select_handles(typ, chosen)
            for item in chosen:
                _interactions.resolve_handle(actor._env.backend, item, resolved, user_id=actor.id)
            chosen = [str(item.id) for item in chosen]
        data["values"] = chosen
    elif typ == ComponentType.FILE_UPLOAD:
        files = [] if not supplied else _modal_files(value)
        lo, hi = _modal_bounds(component, default_min=1, default_max=1, maximum_limit=10)
        if required and not files:
            raise SetupError(f"Required modal control {custom_id!r} was not supplied")
        if files and not lo <= len(files) <= hi:
            raise SetupError(f"Modal control {custom_id!r} expects between {lo} and {hi} files")
        data["values"] = []
        for filename, blob in files:
            pending_uploads.append((data, filename, blob))
    elif typ == ComponentType.RADIO_GROUP:
        if not supplied:
            if required:
                raise SetupError(f"Required modal control {custom_id!r} was not supplied")
            value = None
        if value is not None:
            if not isinstance(value, str):
                raise SetupError(f"RadioGroup {custom_id!r} expects a string")
            options = {option["value"] for option in component.get("options") or []}
            if value not in options:
                raise SetupError(f"RadioGroup option {value!r} does not exist")
        data["value"] = value
    elif typ == ComponentType.CHECKBOX_GROUP:
        chosen = [] if not supplied else _as_values(value)
        options = component.get("options") or []
        lo, hi = _modal_bounds(
            component,
            default_min=1,
            default_max=len(options),
            maximum_limit=10,
        )
        _ensure_unique(chosen)
        if required and not chosen:
            raise SetupError(f"Required modal control {custom_id!r} was not supplied")
        if chosen and not lo <= len(chosen) <= hi:
            raise SetupError(f"Modal control {custom_id!r} expects between {lo} and {hi} values")
        if not all(isinstance(item, str) for item in chosen):
            raise SetupError(f"CheckboxGroup {custom_id!r} expects string values")
        options = {option["value"] for option in component.get("options") or []}
        unknown = [item for item in chosen if item not in options]
        if unknown:
            raise SetupError(f"CheckboxGroup option {unknown[0]!r} does not exist")
        data["values"] = chosen
    elif typ == ComponentType.CHECKBOX:
        value = component.get("default", False) if not supplied else value
        if not isinstance(value, bool):
            raise SetupError(f"Checkbox {custom_id!r} expects a bool")
        data["value"] = value
    else:
        raise SetupError(f"Unsupported modal component type {typ}")
    return data


def _modal_submit_nodes(
    nodes: list[dict[str, Any]],
    actor: Any,
    values: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    channel_id: int,
    pending_uploads: list[tuple[dict[str, Any], str, bytes]],
) -> list[dict[str, Any]]:
    out = []
    for node in nodes:
        typ = node.get("type")
        if typ == ComponentType.TEXT_DISPLAY:
            out.append({"type": typ, "id": node["id"]})
        elif typ == ComponentType.ACTION_ROW:
            children = _modal_submit_nodes(
                node.get("components") or [], actor, values, resolved, channel_id, pending_uploads
            )
            out.append({"type": typ, "id": node["id"], "components": children})
        elif typ == ComponentType.LABEL:
            child = node.get("component")
            assert isinstance(child, dict)
            children = _modal_submit_nodes([child], actor, values, resolved, channel_id, pending_uploads)
            out.append({"type": typ, "id": node["id"], "component": children[0]})
        elif typ in {
            ComponentType.TEXT_INPUT,
            *SELECT_TYPES,
            ComponentType.FILE_UPLOAD,
            ComponentType.RADIO_GROUP,
            ComponentType.CHECKBOX_GROUP,
            ComponentType.CHECKBOX,
        }:
            out.append(_modal_leaf(actor, node, values, resolved, channel_id, pending_uploads))
    return out


def _commit_modal_uploads(
    actor: Any,
    channel_id: int,
    pending_uploads: list[tuple[dict[str, Any], str, bytes]],
    resolved: dict[str, dict[str, Any]],
) -> None:
    backend = actor._env.backend
    for data, filename, blob in pending_uploads:
        attachment_id = _interactions.store_interaction_attachment(
            backend, channel_id, filename, blob, resolved
        )
        data["values"].append(attachment_id)


async def _submit_modal(actor: Any, shown: InteractionResult, values: dict[str, Any]) -> InteractionResult:
    if not isinstance(shown, InteractionResult) or shown._env is not actor._env:
        raise SetupError("That modal belongs to another Env")
    interaction = shown._interaction
    if interaction.user_id != actor.id:
        raise SetupError("Only the modal opener can submit it")
    if interaction.modal is None:
        raise SetupError("That interaction did not respond with a modal")
    if interaction.modal_consumed:
        raise SetupError("That modal has already been submitted")
    spec = interaction.modal
    if not isinstance(values, dict):
        raise SetupError("Modal values must be a dict keyed by custom_id")
    try:
        spec = validate_modal(spec)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    channel_id = interaction.channel_id
    _check_user_dm_channel(actor, channel_id)
    if not can_access_channel(actor._env, channel_id, actor):
        raise SetupError("That modal is no longer available to this user")
    controls = _modal_control_map(spec)
    unknown = set(values) - set(controls)
    if unknown:
        raise SetupError(f"Unknown modal custom_id {sorted(unknown)[0]!r}")
    resolved: dict[str, dict[str, Any]] = {}
    pending_uploads: list[tuple[dict[str, Any], str, bytes]] = []
    components = _modal_submit_nodes(spec["components"], actor, values, resolved, channel_id, pending_uploads)
    backend = actor._env.backend
    source_message_id = shown._interaction.source_message_id
    extra = None
    if source_message_id is not None:
        source = backend.get_message(channel_id, source_message_id)
        extra = {"message": dict(serializers.message_payload(backend, source))}
    channel = ChannelHandle(actor._env, getattr(actor, "guild", None), backend.get_channel(channel_id))
    _commit_modal_uploads(actor, channel_id, pending_uploads, resolved)
    data: dict[str, Any] = {"custom_id": spec["custom_id"], "components": components}
    if resolved:
        data["resolved"] = resolved
    interaction.modal_consumed = True
    return await _dispatch_actor_interaction(
        actor,
        InteractionType.MODAL_SUBMIT,
        channel,
        data,
        extra=extra,
        source_message_id=source_message_id,
    )
