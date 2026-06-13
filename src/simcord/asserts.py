"""Assertion helpers with failure messages that show what the bot actually did.

These are plain functions that raise ``AssertionError`` — runner-agnostic, so
they work under pytest (which renders the message) or plain unittest. Each one
prints the bot's real output on failure, so a red test explains itself without
a debugger.

    import simcord
    from simcord import assert_responded, assert_sent, assert_error

    async with simcord.run(bot) as env:
        ...
        assert_sent(channel, content="Pong!")
        result = await alice.slash(channel, "hello")
        assert_responded(result, contains="hi", ephemeral=True)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .results import InteractionResult, ResponseMessage

if TYPE_CHECKING:
    from .actors import MemberActor
    from .builders import ChannelHandle, UserHandle
    from .env import Env

MessageLike = ResponseMessage | discord.Message


def _ephemeral(message: MessageLike) -> bool:
    if isinstance(message, ResponseMessage):
        return message.ephemeral
    return message.flags.ephemeral


def _embed_titles(message: MessageLike) -> list[str | None]:
    return [embed.title for embed in message.embeds]


def _check_fields(
    message: MessageLike,
    *,
    content: str | None,
    contains: str | None,
    embed_title: str | None,
    ephemeral: bool | None,
) -> list[str]:
    """Return a list of human-readable mismatches (empty means it matched)."""
    problems = []
    if content is not None and message.content != content:
        problems.append(f"  content: expected {content!r}, got {message.content!r}")
    if contains is not None and contains not in message.content:
        problems.append(f"  content does not contain {contains!r} (got {message.content!r})")
    if embed_title is not None:
        titles = _embed_titles(message)
        if embed_title not in titles:
            problems.append(f"  no embed titled {embed_title!r} (embed titles: {titles!r})")
    if ephemeral is not None and _ephemeral(message) != ephemeral:
        problems.append(f"  ephemeral: expected {ephemeral}, got {_ephemeral(message)}")
    return problems


def assert_message(
    message: MessageLike,
    *,
    content: str | None = None,
    contains: str | None = None,
    embed_title: str | None = None,
    ephemeral: bool | None = None,
) -> None:
    """Assert a single message matches the given fields. Each field is checked
    only when provided. Accepts a ``ResponseMessage`` or a real ``discord.Message``."""
    problems = _check_fields(
        message, content=content, contains=contains, embed_title=embed_title, ephemeral=ephemeral
    )
    if problems:
        raise AssertionError("message did not match:\n" + "\n".join(problems) + f"\nactual: {message!r}")


def assert_sent(
    channel: ChannelHandle,
    *,
    content: str | None = None,
    contains: str | None = None,
    embed_title: str | None = None,
    viewer: MemberActor | UserHandle | None = None,
) -> None:
    """Assert the channel's most recent (visible) message matches.

    ``viewer=`` filters to what that user can see, hiding ephemeral messages
    addressed to others — the same rule as :meth:`ChannelHandle.history`.
    """
    history = channel.history(viewer=viewer)
    if not history:
        raise AssertionError(f"expected a message in {channel!r}, but none was sent")
    last = history[-1]
    problems = _check_fields(
        last, content=content, contains=contains, embed_title=embed_title, ephemeral=None
    )
    if problems:
        recent = "\n".join(f"  - {m.content!r}" for m in history[-5:])
        raise AssertionError(
            "last message did not match:\n" + "\n".join(problems) + f"\nrecent messages:\n{recent}"
        )


def assert_responded(
    result: InteractionResult,
    *,
    content: str | None = None,
    contains: str | None = None,
    embed_title: str | None = None,
    ephemeral: bool | None = None,
) -> None:
    """Assert the interaction produced a response message matching the given fields."""
    response = result.response
    if response is None:
        raise AssertionError(f"interaction produced no response message\nactual: {result!r}")
    problems = _check_fields(
        response, content=content, contains=contains, embed_title=embed_title, ephemeral=ephemeral
    )
    if problems:
        raise AssertionError(
            "interaction response did not match:\n" + "\n".join(problems) + f"\nactual: {result!r}"
        )


def assert_error(
    env: Env,
    exc_type: type[BaseException] = BaseException,
    *,
    code: int | None = None,
    contains: str | None = None,
) -> BaseException:
    """Assert the bot captured an error matching ``exc_type``/``code``/``contains``.

    discord.py wraps callback failures (e.g. ``CommandInvokeError.original``), so
    the type and code are matched against the error *and* its ``.original``.
    Returns the matched error. Reading ``env.errors`` marks errors inspected, so
    this also satisfies the teardown ``check_errors`` guard.
    """
    captured = env.errors

    def matches(error: BaseException) -> bool:
        candidates = [error]
        original = getattr(error, "original", None)
        if original is not None:
            candidates.append(original)
        if not any(isinstance(c, exc_type) for c in candidates):
            return False
        if code is not None and not any(getattr(c, "code", None) == code for c in candidates):
            return False
        if contains is not None and not any(contains in str(c) for c in candidates):
            return False
        return True

    for error in captured:
        if matches(error):
            return error

    wanted = [exc_type.__name__]
    if code is not None:
        wanted.append(f"code={code}")
    if contains is not None:
        wanted.append(f"containing {contains!r}")
    if not captured:
        raise AssertionError(f"expected an error ({', '.join(wanted)}), but the bot captured none")
    listed = "\n".join(f"  - {e!r}" for e in captured)
    raise AssertionError(f"no captured error matched ({', '.join(wanted)}); captured:\n{listed}")


def assert_no_errors(env: Env) -> None:
    """Assert the bot ran cleanly: raises an ``ExceptionGroup`` of anything it
    captured, or does nothing. A clearer-named pairing for :func:`assert_error`
    over :meth:`Env.raise_errors`."""
    env.raise_errors()
