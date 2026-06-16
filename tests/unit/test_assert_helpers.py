"""Failure paths of the public assertion helpers: the mismatch messages are part
of the value proposition (a red test should explain itself), so they are tested.
"""

from __future__ import annotations

import types

import pytest

import simcord
from simcord import assert_error, assert_message


def _msg(content="hello world", titles=("Title",)):
    return types.SimpleNamespace(
        content=content,
        embeds=[types.SimpleNamespace(title=t) for t in titles],
    )


def test_assert_message_content_mismatch():
    with pytest.raises(AssertionError, match="content"):
        assert_message(_msg(), content="bye")


def test_assert_message_contains_mismatch():
    with pytest.raises(AssertionError, match="does not contain"):
        assert_message(_msg(), contains="zzz")


def test_assert_message_embed_title_mismatch():
    with pytest.raises(AssertionError, match="no embed titled"):
        assert_message(_msg(), embed_title="Missing")


def test_assert_message_positive_paths():
    # Matching content/embed_title raises nothing (exercises _embed_titles too).
    assert_message(_msg(), contains="world", embed_title="Title")


class _Wrapper(Exception):
    """Stands in for discord.py's CommandInvokeError, which wraps .original."""

    def __init__(self, original: BaseException) -> None:
        super().__init__("wrapped")
        self.original = original


class _FakeEnv:
    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = errors


def test_assert_error_matches_via_unwrapped_original():
    inner = simcord.BackendError(403, 50013, "Missing Permissions")
    env = _FakeEnv([_Wrapper(inner)])

    matched = assert_error(env, simcord.BackendError, code=50013, contains="Missing")
    assert matched.original is inner


def test_assert_error_no_match_lists_captured():
    env = _FakeEnv([ValueError("nope")])
    with pytest.raises(AssertionError, match="no captured error matched"):
        assert_error(env, simcord.BackendError, code=50013, contains="x")


def test_assert_error_none_captured():
    env = _FakeEnv([])
    with pytest.raises(AssertionError, match="captured none"):
        assert_error(env, simcord.BackendError)
