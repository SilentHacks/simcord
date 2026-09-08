import pytest

from simcord.components import (
    COMPONENTS_V2_FLAG,
    ComponentValidationError,
    component_mentions,
    resolve_attachment_references,
    validate_components,
    validate_message_state,
    validate_modal,
    walk_components,
)

V2 = COMPONENTS_V2_FLAG
MAX_ID = (1 << 32) - 1


def button(style=1, custom_id="button", **extra):
    value = {"type": 2, "style": style}
    if custom_id is not None:
        value["custom_id"] = custom_id
    value.update(extra)
    return value


def row(*children):
    return {"type": 1, "components": list(children)}


def text(content="text", **extra):
    value = {"type": 10, "content": content}
    value.update(extra)
    return value


def select(kind=3, custom_id="select", **extra):
    value = {"type": kind, "custom_id": custom_id}
    value.update(extra)
    return value


def section(accessory=None, children=None, **extra):
    children = [text("section")] if children is None else children
    value = {
        "type": 9,
        "components": list(children),
        "accessory": accessory or button(custom_id="section-button"),
    }
    value.update(extra)
    return value


def valid_thumbnail(url="https://cdn.test/thumb.png", **extra):
    value = {"type": 11, "media": {"url": url}}
    value.update(extra)
    return value


def valid_gallery(url="https://cdn.test/gallery.png", **extra):
    value = {"type": 12, "items": [{"media": {"url": url}}]}
    value.update(extra)
    return value


def valid_file(url="https://cdn.test/file.bin", **extra):
    value = {"type": 13, "file": {"url": url}}
    value.update(extra)
    return value


def modal_control(kind, custom_id="control", **extra):
    value = {"type": kind, "custom_id": custom_id}
    value.update(extra)
    return value


def modal(*components, custom_id="modal", title="Modal"):
    return {"custom_id": custom_id, "title": title, "components": list(components)}


def label(component, text="Control", **extra):
    value = {"type": 18, "label": text, "component": component}
    value.update(extra)
    return value


@pytest.mark.parametrize(
    "component",
    [
        {"type": 10, "content": "intro"},
        label(modal_control(4, "name")),
        label(modal_control(3, "choice", options=[{"label": "A", "value": "a"}])),
        label(modal_control(5, "user")),
        label(modal_control(19, "files", min_values=0, max_values=2)),
        label(
            modal_control(
                21,
                "radio",
                options=[{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
            )
        ),
        label(modal_control(22, "checks", options=[{"label": "A", "value": "a"}])),
        label(modal_control(23, "accepted", default=True)),
        {"type": 1, "components": [modal_control(4, "row-text")]},
    ],
)
def test_modal_definitions_accept_wrappers_and_normalize_ids(component):
    original = modal(component)
    normalized = validate_modal(original)

    assert normalized["custom_id"] == "modal"
    assert [item["type"] for item in walk_components(normalized["components"])] == [
        item["type"] for item in walk_components(original["components"])
    ]
    assert all(item.get("id", 1) >= 1 for item in walk_components(normalized["components"]))
    assert all("id" not in item for item in walk_components(original["components"]))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "modal: must be an object"),
        ({}, "custom_id: must be a string"),
        ({"custom_id": "", "title": "T", "components": []}, "custom_id: must be at least 1"),
        ({"custom_id": "x", "title": "", "components": []}, "title: must be at least 1"),
        ({"custom_id": "x", "title": "T", "components": None}, "components"),
        ({"custom_id": "x", "title": "T", "components": [None]}, "must be an object"),
        (modal(modal_control(4)), "wrapped by a label or action row"),
        (modal({"type": 18, "label": "x"}), "component: must be an object"),
        (modal(label({"type": 10, "content": "bad"})), "must contain one modal control"),
        (modal({"type": 18, "label": "x" * 46, "component": modal_control(4)}), "label"),
        (modal(label(modal_control(4), description="x" * 101)), "description"),
        (modal({"type": 1, "components": []}), "exactly one control"),
        (modal({"type": 1, "components": [None]}), "must be an object"),
        (modal({"type": 1, "components": [modal_control(10)]}), "must contain one modal control"),
        (modal(label(modal_control(4, "same")), label(modal_control(4, "same"))), "not unique"),
        (modal(label(modal_control(4, id=-1))), "id"),
        (modal(label(modal_control(4, id=True))), "positive 32-bit"),
        (modal(label(modal_control(4, id=1)), label(modal_control(4, id=1))), "not unique"),
    ],
)
def test_modal_definition_shape_errors_are_reported(payload, message):
    raises(message, validate_modal, payload)


