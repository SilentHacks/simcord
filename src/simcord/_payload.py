"""Private detachment for wire payloads, including deeply nested reply ancestry."""

from copy import deepcopy
from typing import Any


def detach(value: Any) -> Any:
    """Copy plain wire dict/list graphs without consuming the Python call stack.

    Non-wire data falls back as a whole to stdlib deepcopy, preserving custom
    hooks, traversal order, aliases and failures without invoking hooks twice.
    """
    kind = type(value)
    if kind is not dict and kind is not list:
        return deepcopy(value)
    result: Any = {} if kind is dict else []
    memo = {id(value): result}
    stack = [(iter(value.items()) if kind is dict else enumerate(value), result)]
    while stack:
        entries, target = stack[-1]
        try:
            key, item = next(entries)
        except StopIteration:
            stack.pop()
            continue
        if type(target) is dict:
            key_kind = type(key)
            if not (
                key_kind is str
                or key_kind is int
                or key_kind is float
                or key_kind is bool
                or key_kind is bytes
                or key is None
            ):
                return deepcopy(value)
        kind = type(item)
        if kind is str or kind is int or kind is float or kind is bool or kind is bytes or item is None:
            copied = item
        elif kind is dict or kind is list:
            identity = id(item)
            if identity in memo:
                copied = memo[identity]
            else:
                copied = {} if kind is dict else []
                memo[identity] = copied
                stack.append((iter(item.items()) if kind is dict else enumerate(item), copied))
        else:
            return deepcopy(value)
        if type(target) is list:
            target.append(copied)
        else:
            target[key] = copied
    return result
