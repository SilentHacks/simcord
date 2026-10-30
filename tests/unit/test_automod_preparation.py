from copy import deepcopy

import pytest

from simcord.backend import Backend, serializers
from simcord.http import router


def _world(keywords):
    backend = Backend()
    guild = backend.create_guild("AutoMod")
    body = {
        "name": "Keywords",
        "event_type": 1,
        "trigger_type": 1,
        "trigger_metadata": {"keyword_filter": keywords, "extra": {"values": ["original"]}},
        "actions": [{"type": 1, "metadata": {"custom_message": "blocked", "extra": ["original"]}}],
    }
    rule = backend.create_auto_mod_rule(guild.id, backend.bot_user.id, body)
    return backend, guild, rule, body


@pytest.mark.parametrize("edit", [False, True])
def test_nested_inputs_and_payloads_are_independent(edit):
    backend, guild, rule, body = _world(["original"])
    if edit:
        body["trigger_metadata"]["keyword_filter"] = ["replacement"]
        backend.edit_auto_mod_rule(guild.id, rule.id, body)
    expected = deepcopy(body)
    first = serializers.auto_mod_rule_payload(backend, rule)
    second = serializers.auto_mod_rule_payload(backend, rule)
    body["trigger_metadata"]["keyword_filter"][:] = ["caller"]
    body["trigger_metadata"]["extra"]["values"].append("caller")
    body["actions"][0]["metadata"]["extra"].append("caller")
    first["trigger_metadata"]["keyword_filter"][:] = ["consumer"]
    first["trigger_metadata"]["extra"]["values"].append("consumer")
    first["actions"][0]["metadata"]["extra"].append("consumer")
    for payload in (second, serializers.auto_mod_rule_payload(backend, rule)):
        assert payload["trigger_metadata"] == expected["trigger_metadata"]
        assert payload["actions"] == expected["actions"]
    keyword = expected["trigger_metadata"]["keyword_filter"][0]
    assert backend._auto_mod_match(rule, keyword) == keyword
    assert backend._auto_mod_match(rule, "caller consumer") is None


@pytest.mark.parametrize("method", ["POST", "PATCH", "GET", "LIST"])
def test_http_responses_logs_and_events_do_not_alias_rules(method):
    backend, guild, rule, body = _world(["original"])
    path = f"/guilds/{guild.id}/auto-moderation/rules"
    if method in ("PATCH", "GET"):
        path += f"/{rule.id}"
    response = router.dispatch(
        backend,
        "GET" if method == "LIST" else method,
        path,
        json=body if method in ("POST", "PATCH") else None,
    )
    if method == "LIST":
        response = response[0]
    stored = backend.get_auto_mod_rule(guild.id, int(response["id"]))
    history = deepcopy(backend.transcript)
    request = deepcopy(backend.http_requests[-1].json)
    body["trigger_metadata"]["keyword_filter"][:] = ["caller"]
    body["actions"][0]["metadata"]["extra"].append("caller")
    response["trigger_metadata"]["keyword_filter"][:] = ["consumer"]
    response["actions"][0]["metadata"]["extra"].append("consumer")
    assert stored.trigger_metadata["keyword_filter"] == ["original"]
    assert stored.actions[0]["metadata"]["extra"] == ["original"]
    assert backend.transcript == history
    assert backend.http_requests[-1].json == request
    assert backend._auto_mod_match(stored, "original") == "original"
    assert backend._auto_mod_match(stored, "caller consumer") is None


def test_failed_ingress_copy_leaves_rule_unchanged():
    backend, guild, rule, body = _world(["original"])
    before = serializers.auto_mod_rule_payload(backend, rule)
    history = deepcopy(backend.transcript)

    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("cannot copy input")

    with pytest.raises(RuntimeError, match="cannot copy input"):
        backend.edit_auto_mod_rule(
            guild.id,
            rule.id,
            {
                "name": "changed",
                "trigger_metadata": {"keyword_filter": ["changed"]},
                "actions": [Uncopyable()],
            },
        )
    assert serializers.auto_mod_rule_payload(backend, rule) == before
    assert backend.transcript == history
    assert backend._auto_mod_match(rule, "original") == "original"
    with pytest.raises(RuntimeError, match="cannot copy input"):
        backend.create_auto_mod_rule(guild.id, backend.bot_user.id, {**body, "actions": [Uncopyable()]})
    assert list(guild.auto_mod_rules) == [rule.id]
    assert backend.transcript == history


@pytest.mark.parametrize("edit", [False, True])
def test_event_delivery_failure_does_not_roll_back_committed_rule(edit):
    backend, guild, rule, body = _world(["original"])
    body["trigger_metadata"]["keyword_filter"] = ["committed"]

    def fail_delivery(event, payload):
        raise RuntimeError("delivery failed")

    backend.subscribers.append(fail_delivery)
    with pytest.raises(RuntimeError, match="delivery failed"):
        if edit:
            backend.edit_auto_mod_rule(guild.id, rule.id, body)
        else:
            backend.create_auto_mod_rule(guild.id, backend.bot_user.id, body)
    committed = rule if edit else list(guild.auto_mod_rules.values())[-1]
    assert backend.get_auto_mod_rule(guild.id, committed.id) is committed
    assert backend._auto_mod_match(committed, "committed") == "committed"
    assert backend._auto_mod_match(committed, "original") is None


