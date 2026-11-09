import pytest
from preview_helpers import action_body, control_key

from simcord.components import walk_components


@pytest.mark.asyncio
async def test_rejected_actions_keep_sequence_and_report_expected(env, channel, alice):
    await alice.slash(channel, "panel")
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python

        gap = await preview._action("python", action_body(page, "refresh", 5, request_id="gap"))
        assert gap["rejected"] is True
        assert gap["settlement"] == "rejected"
        assert gap["dispatch"] == "not_dispatched"
        assert gap["dispatched"] is False
        assert gap["sequence"] == 5
        assert gap["expectedSequence"] == 0
        assert gap["diagnostics"][0]["code"] == "sequence-gap"

        payload = preview._page_payload(page)
        ping_key = control_key(payload, "persistent:ping")
        stale = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="stale",
                control_key=ping_key,
                published_revision=page.revision + 1,
            ),
        )
        assert stale["rejected"] is True
        assert stale["diagnostics"][0]["code"] == "stale-revision"
        assert stale["expectedSequence"] == 0

        # Rejections consumed nothing: sequence 1 is still the next admission.
        settled = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="click",
                control_key=ping_key,
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"
        assert settled["dispatched"] is True
        assert settled["expectedSequence"] == 1


@pytest.mark.asyncio
async def test_invalid_modal_submit_does_not_consume_sequence(env, channel, alice):
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        invalid = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="bad-modal",
                modal_handle=page.modal_handle,
                values={"bogus": "x"},
                published_revision=page.revision,
            ),
        )
        assert invalid["rejected"] is True
        assert invalid["diagnostics"][0]["code"] == "validation-failed"
        assert invalid["expectedSequence"] == 0
        # The modal is still open and sequence 1 remains available.
        assert preview._page_payload(page)["modal"] is not None
        settled = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="good-modal",
                modal_handle=page.modal_handle,
                values={"name": "Ada"},
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"
        assert channel.last_message.content == "Thanks Ada"


@pytest.mark.asyncio
async def test_disabled_select_rejected_before_dispatch(env, channel, alice):
    """A select disabled after the snapshot rejects at admission, sequence intact."""
    panel = await alice.slash(channel, "color")
    stored = env.backend.get_message(channel.id, panel.response.id)
    select = next(
        component for component in walk_components(stored.components) if component.get("custom_id") == "color"
    )
    select["disabled"] = True
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(panel.response)
        page = preview._python
        color_key = control_key(preview._page_payload(page), "color")
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="disabled-select",
                control_key=color_key,
                values=["red"],
                published_revision=page.revision,
            ),
        )
        assert rejected["rejected"] is True
        assert rejected["dispatched"] is False
        assert rejected["expectedSequence"] == 0

        select["disabled"] = False
        settled = await preview._action(
            "python",
            action_body(
                page,
                "select",
                1,
                request_id="enabled-select",
                control_key=color_key,
                values=["red"],
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"
        assert channel.last_message.content == "Picked red"


@pytest.mark.asyncio
async def test_click_on_select_rejected_before_dispatch(env, channel, alice):
    """kind=click against a select's custom_id is a pre-admission rejection."""
    panel = await alice.slash(channel, "color")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(panel.response)
        page = preview._python
        color_key = control_key(preview._page_payload(page), "color")
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="click-select",
                control_key=color_key,
                published_revision=page.revision,
            ),
        )
        assert rejected["rejected"] is True
        assert rejected["dispatched"] is False
        assert rejected["expectedSequence"] == 0


@pytest.mark.asyncio
async def test_consumed_modal_rejected_on_second_page(env, channel, alice):
    """A modal consumed on one page rejects, not fails, when resubmitted elsewhere."""
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        second = preview._open_page(alice.id)
        assert second.modal_handle is not None
        settled = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="submit",
                modal_handle=page.modal_handle,
                values={"name": "Ada"},
                published_revision=page.revision,
            ),
        )
        assert settled["rejected"] is False
        assert settled["settlement"] == "settled"

        resubmit = await preview._action(
            second.id,
            action_body(
                second,
                "modal_submit",
                1,
                request_id="resubmit",
                modal_handle=second.modal_handle,
                values={"name": "Bob"},
                published_revision=second.revision,
            ),
        )
        assert resubmit["rejected"] is True
        assert resubmit["dispatched"] is False
        assert resubmit["expectedSequence"] == 0
        assert channel.last_message.content == "Thanks Ada"


@pytest.mark.asyncio
async def test_modal_missing_required_control_rejected(env, channel, alice):
    """Omitting a required modal control rejects at admission, modal stays open."""
    feedback = await alice.slash(channel, "feedback")
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(feedback)
        page = preview._python
        rejected = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                1,
                request_id="missing-required",
                modal_handle=page.modal_handle,
                values={},
                published_revision=page.revision,
            ),
        )
        assert rejected["rejected"] is True
        assert rejected["dispatched"] is False
        assert rejected["expectedSequence"] == 0
        assert preview._page_payload(page)["modal"] is not None


@pytest.mark.asyncio
async def test_preview_generation_fields_reject_before_sequence_admission(env, channel, alice):
    async with env.preview(channel, viewers=[alice]) as preview:
        page = preview._python
        missing = action_body(page, "refresh", 1)
        missing.pop("generation")
        rejected = await preview._action("python", missing)
        assert rejected["rejected"] is True
        assert rejected["diagnostics"][0]["code"] == "bad-envelope"
        assert rejected["expectedSequence"] == 0

        malformed = action_body(page, "refresh", 1, request_id="malformed", bot_generation=True)
        rejected = await preview._action("python", malformed)
        assert rejected["rejected"] is True
        assert rejected["diagnostics"][0]["code"] == "bad-envelope"
        assert rejected["expectedSequence"] == 0

        admitted = await preview._action("python", action_body(page, "refresh", 1, request_id="valid"))
        assert admitted["rejected"] is False
        assert admitted["sequence"] == 1
