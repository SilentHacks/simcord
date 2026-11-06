"""pytest plugin: ready-made fixtures for simcord.

Define a ``simcord_bot`` fixture in your conftest that builds your bot, and the
``simcord_env`` fixture hands you a running environment::

    # conftest.py
    import pytest
    from mybot import create_bot

    @pytest.fixture
    def simcord_bot():
        return create_bot()

    # test_bot.py
    async def test_ping(simcord_env):
        channel = simcord_env.create_guild().create_text_channel("general")
        ...

Per-test options for the environment go through the ``simcord`` marker, whose
keyword arguments are forwarded to :func:`simcord.run`::

    @pytest.mark.simcord(strict_sync=False)
    async def test_unsynced_command(simcord_env):
        ...

Requires the ``pytest`` extra (``pip install simcord[pytest]``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
import pytest

from ._diagnostics import ACTIVE_RUN, PermissionRun
from .env import Env

_DIAGNOSTICS = pytest.StashKey[dict[str, Any]]()


def pytest_addoption(parser):
    group = parser.getgroup("simcord")
    group.addoption(
        "--simcord-observed-permissions",
        action="store_true",
        help="Report the union of permission flags required by bot REST checks.",
    )
    group.addoption(
        "--simcord-sweep",
        action="store_true",
        help="Rerun passing tests with each observed bot permission authentically revoked.",
    )


try:
    import pytest_asyncio
except ImportError:  # pragma: no cover - plugin is inert without the extra
    pytest_asyncio = None


def pytest_configure(config):
    config.stash[_DIAGNOSTICS] = {"observed": 0, "sweeps": []}
    config.addinivalue_line(
        "markers",
        "simcord(**options): keyword options forwarded to simcord.run() for the simcord_env fixture",
    )


def _unsupported_scopes(item):
    reasons = []
    for name, definitions in item._fixtureinfo.name2fixturedefs.items():
        if not definitions:
            continue
        definition = definitions[-1]
        module = definition.func.__module__
        if definition.scope != "function" and not module.startswith(
            ("_pytest.", "pytest_asyncio.", "faker.", "pytest_metadata.")
        ):
            reasons.append(f"fixture {name!r} has unsupported {definition.scope} scope")
        loop_scope = getattr(definition.func, "_loop_scope", None)
        if loop_scope not in (None, "function"):
            reasons.append(f"fixture {name!r} has unsupported {loop_scope} asyncio loop scope")
    marker = item.get_closest_marker("asyncio")
    loop_scope = marker.kwargs.get("loop_scope", marker.kwargs.get("scope")) if marker else None
    if loop_scope not in (None, "function"):
        reasons.append(f"unsupported {loop_scope} asyncio test loop scope")
    for setting in ("asyncio_default_test_loop_scope", "asyncio_default_fixture_loop_scope"):
        try:
            scope = item.config.getini(setting)
        except ValueError:
            continue  # Older supported pytest-asyncio versions do not register both settings.
        if scope not in (None, "", "function"):
            reasons.append(f"unsupported {setting}={scope}")
    return reasons


def _leaves(error):
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from _leaves(child)
    else:
        yield error


def _failure_detail(error, source):
    original = getattr(error, "original", error)
    trace = original.__traceback__
    location = "source location unavailable (no user traceback)"
    while trace is not None:
        module = trace.tb_frame.f_globals.get("__name__", "")
        if not module.startswith(
            ("simcord.", "discord.", "_pytest.", "pytest_asyncio.", "pluggy.", "asyncio.")
        ):
            code = trace.tb_frame.f_code
            location = f"{code.co_filename}:{trace.tb_lineno} ({code.co_name})"
        trace = trace.tb_next
    source = f"{source}; {location}"
    return f"unhandled {type(original).__name__}: {original} (source: {source})"


def _application_error(error, item):
    """Do not mistake an SDK assertion helper for unhandled application code."""
    root = item.config.rootpath
    testpaths = [root / path for path in item.config.getini("testpaths")]
    if not testpaths:
        relative = item.path.relative_to(root)
        testpaths = [root / relative.parts[0] if len(relative.parts) > 1 else root]
    trace = getattr(error, "original", error).__traceback__
    caller = None
    while trace is not None:
        module = trace.tb_frame.f_globals.get("__name__", "")
        if isinstance(error, TimeoutError) and module == "simcord._settlement":
            return True
        filename = Path(trace.tb_frame.f_code.co_filename).resolve()
        if not module.startswith(
            ("simcord.", "discord.", "_pytest.", "pytest_asyncio.", "pluggy.", "asyncio.")
        ) and filename.is_relative_to(root):
            caller = filename
        trace = trace.tb_next
    return (
        caller is not None
        and caller != item.path
        and caller.name != "conftest.py"
        and not any(caller.is_relative_to(path) for path in testpaths)
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    """Use pytest's complete rerun lifecycle, including fresh async fixture teardown."""
    config = item.config
    if not (config.getoption("simcord_sweep") or config.getoption("simcord_observed_permissions")):
        return None
    from _pytest.runner import runtestprotocol

    state = config.stash[_DIAGNOSTICS]
    baseline = PermissionRun()
    scope_reasons = _unsupported_scopes(item) if config.getoption("simcord_sweep") else []
    token = ACTIVE_RUN.set(baseline)
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    try:
        # Each full protocol finalizes function fixtures. Retain pytest's
        # ordinary broader fixture lifetime; those user scopes are not swept.
        reports = runtestprotocol(item, nextitem=nextitem)
    finally:
        ACTIVE_RUN.reset(token)
        item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    observed = 0
    guild_flags = set()
    for env in baseline.envs:
        observed |= env.observed_permissions().value
        for check in env.backend.permission_checks:
            if check["channel_id"] is None and check["guild_id"] is not None:
                guild_flags.update(getattr(discord.Permissions, name).flag for name in check["required"])
    state["observed"] |= observed
    if not config.getoption("simcord_sweep") or not baseline.envs:
        return True
    if not all(report.passed for report in reports):
        state["sweeps"].append((item.nodeid, None, "baseline not passed; not swept"))
        return True
    if not observed:
        state["sweeps"].append((item.nodeid, None, "no observed flags; not swept"))
        return True
    unsupported = scope_reasons or _unsupported_scopes(item)
    if unsupported:
        state["sweeps"].append((item.nodeid, None, "unsupported: " + "; ".join(unsupported)))
        return True
    for flag, enabled in discord.Permissions(observed):
        if not enabled:
            continue
        run = PermissionRun(
            flag=flag,
            guild_scope=getattr(discord.Permissions, flag).flag in guild_flags,
            baseline_env_ids={id(env) for env in baseline.envs},
        )
        token = ACTIVE_RUN.set(run)
        try:
            rerun_reports = runtestprotocol(item, log=False, nextitem=nextitem)
        finally:
            ACTIVE_RUN.reset(token)
            for env in run.envs:
                env.backend.permission_projection = None
        captured = [error for env in run.envs for error in env.errors_since(0)]
        failures = [
            (leaf, "bot callback/task")
            for env in run.envs
            for error in env.errors_since(0)
            if id(error) in env._unhandled_error_ids
            for leaf in _leaves(error)
        ]
        known = {id(leaf) for error in captured for leaf in _leaves(error)}
        for report in rerun_reports:
            for error in getattr(report, "_simcord_exceptions", ()):
                if id(error) in known or (report.when == "call" and isinstance(error, AssertionError)):
                    continue
                if report.when == "call" and _application_error(error, item):
                    failures.append((error, report.when))
                elif report.failed:
                    # Unowned call failures and invalid fixture lifecycles are not bot findings.
                    run.unsupported.append(
                        f"{report.when} uncaptured error: {_failure_detail(error, report.when)}"
                    )
                    break
        if run.unsupported:
            state["sweeps"].append((item.nodeid, flag, "unsupported: " + "; ".join(run.unsupported)))
        elif failures:
            for error, source in failures:
                state["sweeps"].append((item.nodeid, flag, _failure_detail(error, source)))
        else:
            state["sweeps"].append((item.nodeid, flag, "no unhandled bot errors"))
    return True


