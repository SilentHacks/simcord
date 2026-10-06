import json
from pathlib import Path

import pytest

from simcord.backend.errors import SetupError
from simcord.enums import ChannelType, OptionType
from simcord.interactions import (
    OptionError,
    check_options,
    parse_option_input,
    validate_option_value,
)

OPTION_CASES = json.loads((Path(__file__).parents[1] / "fixtures/commands/option_cases.json").read_text())


@pytest.mark.parametrize("case", OPTION_CASES, ids=lambda case: case["name"])
def test_shared_option_input_cases(case):
    expected = case["expect"]
    if "code" in expected:
        with pytest.raises(OptionError) as exc:
            parse_option_input("test", case["option"], case["raw"])
        assert exc.value.code == expected["code"]
    else:
        assert parse_option_input("test", case["option"], case["raw"]) == expected["value"]


def test_entity_kind_mismatch_has_structured_error(env):
    role = env.guild.create_role("reviewer")
    with pytest.raises(OptionError) as exc:
        validate_option_value("assign", {"name": "member", "type": OptionType.USER}, role)
    assert exc.value.code == "option-entity"
    assert exc.value.option == "member"


def test_channel_type_filter_mismatch(env, channel):
    voice = env.guild.create_voice_channel("voice")
    option = {"name": "destination", "type": OptionType.CHANNEL, "channel_types": [ChannelType.TEXT]}
    with pytest.raises(OptionError) as exc:
        validate_option_value("move", option, voice)
    assert exc.value.code == "option-channel-type"
    assert validate_option_value("move", option, channel) is channel


@pytest.mark.parametrize(
    ("file_types", "filename", "accepted"),
    [
        (["IMAGE"], "picture.JpEg", True),
        ([".PNG"], "picture.png", True),
        (["audio"], "sound.OPUS", True),
        (["video"], "clip.webm", True),
        (["image"], "document.pdf", False),
        ([".png"], "picture.jpg", False),
        (["image"], "no-extension", False),
    ],
)
def test_attachment_file_type_filter(file_types, filename, accepted):
    option = {"name": "upload", "type": OptionType.ATTACHMENT, "file_types": file_types}
    if accepted:
        assert validate_option_value("upload", option, (filename, bytearray(b"data"))) == (
            filename,
            b"data",
        )
    else:
        with pytest.raises(OptionError) as exc:
            validate_option_value("upload", option, (filename, b"data"))
        assert exc.value.code == "option-file-type"


def test_non_leaf_command_is_rejected():
    with pytest.raises(OptionError) as exc:
        check_options("config", {"options": [{"name": "set", "type": OptionType.SUBCOMMAND}]}, {})
    assert exc.value.code == "command-not-leaf"


def test_required_option_is_rejected():
    with pytest.raises(OptionError) as exc:
        check_options(
            "find", {"options": [{"name": "query", "type": OptionType.STRING, "required": True}]}, {}
        )
    assert exc.value.code == "option-required"
    assert exc.value.option == "query"


def test_unknown_option_keeps_declared_options_note():
    with pytest.raises(OptionError) as exc:
        check_options("find", {"options": [{"name": "query", "type": OptionType.STRING}]}, {"other": "x"})
    assert exc.value.code == "option-unknown"
    assert any("Declared options: ['query']" in note for note in exc.value.__notes__)


def test_partial_options_drop_invalid_values_but_preserve_valid_order():
    spec = {
        "options": [
            {"name": "limit", "type": OptionType.INTEGER, "min_value": 1},
            {"name": "query", "type": OptionType.STRING},
        ]
    }
    assert check_options("find", spec, {"limit": 0, "query": "simcord"}, partial=True) == {"query": "simcord"}
    with pytest.raises(OptionError) as exc:
        check_options("find", spec, {"unexpected": "x"}, partial=True)
    assert exc.value.code == "option-unknown"


def test_option_error_is_setup_error():
    error = OptionError("option-type", "flag", "Expected a boolean")
    assert isinstance(error, SetupError)
    assert error.code == "option-type"
    assert error.option == "flag"
