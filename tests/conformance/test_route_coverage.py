"""Surface conformance: simcord's route table measured against discord.py.

``parity.unimplemented_routes()`` reads the ``Route(...)`` literals out of
``discord.http.HTTPClient`` and subtracts what simcord serves, so the parity
matrix's gap list is derived, not hand-written. These tests guard that
derivation: that extraction still works against the installed discord.py, and
that the implemented/gap split is correct on known anchors. The matrix-sync
check in ``tests/unit/test_parity.py`` turns a discord.py change into a failing
build until the gap list is regenerated — the drift guard.
"""

from __future__ import annotations

from simcord import parity


def test_route_extraction_is_healthy():
    # If discord.py changes how it writes routes, extraction would silently
    # under-report gaps. Assert we still introspect the bulk of the REST surface.
    assert len(parity.discordpy_rest_routes()) >= 150


def test_implemented_methods_are_not_reported_as_gaps():
    """Normalisation must match simcord routes to discord.py despite differing
    path-parameter names (e.g. ``{event_id}`` vs ``{guild_scheduled_event_id}``)."""
    gap_methods = {name for name, _, _ in parity.unimplemented_routes()}
    for method in (
        "send_message",
        "edit_message",
        "get_member",
        "ban",
        "add_reaction",
        "create_channel",
        "edit_channel",
        "edit_role",
        "get_guild",
        "create_invite",
        "delete_scheduled_event",
        "edit_channel_permissions",
        # Tier-1 routes implemented for 1.0.
        "get_role",
        "edit_my_member",
        "move_role_position",
        "bulk_channel_update",
        "leave_guild",
        "get_guilds",
        "edit_profile",
    ):
        assert method not in gap_methods, f"{method} is implemented but reported as a gap"


def test_unimplemented_methods_are_reported_as_gaps():
    """Feature areas simcord has no routes for must show up as gaps."""
    gap_methods = {name for name, _, _ in parity.unimplemented_routes()}
    for method in (
        "create_soundboard_sound",
        "get_entitlements",
        "create_integration",
        "get_guild_onboarding",
        "edit_widget",
        "delete_guild",
    ):
        assert method in gap_methods, f"{method} is unimplemented but not reported as a gap"


def test_out_of_scope_methods_are_separated_from_gaps():
    """Deliberate non-goals are reported as out-of-scope, not as backlog gaps,
    and stay valid discord.py methods (so the set can't silently rot)."""
    derivable = parity.discordpy_rest_routes()
    gap_methods = {name for name, _, _ in parity.unimplemented_routes()}
    oos_methods = {name for name, _, _ in parity.out_of_scope_routes()}

    assert parity.OUT_OF_SCOPE <= set(derivable), "an OUT_OF_SCOPE method is unknown to discord.py"
    assert oos_methods == set(parity.OUT_OF_SCOPE)
    assert not (oos_methods & gap_methods), "out-of-scope methods must not double-count as gaps"