@pytest.mark.parametrize(
    ("control", "message"),
    [
        (modal_control(4, "text", style=True), "style"),
        (modal_control(4, "text", label="x" * 46), "label"),
        (modal_control(4, "text", placeholder="x" * 101), "placeholder"),
        (modal_control(4, "text", default=1), "default"),
        (modal_control(4, "text", min_length=True), "min_length"),
        (modal_control(4, "text", max_length=0), "max_length"),
        (modal_control(4, "text", min_length=3, max_length=2), "cannot exceed"),
        (modal_control(3, "select", required="yes"), "required"),
        (modal_control(3, "select", min_values=True), "min_values"),
        (modal_control(3, "select", max_values=0), "max_values"),
        (modal_control(3, "select", min_values=2, max_values=1), "cannot exceed"),
        (modal_control(3, "select", max_values=26), "max_values"),
        (modal_control(19, "upload", max_values=11), "max_values"),
        (
            modal_control(
                22,
                "checks",
                options=[{"label": str(index), "value": str(index)} for index in range(10)],
                max_values=11,
            ),
            "max_values",
        ),
        (modal_control(3, "select", disabled="no"), "disabled"),
        (modal_control(3, "select", placeholder="x" * 151), "placeholder"),
        (modal_control(3, "select", options=None), "between 1 and 25"),
        (modal_control(21, "radio", options=[{"label": "a", "value": "a"}]), "between 2 and 10"),
        (modal_control(22, "checks", options=[{"label": "a", "value": "a", "default": "yes"}]), "default"),
        (modal_control(23, "check", default="yes"), "default"),
    ],
)
def test_modal_control_constraints_are_validated(control, message):
    raises(message, validate_modal, modal(label(control)))


def test_modal_options_validate_optional_fields_and_duplicates():
    valid = modal(
        label(
            modal_control(
                3,
                "choice",
                options=[
                    {
                        "label": "A",
                        "value": "a",
                        "description": None,
                        "emoji": None,
                        "default": False,
                    }
                ],
            )
        )
    )
    assert validate_modal(valid)["components"][0]["component"]["custom_id"] == "choice"
    for options, message in [
        ([None], "must be an object"),
        ([{"label": "A", "value": "a"}, {"label": "B", "value": "a"}], "not unique"),
        ([{"label": "A", "value": "a", "description": "x" * 101}], "description"),
        ([{"label": "A", "value": "a", "emoji": "bad"}], "emoji"),
        ([{"label": "A", "value": "a", "default": "bad"}], "default"),
    ]:
        raises(message, validate_modal, modal(label(modal_control(3, "choice", options=options))))


def validate_v2(*components):
    return validate_components(list(components), flags=V2)


def raises(match, fn, *args, **kwargs):
    with pytest.raises(ComponentValidationError, match=match):
        fn(*args, **kwargs)


def test_walk_components_traverses_all_nested_wire_keys_and_skips_scalars():
    tree = {
        "type": 17,
        "components": [{"type": 10, "content": "body"}, "ignored"],
        "accessory": {"type": 11, "media": {"url": "thumb"}},
        "component": {"type": 10, "content": "modal"},
    }

    assert [item["type"] for item in walk_components(tree)] == [17, 10, 11, 10]
    assert [item["type"] for item in walk_components([tree, None])] == [17, 10, 11, 10]


def test_component_mentions_collects_nested_text_displays():
    components = [section(children=[text("first")]), {"type": 10, "content": "second"}]
    assert component_mentions(components) == "first\nsecond"


@pytest.mark.parametrize(
    ("components", "message"),
    [
        (None, "must be an array"),
        ({}, "must be an array"),
        ([None], "must be an object"),
        ([{"type": True}], "type is required"),
        ([{"type": 15}], "unsupported type 15"),
        ([{"type": 2, "style": 1, "custom_id": "x"}], "only action rows"),
    ],
)
def test_rejects_malformed_or_legacy_top_level_components(components, message):
    raises(message, validate_components, components)


