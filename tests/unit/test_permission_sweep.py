"""The shipped plugin runs complete pytest lifecycles, not HTTP fault scripts."""

from __future__ import annotations

import pytest

BOT = """
import discord
import pytest
from discord.ext import commands

@pytest.fixture
def simcord_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    @bot.event
    async def on_message(message):
        if message.author.bot:
            return
        HANDLER
    return bot
"""


@pytest.mark.parametrize("creation", ["run", "direct", "fixture"])
def test_sweep_finds_unhandled_forbidden_with_fresh_lifecycles(pytester, creation):
    pytester.makeconftest(BOT.replace("HANDLER", 'await message.channel.send("reply")'))
    setup = """
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        await alice.send(channel, "hello")
        assert channel.last_message.content == "reply"
"""
    if creation == "run":
        body = "async with simcord.run(simcord_bot) as env:\n" + setup
        signature = "simcord_bot, lifecycle"
    elif creation == "direct":
        body = (
            "env = simcord.Env(simcord_bot)\nawait env.start()\ntry:\n"
            + setup
            + "\nfinally:\n    await env.shutdown()"
        )
        signature = "simcord_bot, lifecycle"
    else:
        body = "env = simcord_env\n" + "\n".join(line[8:] for line in setup.strip("\n").splitlines())
        signature = "simcord_env, lifecycle"
    pytester.makepyfile(
        "import simcord\nimport pytest\nfrom pathlib import Path\n\n"
        "@pytest.fixture\ndef lifecycle():\n"
        "    with Path('lifecycle').open('a') as log: log.write('setup\\n')\n"
        "    yield\n"
        "    with Path('lifecycle').open('a') as log: log.write('teardown\\n')\n\n"
        f"async def test_bot({signature}):\n" + "\n".join("    " + line for line in body.splitlines())
    )
    result = pytester.runpytest_subprocess(
        "-o", "asyncio_mode=auto", "--simcord-sweep", "--simcord-observed-permissions"
    )
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "*simcord observed permissions*",
            "*send_messages*",
            "*simcord permission sweep*",
            "*[[]send_messages]: unhandled Forbidden*source: bot callback/task*",
        ]
    )
    assert (pytester.path / "lifecycle").read_text().splitlines() == ["setup", "teardown"] * 3


