"""Private detachment for wire payloads, including deeply nested reply ancestry."""

from copy import deepcopy
from typing import Any

_ATOMIC = (str, int, float, bool, bytes, type(None))


def detach(value: Any) -> Any:
    """Copy plain wire dict/list graphs without consuming the Python call stack.

    Non-wire data falls back as a whole to stdlib deepcopy, preserving custom
    hooks, traversal order, aliases and failures without invoking hooks twice.
    """
    if type(value) not in (dict, list):
        return deepcopy(value)
    result: Any = {} if type(value) is dict else []
    memo = {id(value): result}
    stack = [(iter(value.items()) if type(value) is dict else enumerate(value), result)]
    while stack:
        entries, target = stack[-1]
        try:
            key, item = next(entries)
        except StopIteration:
            stack.pop()
            continue
        if type(target) is dict and type(key) not in _ATOMIC:
            return deepcopy(value)
        kind = type(item)
        if kind in _ATOMIC:
            copied = item
        elif kind in (dict, list):
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