def test_legacy_rows_are_limited_and_normalized_to_zero_ids():
    components = [row(button(custom_id=f"b-{index}")) for index in range(5)]
    normalized = validate_components(components)
    assert all(item["id"] == 0 for item in walk_components(normalized))
    assert all("id" not in item for item in walk_components(components))
    raises("at most 5 action rows", validate_components, [*components, row(button(custom_id="six"))])


def test_v2_requires_flag_and_cannot_mix_legacy_interactive_components():
    raises("require the components_v2 message flag", validate_components, [text()])
    raises(
        "cannot be mixed with V2 layouts",
        validate_components,
        [text(), button(custom_id="legacy")],
        flags=V2,
    )


@pytest.mark.parametrize("bad_id", [True, False, -1, MAX_ID + 1, "1"])
def test_v2_ids_must_be_positive_32_bit_integers(bad_id):
    raises("id must be a positive 32-bit integer", validate_v2, text(id=bad_id))


def test_ids_are_unique_and_missing_ids_are_assigned_around_explicit_ids():
    components = [text("first", id=2), text("free"), section(children=[text("nested")], id=4)]
    normalized = validate_v2(*components)
    assert [item["id"] for item in walk_components(normalized)] == [2, 1, 4, 3, 5]
    raises("id 2 is not unique", validate_v2, text(id=2), text("duplicate", id=2))


@pytest.mark.parametrize(
    ("children", "message"),
    [
        (None, "between 1 and 5"),
        ([], "between 1 and 5"),
        ([button(custom_id=str(index)) for index in range(6)], "between 1 and 5"),
        ([None], "must be an object"),
        ([text()], "only buttons and select menus"),
        ([{"type": None}], "type is required"),
    ],
)
def test_action_rows_validate_children(children, message):
    component = {"type": 1}
    if children is not None:
        component["components"] = children
    raises(message, validate_components, [component], flags=V2)
    raises(
        "up to five buttons or exactly one select",
        validate_components,
        [row(select(custom_id="menu"), button(custom_id="extra"))],
        flags=V2,
    )


@pytest.mark.parametrize("style", [None, True, 0, 7, "1"])
def test_buttons_validate_style(style):
    raises("style must be between 1 and 6", validate_components, [row(button(style=style))])


@pytest.mark.parametrize(
    ("component", "message"),
    [
        (button(custom_id=None), "custom_id"),
        (button(custom_id=""), "at least 1"),
        (button(custom_id="x" * 101), "100 or fewer"),
        (button(label=1), "label: must be a string"),
        (button(label="x" * 81), "label: must be 80 or fewer"),
        (button(emoji="not-an-object"), "emoji must be an object"),
    ],
)
def test_regular_buttons_validate_fields(component, message):
    raises(message, validate_components, [row(component)])
    raises("regular buttons cannot have url", validate_components, [row(button(url="https://example.test"))])


def test_buttons_accept_labels_and_emoji_and_reject_duplicate_custom_ids():
    normalized = validate_components([row(button(label="Go", emoji={"name": "rocket"}, custom_id="go"))])
    assert normalized[0]["components"][0]["label"] == "Go"
    assert normalized[0]["components"][0]["emoji"] == {"name": "rocket"}
    raises(
        "custom_id 'same' is not unique",
        validate_components,
        [row(button(custom_id="same"), button(custom_id="same"))],
    )


def test_link_buttons_require_url_and_forbid_custom_id():
    valid = validate_components(
        [row(button(style=5, custom_id=None, url="https://example.test", label="Docs"))]
    )
    assert valid[0]["components"][0]["url"] == "https://example.test"
    raises("url: must be a string", validate_components, [row(button(style=5, custom_id=None))])
    raises("link buttons cannot have custom_id", validate_components, [row(button(style=5, url="u"))])
    raises("url: must be at least 1", validate_components, [row(button(style=5, custom_id=None, url=""))])
    validate_components([section(accessory=valid_thumbnail())], flags=V2)
    raises(
        "custom_id 'menu' is not unique",
        validate_components,
        [
            row(select(custom_id="menu", options=[{"label": "A", "value": "a"}])),
            row(select(custom_id="menu", options=[{"label": "B", "value": "b"}])),
        ],
    )
    validate_modal(modal(label(modal_control(23, "unchecked"))))