@pytest.mark.parametrize(
    ("keywords", "content", "expected"),
    [
        (["later", "earlier"], "earlier then later", "later"),
        (["spam"], "spammer", None),
        (["spam"], "éSPAMé", "spam"),
        (["spam"], "1spam", None),
        (["spam"], "spam1", None),
        (["spam*"], "spammer", "spam*"),
        (["spam*"], "antispam", None),
        (["*spam"], "antispam", "*spam"),
        (["*spam"], "spammer", None),
        (["*spam*"], "antispammer", "*spam*"),
        (["**spam**"], "antispammer", "**spam**"),
        (["s"], "\N{LATIN SMALL LETTER LONG S}", "s"),
        (["k"], "\N{KELVIN SIGN}", "k"),
        (["\N{LATIN SMALL LETTER LONG S}"], "S", "\N{LATIN SMALL LETTER LONG S}"),
        (["\N{KELVIN SIGN}"], "K", "\N{KELVIN SIGN}"),
        (["i"], "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}", "i"),
        (["i"], "\N{LATIN SMALL LETTER DOTLESS I}", "i"),
        (["spam"], "xSPAMx", None),
        (["spam"], "xSPAMx then SPAM!", "spam"),
        (["spam", "spam", "*spam*"], "xSPAMx", "*spam*"),
        (["a*b"], "a*b", "a*b"),
        (["a*b"], "axxb", None),
        (["*a*b*"], "xa*by", "*a*b*"),
        (["", "*", "**", "spam"], "spam", "spam"),
        (["missing", "last"], "last", "last"),
        (["missing", "last"], "clean", None),
    ],
)
def test_keyword_matching_preserves_order_wildcards_and_boundaries(keywords, content, expected):
    backend, _, rule, _ = _world(keywords)
    assert backend._auto_mod_match(rule, content) == expected
    assert backend._auto_mod_match(rule, content) == expected


def test_internal_edits_and_separate_live_rules_keep_matching_current():
    backend, guild, first, body = _world(["original"])
    second = backend.create_auto_mod_rule(guild.id, backend.bot_user.id, body)
    for rule in (first, second):
        assert backend._auto_mod_match(rule, "original") == "original"
    first.trigger_metadata["keyword_filter"][:] = ["inplace"]
    assert backend._auto_mod_match(first, "original") is None
    assert backend._auto_mod_match(first, "inplace") == "inplace"
    assert backend._auto_mod_match(second, "original") == "original"
    first.trigger_metadata = {"keyword_filter": ["replacement"]}
    assert backend._auto_mod_match(first, "inplace") is None
    assert backend._auto_mod_match(first, "replacement") == "replacement"
    second.trigger_metadata["keyword_filter"] = ["separate"]
    assert backend._auto_mod_match(second, "original") is None
    assert backend._auto_mod_match(second, "separate") == "separate"
    assert backend._auto_mod_match(first, "replacement") == "replacement"


class _RegexOnlyString(str):
    def isascii(self):
        raise AssertionError("subclass must use regex matching")

    def lower(self):
        raise AssertionError("subclass must use regex matching")


@pytest.mark.parametrize("subclass_keyword", [False, True])
@pytest.mark.parametrize("subclass_content", [False, True])
def test_keyword_matching_keeps_string_subclass_fallback(subclass_keyword, subclass_content):
    keyword = _RegexOnlyString("spam") if subclass_keyword else "spam"
    backend, _, rule, _ = _world([keyword])
    for content, expected in [("clean", None), ("xSPAMx", None), ("SPAM!", keyword)]:
        if subclass_content:
            content = _RegexOnlyString(content)
        assert backend._auto_mod_match(rule, content) == expected


def test_literal_misses_keep_unicode_and_later_hits_current():
    backend, _, rule, _ = _world(["*s*", "k"])
    for content, expected in [
        ("clean", None),
        ("\N{LATIN SMALL LETTER LONG S}", "*s*"),
        ("S", "*s*"),
        ("\N{KELVIN SIGN}", "k"),
        ("K", "k"),
        ("clean", None),
    ]:
        assert backend._auto_mod_match(rule, content) == expected
    rule.trigger_metadata["keyword_filter"][:] = ["new"]
    assert backend._auto_mod_match(rule, "K") is None
    assert backend._auto_mod_match(rule, "NEW") == "new"


def test_matching_keyword_does_not_prepare_unvisited_tail():
    class UnpreparableKeyword(str):
        def strip(self, chars=None):
            raise RuntimeError("cannot prepare keyword")

    backend, _, rule, _ = _world(["first", UnpreparableKeyword("later")])
    assert backend._auto_mod_match(rule, "FIRST") == "first"
    with pytest.raises(RuntimeError, match="cannot prepare keyword"):
        backend._auto_mod_match(rule, "clean")


def test_deferred_regex_keeps_its_owner_after_equal_keyword_replacement():
    class DifferentBoundaries(str):
        def startswith(self, *args, **kwargs):
            return True

        def endswith(self, *args, **kwargs):
            return True

    backend, _, rule, _ = _world(["word"])
    assert backend._auto_mod_match(rule, "clean") is None
    rule.trigger_metadata["keyword_filter"][:] = [DifferentBoundaries("word")]
    assert backend._auto_mod_match(rule, "swordé") is None
    assert backend._auto_mod_match(rule, "WORD!") == "word"
