"""Property-based fuzzing of the honesty layer.

Generalises ``test_field_honesty.py`` from a dozen hand-picked routes to a
property-based sweep over *every* route whose body flows through
``RequestContext.fields``/``list_fields``. The honesty layer and its synchronous
entry point (``router.dispatch(backend, ...)``) need no bot and no event loop, so
the whole sweep runs as a plain Hypothesis test against a populated backend world.

What it proves, for every honesty-vetted route:

* **Loudness** — a body key the handler does not declare is never silently
  dropped; it raises ``UnsupportedField`` naming that key. A handler that read its
  body directly (bypassing the layer) would drop the key and fail this test.
* **No false rejection** — a key the handler *does* declare never raises
  ``UnsupportedField``.
* **Reject reasons** — a deliberately-refused field surfaces its documented reason.

What it does *not* prove: that a declared key is actually written to state (the
``*_fields_apply`` cases in ``test_field_honesty.py`` cover that), and it only
covers routes that *use* the honesty layer. Routes that read their body directly
(message create, interaction callback, bulk command overwrite, ...) are enumerated
in ``EXEMPT`` and guarded by :func:`test_every_write_route_is_classified`, which —
like the parity drift guard — fails loudly if a new write route is neither vetted
nor explicitly exempt.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from _world import build_world, resolve
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from simcord.backend.errors import BackendError
from simcord.http.router import RequestContext, UnsupportedField, dispatch
from simcord.parity import implemented_routes

_WRITE_METHODS = ("POST", "PATCH", "PUT")


class _Probe(BaseException):
    """Aborts a handler the instant it calls the honesty layer.

    A ``BaseException`` (not ``Exception``/``BackendError``) so no handler's
    ``except`` can swallow it, and so the handler never mutates state past the
    ``ctx.fields`` call — letting us record every route's field contract against
    one shared world without the routes trampling each other.
    """


@dataclass(frozen=True)
class Contract:
    method: str
    template: str
    kind: str  # "object" (fields) or "array" (list_fields)
    handled: frozenset[str]
    ignore: frozenset[str]
    reject: tuple[tuple[str, str], ...]


def _discover() -> dict[tuple[str, str], Contract]:
    """Probe every write route to learn which use the honesty layer, and how.

    Monkeypatches ``fields``/``list_fields`` to record the declared contract and
    raise :class:`_Probe`, then dispatches each route (in a fresh world, so a
    non-vetting route that runs to completion cannot mutate the next route's
    resources) with an empty body. Routes that reach the layer are recorded; the
    rest either run clean (no body contract) or fail before the layer.
    """
    contracts: dict[tuple[str, str], Contract] = {}
    captured: dict[str, object] = {}
    orig_fields, orig_list = RequestContext.fields, RequestContext.list_fields

    def probe_fields(self, *handled, ignore=(), reject=None):  # type: ignore[no-untyped-def]
        captured["c"] = ("object", frozenset(handled), frozenset(ignore), tuple((reject or {}).items()))
        raise _Probe

    def probe_list(self, *handled, ignore=()):  # type: ignore[no-untyped-def]
        captured["c"] = ("array", frozenset(handled), frozenset(ignore), ())
        raise _Probe

    RequestContext.fields = probe_fields  # type: ignore[method-assign]
    RequestContext.list_fields = probe_list  # type: ignore[method-assign]
    try:
        for method, template in _write_routes():
            world = build_world()
            path = resolve(template, world)
            if path is None:
                continue
            captured.pop("c", None)
            try:
                dispatch(world.backend, method, path, json={})
            except _Probe:
                kind, handled, ignore, reject = captured["c"]  # type: ignore[misc]
                contracts[method, template] = Contract(method, template, kind, handled, ignore, reject)
            except BaseException:
                # Ran clean (no honesty layer) or failed before it; either way the
                # completeness guard below requires it to be classified in EXEMPT.
                pass
    finally:
        RequestContext.fields = orig_fields  # type: ignore[method-assign]
        RequestContext.list_fields = orig_list  # type: ignore[method-assign]
    return contracts


def _write_routes() -> list[tuple[str, str]]:
    return [(m, p) for m, p in implemented_routes() if m in _WRITE_METHODS]


CONTRACTS = _discover()
VETTED = sorted(CONTRACTS)
REJECTING = sorted(r for r in VETTED if CONTRACTS[r].reject)

# Write routes that deliberately do NOT route their body through the honesty
# layer, each with the reason. Two kinds: routes with no request body, and the
# structured-payload routes that read specific keys directly. This is the honest
# inventory of the honesty layer's own blind spots — keep it accurate.
EXEMPT: dict[tuple[str, str], str] = {
    # No request body to vet.
    ("POST", "/channels/{channel_id}/messages/{message_id}/crosspost"): "no request body",
    ("POST", "/channels/{channel_id}/polls/{message_id}/expire"): "no request body",
    ("POST", "/channels/{channel_id}/typing"): "no request body",
    ("PUT", "/channels/{channel_id}/messages/pins/{message_id}"): "no request body",
    ("PUT", "/channels/{channel_id}/thread-members/@me"): "no request body",
    ("PUT", "/channels/{channel_id}/thread-members/{user_id}"): "no request body",
    ("PUT", "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me"): "no request body",
    ("PUT", "/guilds/{guild_id}/members/{user_id}/roles/{role_id}"): "no request body",
    ("PUT", "/guilds/{guild_id}/bans/{user_id}"): (
        "discards the body — delete_message_seconds is not modelled offline"
    ),
    # Structured payloads read key-by-key rather than vetted as a flat field set.
    ("POST", "/channels/{channel_id}/messages"): (
        "message create reads content/embeds/components/poll/... directly (bot_message)"
    ),
    ("POST", "/channels/{channel_id}/messages/bulk-delete"): "reads the `messages` id list directly",
    ("POST", "/webhooks/{webhook_id}/{token}"): "webhook execute is message-create-shaped",
    ("POST", "/interactions/{interaction_id}/{token}/callback"): "interaction callback reads nested `data`",
    ("PUT", "/applications/{application_id}/commands"): "bulk command overwrite reads the command array",
    ("PUT", "/applications/{application_id}/guilds/{guild_id}/commands"): (
        "bulk command overwrite reads the command array"
    ),
}

_GARBAGE_KEYS = st.text(max_size=8).map(lambda s: "zzfuzz_" + s)
_VALUES = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=12))


def _ids(route: tuple[str, str]) -> str:
    return f"{route[0]} {route[1]}"


def _dispatch(method: str, template: str, body: object) -> None:
    world = build_world()
    path = resolve(template, world)
    assert path is not None  # VETTED routes always resolve
    dispatch(world.backend, method, path, json=body)


def _wrap(kind: str, key: str, value: object) -> object:
    return [{key: value}] if kind == "array" else {key: value}


@pytest.mark.parametrize("route", VETTED, ids=_ids)
@settings(max_examples=25, deadline=None)
@given(key=_GARBAGE_KEYS, value=_VALUES)
def test_unknown_field_rejected(route: tuple[str, str], key: str, value: object) -> None:
    """A body key no handler declares raises UnsupportedField, naming the key."""
    contract = CONTRACTS[route]
    assume(key not in contract.handled and key not in contract.ignore)
    assume(key not in {k for k, _ in contract.reject})
    with pytest.raises(UnsupportedField) as exc:
        _dispatch(contract.method, contract.template, _wrap(contract.kind, key, value))
    assert key in exc.value.fields


@pytest.mark.parametrize("route", VETTED, ids=_ids)
@settings(max_examples=15, deadline=None)
@given(data=st.data())
def test_declared_field_not_rejected(route: tuple[str, str], data: st.DataObject) -> None:
    """A key the handler declares is never rejected by the honesty layer."""
    contract = CONTRACTS[route]
    if not contract.handled:
        pytest.skip("route declares no handled fields")
    key = data.draw(st.sampled_from(sorted(contract.handled)))
    value = data.draw(_VALUES)
    try:
        _dispatch(contract.method, contract.template, _wrap(contract.kind, key, value))
    except UnsupportedField as exc:  # must precede BackendError — it is a subclass
        pytest.fail(f"declared field {key!r} wrongly rejected on {_ids(route)}: {exc}")
    except BackendError:
        pass  # a bad *value* (e.g. unparseable id) is fine; only the key matters here
    except Exception:
        pass  # handler choking on a fuzzed value is not an honesty violation


@pytest.mark.parametrize("route", REJECTING, ids=_ids)
def test_reject_reason_surfaces(route: tuple[str, str]) -> None:
    """A deliberately-refused field fails loudly carrying its documented reason."""
    contract = CONTRACTS[route]
    for key, reason in contract.reject:
        with pytest.raises(UnsupportedField) as exc:
            _dispatch(contract.method, contract.template, _wrap(contract.kind, key, "x"))
        assert key in exc.value.fields
        assert reason in " ".join(getattr(exc.value, "__notes__", []))


def test_every_write_route_is_classified() -> None:
    """Every write route is honesty-vetted or explicitly exempt — no silent gap.

    The drift guard for the honesty layer (mirrors ``test_parity``): a new write
    route must either flow its body through ``ctx.fields`` or be added to
    ``EXEMPT`` with a reason, or this fails.
    """
    writes = set(_write_routes())
    vetted = set(CONTRACTS)
    exempt = set(EXEMPT)
    assert not (vetted & exempt), f"routes both vetted and exempt (stale EXEMPT): {vetted & exempt}"
    assert exempt <= writes, f"EXEMPT names routes that no longer exist: {exempt - writes}"
    unclassified = writes - vetted - exempt
    assert not unclassified, (
        "unclassified write route(s) — route the body through ctx.fields or add to EXEMPT: "
        f"{sorted(unclassified)}"
    )
    # Coverage floor: guard against a regression that quietly drops the harness's
    # reach (which would let routes slip from vetted into EXEMPT unnoticed).
    assert len(vetted) >= 39, f"honesty-layer coverage dropped to {len(vetted)} routes"
    assert REJECTING, "expected at least one reject= route (e.g. prune include_roles)"
