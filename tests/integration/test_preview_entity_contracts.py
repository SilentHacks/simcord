import discord
import pytest
from preview_helpers import action_body, control_key


async def _entity_defaults_world(env, channel):
    users = {
        name: env.guild.add_member(env.create_user(name))
        for name in (f"candidate-{index:03}" for index in range(55))
    }
    default_user = users["candidate-054"]
    draft_user = users["candidate-053"]
    default_role = env.guild.create_role("preview-default-role")
    draft_channel = env.guild.create_text_channel("draft-channel")
    source_channel_id = channel.id
    received = []

    class EntityDefaultsModal(discord.ui.Modal, title="Entity defaults"):
        user = discord.ui.Label(
            text="User",
            component=discord.ui.UserSelect(
                custom_id="entity-user",
                default_values=[
                    discord.SelectDefaultValue(id=default_user.id, type=discord.SelectDefaultValueType.user)
                ],
            ),
        )
        role = discord.ui.Label(
            text="Role",
            component=discord.ui.RoleSelect(
                custom_id="entity-role",
                default_values=[
                    discord.SelectDefaultValue(id=default_role.id, type=discord.SelectDefaultValueType.role)
                ],
            ),
        )
        mentionable = discord.ui.Label(
            text="Mentionable",
            component=discord.ui.MentionableSelect(
                custom_id="entity-mentionable",
                max_values=2,
                default_values=[
                    discord.SelectDefaultValue(id=default_user.id, type=discord.SelectDefaultValueType.user),
                    discord.SelectDefaultValue(id=default_role.id, type=discord.SelectDefaultValueType.role),
                ],
            ),
        )
        channel = discord.ui.Label(
            text="Channel",
            component=discord.ui.ChannelSelect(
                custom_id="entity-channel",
                channel_types=[discord.ChannelType.text],
                default_values=[
                    discord.SelectDefaultValue(
                        id=source_channel_id, type=discord.SelectDefaultValueType.channel
                    )
                ],
            ),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            received.append(
                {
                    "user": [value.id for value in self.user.component.values],
                    "role": [value.id for value in self.role.component.values],
                    "mentionable": [value.id for value in self.mentionable.component.values],
                    "channel": [value.id for value in self.channel.component.values],
                }
            )
            await interaction.response.send_message("entity defaults submitted")

    class OpenEntityDefaultsView(discord.ui.View):
        @discord.ui.button(label="Open entity defaults", custom_id="open-entity-defaults")
        async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.send_modal(EntityDefaultsModal())

    message = await env.bot.get_channel(channel.id).send(
        content="Entity defaults", view=OpenEntityDefaultsView()
    )
    return message, default_user, draft_user, default_role, draft_channel, received


async def _open_entity_defaults(preview, message):
    await preview.show(message)
    page = preview._python
    payload = preview._page_payload(page)
    open_key = control_key(payload, "open-entity-defaults")
    opened = await preview._action(
        "python",
        action_body(
            page,
            "click",
            1,
            request_id="open-entity-defaults",
            control_key=open_key,
            published_revision=page.revision,
        ),
    )
    assert opened["dispatched"] is True
    assert opened["outcomes"] == [{"kind": "modal"}]
    assert opened["target"] == {
        "messageId": str(message.id),
        "controlKey": open_key,
    }
    assert page.modal_handle is not None

    payload = preview._page_payload(page)
    custom_ids = {
        "entity-user",
        "entity-role",
        "entity-mentionable",
        "entity-channel",
    }
    modal_controls = {}
    pending = [payload["modal"]["payload"]["components"]]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            custom_id = item.get("custom_id")
            if custom_id in custom_ids:
                modal_controls[custom_id] = item
            pending.extend(item.values())
    assert set(modal_controls) == custom_ids
    assert all("default_values" not in item for item in modal_controls.values())

    keys = {custom_id: item["control_key"] for custom_id, item in modal_controls.items()}
    return page, payload, open_key, keys


@pytest.mark.asyncio
async def test_modal_entity_defaults_and_off_page_drafts_submit_native_values(env, channel, alice):
    message, default_user, draft_user, default_role, draft_channel, received = await _entity_defaults_world(
        env, channel
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        page, payload, open_key, keys = await _open_entity_defaults(preview, message)
        assert set(payload["candidates"]) == set(keys.values())
        descriptors = payload["candidates"]
        assert all(len(descriptor["entries"]) <= 50 for descriptor in descriptors.values())
        expected_defaults = {
            "entity-user": ("users", {str(default_user.id)}),
            "entity-role": ("roles", {str(default_role.id)}),
            "entity-mentionable": ("mentionables", {str(default_user.id), str(default_role.id)}),
            "entity-channel": ("channels", {str(channel.id)}),
        }
        for custom_id, (kind, expected_ids) in expected_defaults.items():
            descriptor = descriptors[keys[custom_id]]
            assert descriptor["type"] == kind
            assert {item["id"] for item in descriptor["selected"]} == expected_ids
        first_user_page = descriptors[keys["entity-user"]]
        assert str(default_user.id) not in {item["id"] for item in first_user_page["entries"]}
        assert str(draft_user.id) not in {item["id"] for item in first_user_page["entries"]}

        def candidate_body(
            sequence,
            custom_id,
            query,
            request_id,
            *,
            selected_values=None,
            cursor=None,
        ):
            return action_body(
                page,
                "browse_candidates",
                sequence,
                request_id=request_id,
                control_key=keys[custom_id],
                modal_handle=page.modal_handle,
                query=query,
                cursor=cursor,
                selected_values=selected_values,
            )

        draft_user_id = str(draft_user.id)
        draft_user_query = candidate_body(
            2,
            "entity-user",
            "",
            "draft-user",
            selected_values=[draft_user_id],
        )
        draft_user_result = await preview._action("python", draft_user_query)
        assert draft_user_result["dispatched"] is False
        draft_user_descriptor = draft_user_result["result"]["candidate"]
        assert {item["id"] for item in draft_user_descriptor["selected"]} == {draft_user_id}
        assert draft_user_descriptor["selected"][0]["name"] == draft_user.name
        assert draft_user_id not in {item["id"] for item in draft_user_descriptor["entries"]}

        user_no_results = candidate_body(
            3,
            "entity-user",
            "no-such-user",
            "draft-user-no-results",
            selected_values=[draft_user_id],
        )
        no_user_results = await preview._action("python", user_no_results)
        no_user_descriptor = no_user_results["result"]["candidate"]
        assert no_user_descriptor["state"] == "empty"
        assert no_user_descriptor["entries"] == []
        assert {item["id"] for item in no_user_descriptor["selected"]} == {draft_user_id}

        draft_channel_id = str(draft_channel.id)
        channel_query = candidate_body(
            4,
            "entity-channel",
            "no-such-channel",
            "draft-channel",
            selected_values=[draft_channel_id],
        )
        channel_result = await preview._action("python", channel_query)
        channel_descriptor = channel_result["result"]["candidate"]
        assert channel_descriptor["state"] == "empty"
        assert channel_descriptor["entries"] == []
        assert channel_descriptor["selected"][0]["id"] == draft_channel_id
        assert channel_descriptor["selected"][0]["name"] == draft_channel.name
        submitted = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                5,
                request_id="submit-entity-defaults",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={
                    "entity-user": [str(default_user.id)],
                    "entity-role": [str(default_role.id)],
                    "entity-mentionable": [str(default_user.id), str(default_role.id)],
                    "entity-channel": [str(channel.id)],
                },
            ),
        )
        assert submitted["dispatch"] == "dispatched"
        assert submitted["acknowledgement"] == "acknowledged"
        assert submitted["settlement"] == "settled"
        assert len(received) == 1
        assert received[0]["user"] == [default_user.id]
        assert received[0]["role"] == [default_role.id]
        assert set(received[0]["mentionable"]) == {default_user.id, default_role.id}
        assert received[0]["channel"] == [channel.id]
        assert channel.last_message.content == "entity defaults submitted"
        assert submitted["target"] == {"messageId": str(message.id), "controlKey": open_key}
        assert submitted["outcomes"] == [{"kind": "response", "messageId": str(channel.last_message.id)}]
        assert page.target_id == message.id
        assert preview._page_payload(page)["targetId"] == str(message.id)