def pytest_sessionfinish(session, exitstatus):
    state = session.config.stash[_DIAGNOSTICS]
    if hasattr(session.config, "workeroutput"):
        session.config.workeroutput["simcord_diagnostics"] = state
    if session.exitstatus == pytest.ExitCode.OK and any(
        detail.startswith("unhandled ") for _, _, detail in state["sweeps"]
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """Merge JSON-friendly worker results; never print a false empty master report."""
    if not (node.config.getoption("simcord_sweep") or node.config.getoption("simcord_observed_permissions")):
        return
    state = node.config.stash[_DIAGNOSTICS]
    child = getattr(node, "workeroutput", {}).get("simcord_diagnostics")
    if child is None:
        state["sweeps"].append((node.gateway.id, None, "unsupported: worker diagnostic results unavailable"))
        return
    state["observed"] |= child["observed"]
    state["sweeps"].extend(child["sweeps"])


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if hasattr(config, "workerinput"):
        return
    state = config.stash[_DIAGNOSTICS]
    if config.getoption("simcord_observed_permissions"):
        names = [name for name, enabled in discord.Permissions(state["observed"]) if enabled]
        terminalreporter.section("simcord observed permissions")
        incomplete = any(
            "worker diagnostic results unavailable" in detail for _, _, detail in state["sweeps"]
        )
        terminalreporter.write_line(", ".join(names) or ("(no flags received)" if incomplete else "(none)"))
        if incomplete:
            terminalreporter.write_line("Incomplete coverage: worker diagnostic results unavailable.")
    if config.getoption("simcord_sweep"):
        terminalreporter.section("simcord permission sweep")
        for nodeid, flag, detail in state["sweeps"]:
            terminalreporter.write_line(f"{nodeid} [{flag or 'baseline'}]: {detail}")
        findings = sum(detail.startswith("unhandled ") for _, _, detail in state["sweeps"])
        unsupported = sum(detail.startswith("unsupported:") for _, _, detail in state["sweeps"])
        exercised = len(
            {
                (nodeid, flag)
                for nodeid, flag, detail in state["sweeps"]
                if flag is not None and not detail.startswith("unsupported:")
            }
        )
        terminalreporter.write_line(
            f"{exercised} exercised revocation case(s); "
            f"{findings} unhandled bot error(s); {unsupported} unsupported case(s)"
        )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    """Select clocks in the actual test context, never an async fixture task."""
    envs = {
        value for value in getattr(item, "funcargs", {}).values() if isinstance(value, Env) and value._started
    }
    if len(envs) == 1:
        with next(iter(envs)).time_context():
            return (yield)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach the env transcript to failing tests — "what the bot did"."""
    report = yield
    run = ACTIVE_RUN.get()
    if run is not None and report.when == "setup" and report.passed:
        for value in getattr(item, "funcargs", {}).values():
            if isinstance(value, Env) and value not in run.envs:
                run.register(value)
    if run is not None and run.flag is not None and call.excinfo is not None:
        report._simcord_exceptions = tuple(_leaves(call.excinfo.value))
    if report.when == "call" and report.failed:
        run = ACTIVE_RUN.get()
        envs = (
            run.envs
            if run is not None
            else [v for v in getattr(item, "funcargs", {}).values() if isinstance(v, Env)]
        )
        for env in envs:
            text = env.transcript()
            if text:
                report.sections.append(("simcord transcript", text))
    return report


@pytest.fixture
def simcord_bot():
    raise pytest.UsageError(
        "Define a `simcord_bot` fixture in your conftest.py that returns your bot to use the `simcord_env` fixture."
    )


if pytest_asyncio is not None:

    @pytest_asyncio.fixture
    async def simcord_env(simcord_bot, request):
        marker = request.node.get_closest_marker("simcord")
        options = marker.kwargs if marker else {}
        env = Env(simcord_bot, **options)
        await env.start()
        try:
            yield env
        finally:
            await env.shutdown()
            if env.check_errors:
                env._raise_errors_since(env._errors_acknowledged)