@pytest.mark.parametrize("sku_id", [None, True, False, "", []])
def test_premium_buttons_require_a_sku_id(sku_id):
    raises(
        "premium buttons require sku_id",
        validate_components,
        [row(button(style=6, custom_id=None, sku_id=sku_id))],
    )


def test_premium_buttons_accept_numeric_or_string_skus_and_reject_other_fields():
    normalized = validate_components(
        [row(button(style=6, custom_id=None, sku_id=123), button(style=6, custom_id=None, sku_id="sku"))]
    )
    assert [item["sku_id"] for item in normalized[0]["components"]] == [123, "sku"]
    for field in ("custom_id", "url", "label", "emoji"):
        payload = button(style=6, custom_id=None, sku_id="sku")
        payload[field] = None
        raises("premium buttons cannot have", validate_components, [row(payload)])


@pytest.mark.parametrize("kind", [3, 5, 6, 7, 8])
def test_select_types_accept_shared_fields(kind):
    payload = select(kind, custom_id=f"select-{kind}")
    if kind == 3:
        payload["options"] = [{"label": "One", "value": "1", "description": None, "emoji": {"name": "1"}}]
        payload["placeholder"] = None
    normalized = validate_components([row(payload)])
    assert normalized[0]["components"][0]["custom_id"] == f"select-{kind}"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (select(custom_id=None, options=[{"label": "x", "value": "x"}]), "custom_id"),
        (select(custom_id="", options=[{"label": "x", "value": "x"}]), "at least 1"),
        (select(custom_id="x" * 101, options=[{"label": "x", "value": "x"}]), "100 or fewer"),
        (
            select(custom_id="x", placeholder="x" * 151, options=[{"label": "x", "value": "x"}]),
            "150 or fewer",
        ),
        (select(custom_id="x", min_values=True, options=[{"label": "x", "value": "x"}]), "min_values"),
        (select(custom_id="x", min_values=-1, options=[{"label": "x", "value": "x"}]), "min_values"),
        (select(custom_id="x", min_values=26, options=[{"label": "x", "value": "x"}]), "min_values"),
        (select(custom_id="x", max_values=True, options=[{"label": "x", "value": "x"}]), "max_values"),
        (select(custom_id="x", max_values=0, options=[{"label": "x", "value": "x"}]), "max_values"),
        (select(custom_id="x", max_values=26, options=[{"label": "x", "value": "x"}]), "max_values"),
        (
            select(custom_id="x", min_values=2, max_values=1, options=[{"label": "x", "value": "x"}]),
            "cannot exceed",
        ),
    ],
)
def test_select_shared_fields_are_bounded(payload, message):
    raises(message, validate_components, [row(payload)])


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (None, "between 1 and 25"),
        ([], "between 1 and 25"),
        ([{}] * 26, "between 1 and 25"),
        ([None], "must be an object"),
        ([{"label": "", "value": "x"}], "label"),
        ([{"label": "x" * 101, "value": "x"}], "label"),
        ([{"label": "x", "value": ""}], "value"),
        ([{"label": "x", "value": "v" * 101}], "value"),
        ([{"label": "x", "value": "x"}, {"label": "y", "value": "x"}], "not unique"),
        ([{"label": "x", "value": "x", "description": "d" * 101}], "description"),
        ([{"label": "x", "value": "x", "emoji": "bad"}], "emoji must be an object"),
    ],
)
def test_string_select_options_are_validated(options, message):
    raises(message, validate_components, [row(select(options=options))])


def test_text_displays_have_individual_and_total_limits_and_are_deep_copied():
    source = [text("hello", id=None)]
    normalized = validate_v2(*source)
    normalized[0]["content"] = "changed"
    assert source[0]["content"] == "hello"
    assert normalized[0]["id"] == 1
    raises("content: must be a string", validate_v2, text(None))
    raises("content: must be at least 1", validate_v2, text(""))
    raises("content: must be 4000 or fewer", validate_v2, text("x" * 4001))
    raises("4000-character total", validate_v2, text("x" * 2001), text("x" * 2000))