@pytest.mark.asyncio
async def test_modal_candidate_cursor_scope_and_revoked_draft_replay(env, channel, alice):
    message, default_user, draft_user, _role, draft_channel, _received = await _entity_defaults_world(
        env, channel
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        page, payload, _open_key, keys = await _open_entity_defaults(preview, message)
        descriptors = payload["candidates"]
        first_user_page = descriptors[keys["entity-user"]]
        assert str(default_user.id) not in {item["id"] for item in first_user_page["entries"]}
        assert str(draft_user.id) not in {item["id"] for item in first_user_page["entries"]}
        assert first_user_page["hasNext"] is True
        user_cursor = first_user_page["nextCursor"]
        assert user_cursor

        def candidate_body(
            sequence,
            custom_id,
            query,
            request_id,
            *,
            selected_values=None,
            cursor=None,
        ):
            return action_body(
                page,
                "browse_candidates",
                sequence,
                request_id=request_id,
                control_key=keys[custom_id],
                modal_handle=page.modal_handle,
                query=query,
                cursor=cursor,
                selected_values=selected_values,
            )

        cross_control = await preview._action(
            "python",
            candidate_body(
                2,
                "entity-mentionable",
                "",
                "cross-control-cursor",
                cursor=user_cursor,
            ),
        )
        assert cross_control["rejected"] is True
        assert cross_control["diagnostics"][0]["code"] == "stale-cursor"
        draft_user_id = str(draft_user.id)
        draft_user_query = candidate_body(
            2,
            "entity-user",
            "",
            "draft-user",
            selected_values=[draft_user_id],
        )
        draft_user_result = await preview._action("python", draft_user_query)
        assert draft_user_result["dispatched"] is False
        draft_user_descriptor = draft_user_result["result"]["candidate"]
        assert {item["id"] for item in draft_user_descriptor["selected"]} == {draft_user_id}
        assert draft_user_descriptor["selected"][0]["name"] == draft_user.name
        assert draft_user_id not in {item["id"] for item in draft_user_descriptor["entries"]}

        user_no_results = candidate_body(
            3,
            "entity-user",
            "no-such-user",
            "draft-user-no-results",
            selected_values=[draft_user_id],
        )
        no_user_results = await preview._action("python", user_no_results)
        no_user_descriptor = no_user_results["result"]["candidate"]
        assert no_user_descriptor["state"] == "empty"
        assert no_user_descriptor["entries"] == []
        assert {item["id"] for item in no_user_descriptor["selected"]} == {draft_user_id}

        draft_channel_id = str(draft_channel.id)
        channel_query = candidate_body(
            4,
            "entity-channel",
            "no-such-channel",
            "draft-channel",
            selected_values=[draft_channel_id],
        )
        channel_result = await preview._action("python", channel_query)
        channel_descriptor = channel_result["result"]["candidate"]
        assert channel_descriptor["state"] == "empty"
        assert channel_descriptor["entries"] == []
        assert channel_descriptor["selected"][0]["id"] == draft_channel_id
        assert channel_descriptor["selected"][0]["name"] == draft_channel.name

        env.guild.remove_member(draft_user)
        cached_draft_channel = env.bot.get_channel(draft_channel.id)
        cached_alice = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await cached_draft_channel.set_permissions(cached_alice, view_channel=False)
        await env.settle()

        revoked_user_query = candidate_body(
            5,
            "entity-user",
            "no-such-user",
            "revoked-user",
            selected_values=[draft_user_id],
        )
        revoked_user_result = await preview._action("python", revoked_user_query)
        revoked_user_descriptor = revoked_user_result["result"]["candidate"]
        assert revoked_user_descriptor["selected"] == []
        assert revoked_user_descriptor["entries"] == []
        replayed_user = await preview._action("python", revoked_user_query)
        replayed_user_descriptor = replayed_user["result"]["candidate"]
        assert replayed_user_descriptor["selected"] == []
        assert replayed_user_descriptor["entries"] == []
        assert all(
            item.get("id") != draft_user_id and item.get("name") != draft_user.name
            for item in replayed_user_descriptor["selected"] + replayed_user_descriptor["entries"]
        )

        revoked_channel_query = candidate_body(
            6,
            "entity-channel",
            "no-such-channel",
            "revoked-channel",
            selected_values=[draft_channel_id],
        )
        revoked_channel_result = await preview._action("python", revoked_channel_query)
        revoked_channel_descriptor = revoked_channel_result["result"]["candidate"]
        assert revoked_channel_descriptor["selected"] == []
        assert revoked_channel_descriptor["entries"] == []
        replayed_channel = await preview._action("python", revoked_channel_query)
        replayed_channel_descriptor = replayed_channel["result"]["candidate"]
        assert replayed_channel_descriptor["selected"] == []
        assert replayed_channel_descriptor["entries"] == []
        assert all(
            item.get("id") != draft_channel_id and item.get("name") != draft_channel.name
            for item in replayed_channel_descriptor["selected"] + replayed_channel_descriptor["entries"]
        )


class _EntityModal(discord.ui.Modal, title="Member"):
    member = discord.ui.Label(
        text="Member",
        component=discord.ui.UserSelect(custom_id="member"),
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("member submitted")


class _EntityModalView(discord.ui.View):
    @discord.ui.button(label="Member", custom_id="open-member")
    async def open_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(_EntityModal())


@pytest.mark.asyncio
async def test_preview_modal_entity_resolution_and_validation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    message = await env.bot.get_channel(channel.id).send(content="member", view=_EntityModalView())
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        page = preview._python
        member_key = control_key(preview._page_payload(page), "open-member")
        opened = await preview._action(
            "python",
            action_body(
                page,
                "click",
                1,
                request_id="open-member",
                control_key=member_key,
                published_revision=page.revision,
            ),
        )
        assert opened["dispatched"] is True
        invalid = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="bad-member",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"member": ["999999999999"]},
            ),
        )
        assert invalid["dispatched"] is False
        submitted = await preview._action(
            "python",
            action_body(
                page,
                "modal_submit",
                2,
                request_id="good-member",
                published_revision=page.revision,
                modal_handle=page.modal_handle,
                values={"member": [str(bob.id)]},
            ),
        )
        assert submitted["dispatched"] is True
        assert channel.last_message.content == "member submitted"