@pytest.mark.parametrize(
    "handler",
    [
        'try:\n            await message.channel.send("reply")\n        except discord.Forbidden:\n            pass',
        'if message.channel.permissions_for(message.guild.me).send_messages:\n            await message.channel.send("reply")',
    ],
)
def test_sweep_ignores_handled_forbidden_and_driver_assertions(pytester, handler):
    pytester.makeconftest(BOT.replace("HANDLER", handler))
    pytester.makepyfile("""
        async def test_bot(simcord_env):
            env = simcord_env
            guild = env.create_guild()
            channel = guild.create_text_channel("general")
            alice = guild.add_member(env.create_user("alice"))
            await alice.send(channel, "hello")
            assert channel.last_message.content == "reply"
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*[[]send_messages]: no unhandled bot errors", "*0 unhandled bot error(s)*"])


def test_sweep_keeps_teardown_bot_errors_after_driver_assertion(pytester):
    pytester.makeconftest(
        BOT.replace(
            "HANDLER",
            """if message.content == "late":
            await message.channel.send("reply")
        else:
            try:
                await message.channel.send("reply")
            except discord.Forbidden:
                pass""",
        )
        + """
import pytest_asyncio

@pytest_asyncio.fixture
async def late(simcord_env):
    env = simcord_env
    guild = env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(env.create_user("alice"))
    yield channel, alice
    await alice.send(channel, "late")
"""
    )
    pytester.makepyfile("""
        async def test_bot(simcord_env, late):
            channel, alice = late
            await alice.send(channel, "first")
            assert channel.last_message.content == "reply"
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*[[]send_messages]: unhandled Forbidden*source: bot callback/task*"])


def test_sweep_does_not_swallow_real_fixture_teardown_failures(pytester):
    pytester.makeconftest(
        BOT.replace(
            "HANDLER",
            """try:
            await message.channel.send("reply")
        except discord.Forbidden:
            pass""",
        )
        + """
import pytest_asyncio

@pytest_asyncio.fixture
async def broken(simcord_env):
    yield
    channel = simcord_env.guild.channels["general"]
    value = simcord_env.backend.compute_permissions(simcord_env.guild.id, simcord_env.backend.bot_user.id, channel.id)
    if not discord.Permissions(value).send_messages:
        raise RuntimeError("real teardown failure")
"""
    )
    pytester.makepyfile("""
        async def test_bot(simcord_env, broken):
            env = simcord_env
            channel = env.create_guild().create_text_channel("general")
            alice = env.guild.add_member(env.create_user("alice"))
            await alice.send(channel, "hello")
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*unsupported:*real teardown failure*"])


def test_baseline_failure_is_not_swept(pytester):
    pytester.makeconftest(BOT.replace("HANDLER", 'await message.channel.send("reply")'))
    pytester.makepyfile("""
        async def test_bot(simcord_env):
            env = simcord_env
            channel = env.create_guild().create_text_channel("general")
            alice = env.guild.add_member(env.create_user("alice"))
            await alice.send(channel, "hello")
            assert False, "baseline failed"
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*[[]baseline]: baseline not passed; not swept"])


def test_broader_user_fixture_scope_is_reported_unsupported(pytester):
    pytester.makeconftest(
        BOT.replace(
            "@pytest.fixture\ndef simcord_bot", '@pytest.fixture(scope="module")\ndef simcord_bot'
        ).replace("HANDLER", 'await message.channel.send("reply")')
    )
    pytester.makepyfile("""
        async def test_bot(simcord_env):
            env = simcord_env
            channel = env.create_guild().create_text_channel("general")
            alice = env.guild.add_member(env.create_user("alice"))
            await alice.send(channel, "hello")
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        ["*[[]baseline]: unsupported: fixture 'simcord_bot' has unsupported module scope"]
    )


def test_observed_report_unions_direct_env_checks_including_denials(pytester):
    pytester.makepyfile("""
        import discord
        import simcord
        from simcord.backend.models import Overwrite
        from simcord.enums import OverwriteType

        async def test_denied():
            env = simcord.Env(discord.Client(intents=discord.Intents.none()))
            guild = env.create_guild()
            channel = guild.create_text_channel("general")
            env.backend.set_overwrite(channel.id, Overwrite(
                target_id=env.backend.bot_user.id, type=OverwriteType.MEMBER,
                deny=discord.Permissions.send_messages.flag,
            ))
            try:
                env.backend.require_permissions(guild.id, env.backend.bot_user.id, channel.id, "send_messages")
            except simcord.BackendError:
                pass

        async def test_allowed():
            env = simcord.Env(discord.Client(intents=discord.Intents.none()))
            guild = env.create_guild()
            env.backend.require_permissions(guild.id, env.backend.bot_user.id, None, "manage_guild")
            actor = guild.add_member(env.create_user("actor"))
            try:
                env.backend.require_permissions(guild.id, actor.id, None, "manage_roles")
            except simcord.BackendError:
                pass
    """)
    result = pytester.runpytest_subprocess(
        "-o",
        "asyncio_mode=auto",
        "--simcord-observed-permissions",
    )
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*simcord observed permissions*", "*manage_guild*send_messages*"])
    assert "manage_roles" not in result.stdout.str()


def test_sweep_reports_permission_dependent_bot_timeout_with_source(pytester):
    pytester.makeconftest(
        BOT.replace(
            "HANDLER",
            """if not message.channel.permissions_for(message.guild.me).send_messages:
            raise TimeoutError("permission-dependent wait")
        await message.channel.send("reply")""",
        )
    )
    pytester.makepyfile("""
        async def test_bot(simcord_env):
            env = simcord_env
            channel = env.create_guild().create_text_channel("general")
            alice = env.guild.add_member(env.create_user("alice"))
            await alice.send(channel, "hello")
            assert channel.last_message.content == "reply"
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "*[[]send_messages]: unhandled TimeoutError: permission-dependent wait*conftest.py:*on_message*",
            "*2 exercised revocation case(s)*1 unhandled bot error(s)*0 unsupported case(s)*",
        ]
    )


def test_shared_guild_grant_is_explicitly_unsupported_not_falsely_revoked(pytester):
    pytester.makepyfile("""
        import discord
        import simcord

        async def test_bot():
            env = simcord.Env(discord.Client(intents=discord.Intents.none()))
            guild = env.create_guild()
            env.backend.require_permissions(guild.id, env.backend.bot_user.id, None, "change_nickname")
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(
        [
            "*[[]change_nickname]: unsupported:*guild-scoped flag granted by shared @everyone*",
            "*0 exercised revocation case(s)*1 unsupported case(s)*",
        ]
    )


def test_xdist_aggregates_observed_flags_and_sweep_findings(pytester):
    pytest.importorskip("xdist")
    pytester.makeconftest(BOT.replace("HANDLER", 'await message.channel.send("reply")'))
    pytester.makepyfile("""
        async def test_send(simcord_env):
            env = simcord_env
            channel = env.create_guild().create_text_channel("general")
            alice = env.guild.add_member(env.create_user("alice"))
            await alice.send(channel, "hello")
            assert channel.last_message.content == "reply"

        async def test_other_flag(simcord_env):
            env = simcord_env
            guild = env.create_guild()
            env.backend.require_permissions(guild.id, env.backend.bot_user.id, None, "manage_guild")
    """)
    result = pytester.runpytest_subprocess(
        "-n",
        "2",
        "-o",
        "asyncio_mode=auto",
        "--simcord-sweep",
        "--simcord-observed-permissions",
    )
    result.assert_outcomes(passed=2)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "*simcord observed permissions*",
            "*manage_guild*send_messages*",
            "*simcord permission sweep*",
            "*[[]send_messages]: unhandled Forbidden*",
        ]
    )


@pytest.mark.parametrize("operation", ["send", "wrapped-fetch", "wrapped-assertion", "test-helper"])
def test_direct_application_errors_are_distinct_from_test_helpers(pytester, operation):
    pytester.makeini("[pytest]\nasyncio_mode=auto\ntestpaths=tests")
    pytester.makeconftest(BOT.replace("HANDLER", "pass"))
    pytester.makepyfile(
        application="""
        import discord

        class HistoryUnavailable(Exception):
            pass

        async def announce(channel, message_id):
            await channel.send("published")

        async def fetch(channel, message_id, error_type=HistoryUnavailable):
            try:
                await channel.fetch_message(message_id)
            except discord.HTTPException as error:
                raise error_type("history inaccessible") from error
    """
    )
    body = {
        "send": "await application.announce(cached, seed.id)",
        "wrapped-fetch": "await application.fetch(cached, seed.id)",
        "wrapped-assertion": "await application.fetch(cached, seed.id, AssertionError)",
        "test-helper": 'await cached.send("test setup")',
    }[operation]
    pytester.mkdir("tests")
    pytester.makepyfile(
        **{
            "tests/test_consumer": f"""
        import application

        async def test_consumer(simcord_env):
            env = simcord_env
            channel = env.create_guild().create_text_channel("general")
            actor = env.guild.add_member(env.create_user("reader"))
            seed = await actor.send(channel, "source")
            cached = env.bot.get_channel(channel.id)
            {body}
    """
        }
    )
    result = pytester.runpytest_subprocess("--simcord-sweep")
    result.assert_outcomes(passed=1)
    if operation == "test-helper":
        assert result.ret == pytest.ExitCode.OK
        result.stdout.fnmatch_lines(
            [
                "*[[]read_messages]: unsupported:*",
                "*[[]send_messages]: unsupported:*",
                "*0 unhandled bot error(s)*",
            ]
        )
    else:
        assert result.ret == pytest.ExitCode.TESTS_FAILED
        kind = {
            "send": "Forbidden",
            "wrapped-fetch": "HistoryUnavailable",
            "wrapped-assertion": "AssertionError",
        }[operation]
        result.stdout.fnmatch_lines([f"*unhandled {kind}:*application.py:*"])


def test_dynamic_role_sharing_stops_unsupported_rerun_and_tears_down(pytester):
    pytester.makeconftest(
        BOT.replace("HANDLER", "pass")
        + """
from pathlib import Path

@pytest.fixture
def lifecycle():
    yield
    with Path("teardowns").open("a") as log:
        log.write("teardown\\n")
"""
    )
    pytester.makepyfile("""
        from pathlib import Path

        async def test_sharing(simcord_env, lifecycle):
            backend = simcord_env.backend
            guild = simcord_env.create_guild()
            actor = guild.add_member(simcord_env.create_user("actor"))
            role_id = backend.get_guild(guild.id).members[backend.bot_user.id].role_ids[0]
            backend.has_permission_flags(guild.id, backend.bot_user.id, None, "manage_guild")
            backend.add_member_role(guild.id, actor.id, role_id)
            with Path("continued").open("a") as log:
                log.write("continued\\n")
    """)
    result = pytester.runpytest_subprocess("-o", "asyncio_mode=auto", "--simcord-sweep")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(
        ["*[[]manage_guild]: unsupported:*shares a grant*", "*0 exercised revocation case(s)*"]
    )
    assert (pytester.path / "continued").read_text().splitlines() == ["continued"]
    assert (pytester.path / "teardowns").read_text().splitlines() == ["teardown", "teardown"]


def test_outcome_boundaries_use_status_not_detail():
    import json
    from types import SimpleNamespace

    from simcord.pytest_plugin import (
        _DIAGNOSTICS,
        _outcome,
        pytest_sessionfinish,
        pytest_terminal_summary,
        pytest_testnodedown,
    )

    outcomes = [
        _outcome("one", "send_messages", "finding", "renamed finding"),
        _outcome("one", "send_messages", "finding", "another error"),
        _outcome("two", "manage_guild", "unsupported", "unhandled misleading prose"),
        _outcome("three", "embed_links", "clean", "unsupported: misleading prose"),
    ]
    state = {"observed": 0, "sweeps": json.loads(json.dumps(outcomes))}
    config = SimpleNamespace(stash={_DIAGNOSTICS: state}, getoption=lambda name: True)
    node = SimpleNamespace(config=config, gateway=SimpleNamespace(id="gw0"))
    pytest_testnodedown(node, None)
    session = SimpleNamespace(config=config, exitstatus=pytest.ExitCode.OK)
    pytest_sessionfinish(session, pytest.ExitCode.OK)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    lines = []
    reporter = SimpleNamespace(section=lambda text: None, write_line=lines.append)
    pytest_terminal_summary(reporter, session.exitstatus, config)
    assert "(no flags received)" in lines
    assert "Incomplete coverage: worker diagnostic results unavailable." in lines
    assert lines[-1] == "2 exercised revocation case(s); 2 unhandled bot error(s); 2 unsupported case(s)"