def test_sections_require_text_children_and_button_or_thumbnail_accessory():
    valid = validate_v2(section(children=[text("one"), text("two")]))
    assert [item["type"] for item in walk_components(valid)] == [9, 10, 10, 2]
    for children in (None, [], [text("1"), text("2"), text("3"), text("4")]):
        payload = {"type": 9, "components": children or []}
        raises("sections must contain between 1 and 3", validate_v2, payload)
    raises(
        "section components must be text displays",
        validate_v2,
        {"type": 9, "components": [button()], "accessory": button()},
    )
    for accessory in (None, text("bad"), {"type": 3, "custom_id": "bad"}):
        payload = {"type": 9, "components": [text()], "accessory": accessory}
        raises("sections require a button or thumbnail accessory", validate_v2, payload)


def test_sections_accept_thumbnail_accessories_and_validate_media_description():
    valid = validate_v2(section(accessory=valid_thumbnail(description="x" * 1024)))
    assert valid[0]["accessory"]["type"] == 11
    raises(
        "description: must be 1024 or fewer",
        validate_v2,
        section(accessory=valid_thumbnail(description="x" * 1025)),
    )
    raises("media must be an object", validate_v2, section(accessory={"type": 11, "media": None}))
    raises(
        "media.url: must be at least 1", validate_v2, section(accessory={"type": 11, "media": {"url": ""}})
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"type": 12, "items": None}, "between 1 and 10"),
        ({"type": 12, "items": [{}] * 11}, "between 1 and 10"),
        ({"type": 12, "items": [None]}, "must be an object"),
        ({"type": 12, "items": [{"media": None}]}, "media must be an object"),
        ({"type": 12, "items": [{"media": {"url": "u"}, "description": "x" * 1025}]}, "description"),
    ],
)
def test_media_galleries_validate_items(payload, message):
    raises(message, validate_v2, payload)


def test_files_and_separators_accept_valid_layout_values_and_reject_invalid_values():
    gallery = valid_gallery()
    gallery["items"][0]["description"] = None
    normalized = validate_v2(gallery, valid_file(), {"type": 14}, {"type": 14, "spacing": 2})
    assert [item["type"] for item in normalized] == [12, 13, 14, 14]
    raises("media must be an object", validate_v2, {"type": 13, "file": None})
    raises("file.url: must be a string", validate_v2, {"type": 13, "file": {"url": 1}})
    raises("spacing must be 1 or 2", validate_v2, {"type": 14, "spacing": 0})


@pytest.mark.parametrize(
    "payload",
    [
        section(accessory=valid_thumbnail(spoiler=1)),
        {"type": 12, "items": [{"media": {"url": "u"}, "spoiler": 1}]},
        valid_file(spoiler=1),
        {"type": 14, "divider": 1},
        {"type": 14, "spacing": True},
        {"type": 17, "components": [], "spoiler": 1},
        {"type": 17, "components": [], "accent_color": True},
        {"type": 17, "components": [], "accent_color": -1},
        {"type": 17, "components": [], "accent_color": 0x1000000},
    ],
)
def test_visual_style_fields_reject_wrong_types_and_out_of_range_colors(payload):
    raises("must be", validate_v2, payload)


def test_visual_style_fields_accept_protocol_values():
    normalized = validate_v2(
        section(accessory=valid_thumbnail(spoiler=True)),
        {"type": 12, "items": [{"media": {"url": "u"}, "spoiler": False}]},
        valid_file(spoiler=True),
        {"type": 14, "divider": False, "spacing": 2},
        {"type": 17, "components": [], "accent_color": 0xABCDEF, "spoiler": False},
    )

    assert [component["type"] for component in normalized] == [9, 12, 13, 14, 17]


def test_containers_accept_layout_children_and_reject_invalid_shapes():
    payload = {
        "type": 17,
        "components": [
            row(button(custom_id="nested-button")),
            text("nested text"),
            section(accessory=button(custom_id="nested-section-button")),
            valid_gallery(),
            valid_file(),
            {"type": 14, "spacing": 2},
        ],
    }
    normalized = validate_v2(payload)
    assert [item["type"] for item in walk_components(normalized)] == [17, 1, 2, 10, 9, 10, 2, 12, 13, 14]
    assert validate_v2({"type": 17, "components": []})[0]["id"] == 1
    assert (
        len(validate_v2({"type": 17, "components": [text(str(i)) for i in range(11)]})[0]["components"]) == 11
    )
    raises("must be an array", validate_v2, {"type": 17})
    raises("unsupported child", validate_v2, {"type": 17, "components": [valid_thumbnail()]})
    raises("unsupported child", validate_v2, {"type": 17, "components": [None]})


