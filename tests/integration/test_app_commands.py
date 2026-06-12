import pytest

import simcord as dpt


async def test_subcommand_group(env, channel, alice):
    result = await alice.slash(channel, "config set", key="lang", value="en")
    assert result.response.content == "lang=en"


async def test_unknown_subcommand_is_caught(env, channel, alice):
    with pytest.raises(dpt.SetupError, match="no subcommand path"):
        await alice.slash(channel, "config unset", key="lang")


async def test_user_context_menu(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    result = await alice.context_menu(channel, "Report", bob)
    assert result.ephemeral
    assert result.response.content == "Reported bob"


async def test_autocomplete(env, channel, alice):
    choices = await alice.autocomplete(channel, "tag", "name", "py")
    assert [c["value"] for c in choices] == ["python", "pytest"]

    result = await alice.slash(channel, "tag", name="pytest")
    assert result.response.content == "Tag: pytest"


async def test_modal_flow(env, channel, alice):
    shown = await alice.slash(channel, "feedback")
    assert shown.modal is not None

    submitted = await alice.submit_modal(shown, {"name": "Alice"})
    assert submitted.response.content == "Thanks Alice"


async def test_select_menu(env, channel, alice):
    result = await alice.slash(channel, "color")

    picked = await alice.select(result.response.message, ["green"], custom_id="color")
    assert picked.response.content == "Picked green"


async def test_select_invalid_value_rejected(env, channel, alice):
    result = await alice.slash(channel, "color")
    with pytest.raises(dpt.SetupError, match="does not exist"):
        await alice.select(result.response.message, ["purple"], custom_id="color")