def test_messages_have_at_most_40_components_and_unique_custom_ids_across_layouts():
    raises("at most 40", validate_v2, *[text(str(index)) for index in range(41)])
    raises(
        "custom_id 'shared' is not unique",
        validate_v2,
        row(button(custom_id="shared")),
        section(accessory=button(custom_id="shared")),
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"content": 1}, "content must be a string"),
        ({"content": "x" * 2001}, "content must be a string"),
        ({"embeds": None}, "embeds must be an array"),
        ({"previous_flags": V2, "flags": 0}, "cannot be removed"),
        ({"flags": V2, "content": "body"}, "cannot contain content"),
        ({"flags": V2, "embeds": [{}]}, "cannot contain content"),
        ({"flags": V2, "poll": {}}, "cannot contain content"),
        ({"flags": V2, "stickers": [1]}, "cannot contain content"),
    ],
)
def test_validate_message_state_enforces_message_level_rules(kwargs, message):
    values = {
        "flags": 0,
        "content": None,
        "embeds": [],
        "poll": None,
        "stickers": None,
        "previous_flags": 0,
    }
    values.update(kwargs)
    components = [text()] if values["flags"] & V2 else [row(button(custom_id="legacy"))]
    raises(message, validate_message_state, components, **values)


def test_validate_message_state_accepts_legacy_and_v2_states():
    legacy = validate_message_state(
        [row(button(custom_id="legacy"))], flags=0, content="hello", embeds=[], poll=None, stickers=None
    )
    assert legacy[0]["id"] == 0
    v2 = validate_message_state([text()], flags=V2, content=None, embeds=[], poll=None, stickers=None)
    assert v2[0]["id"] == 1
    assert validate_message_state([text()], flags=V2, content="", embeds=[], stickers=[])[0]["id"] == 1
    raises(
        "require at least one component",
        validate_message_state,
        [],
        flags=V2,
        content=None,
        embeds=[],
    )


def test_attachment_references_resolve_for_thumbnails_galleries_and_files_without_mutating_input():
    components = [
        section(accessory=valid_thumbnail("attachment://thumb.png")),
        {
            "type": 12,
            "items": [
                {"media": {"url": "attachment://gallery.png"}},
                {"media": {"url": "https://cdn.test/already.png"}},
                {"media": {"url": None}},
            ],
        },
        {"type": 13, "file": {"url": "attachment://file.bin"}},
        {"type": 13, "file": {"url": "https://cdn.test/already.bin", "name": "keep", "size": 9}},
    ]
    attachments = [
        {
            "filename": "thumb.png",
            "url": "https://cdn.test/thumb.png",
            "proxy_url": "https://proxy.test/thumb.png",
            "content_type": "image/png",
            "id": 1,
            "size": 5,
        },
        {
            "filename": "gallery.png",
            "url": "https://cdn.test/gallery.png",
            "proxy_url": "https://proxy.test/gallery.png",
            "id": 2,
            "size": 6,
        },
        {
            "filename": "file.bin",
            "url": "https://cdn.test/file.bin",
            "proxy_url": "https://proxy.test/file.bin",
            "content_type": None,
            "id": 3,
            "size": 7,
        },
    ]
    resolved = resolve_attachment_references(components, attachments)
    assert components[0]["accessory"]["media"]["url"] == "attachment://thumb.png"
    assert resolved[0]["accessory"]["media"]["url"] == "https://cdn.test/thumb.png"
    assert resolved[0]["accessory"]["media"]["attachment_id"] == 1
    assert resolved[1]["items"][0]["media"]["content_type"] is None
    assert resolved[2]["name"] == "file.bin"
    assert resolved[2]["size"] == 7
    assert resolved[3]["file"]["name"] == "keep"
    assert resolved[3]["file"]["size"] == 9


def test_attachment_reference_must_name_an_uploaded_file():
    raises(
        "has no uploaded file",
        resolve_attachment_references,
        [{"type": 13, "file": {"url": "attachment://missing.bin"}}],
        [],
    )
