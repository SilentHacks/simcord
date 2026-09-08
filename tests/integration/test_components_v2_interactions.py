import discord
import pytest
from discord import app_commands
from discord.ext import commands

import simcord


def _layout_view(button_callback, select_callback=None):
    view = discord.ui.LayoutView()
    section = discord.ui.Section(
        discord.ui.TextDisplay("Choose"),
        accessory=discord.ui.Button(label="Open", custom_id="open", id=11),
        id=10,
    )
    section.accessory.callback = button_callback
    view.add_item(discord.ui.Container(section, id=9))
    if select_callback is not None:
        row = discord.ui.ActionRow(id=20)
        select = discord.ui.Select(
            custom_id="choice",
            options=[discord.SelectOption(label="Red", value="red")],
            id=21,
        )
        select.callback = select_callback
        row.add_item(select)
        view.add_item(discord.ui.Container(row, id=19))
    return view


async def test_v2_nested_button_and_select_include_component_ids():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    seen = {}

    @app_commands.command(name="panel")
    async def panel(interaction: discord.Interaction) -> None:
        async def button(interaction: discord.Interaction) -> None:
            seen["button"] = interaction.data
            await interaction.response.send_message("opened")

        async def select(interaction: discord.Interaction) -> None:
            seen["select"] = interaction.data
            await interaction.response.send_message(interaction.data["values"][0])

        await interaction.response.send_message(view=_layout_view(button, select))

    bot.tree.add_command(panel)
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        await env.bot.tree.sync()

        result = await alice.slash(channel, "panel")
        await alice.click(result.response.message, custom_id="open")
        assert seen["button"]["id"] == 11

        picked = await alice.select(result.response.message, ["red"], custom_id="choice")
        assert picked.response.content == "red"
        assert seen["select"]["id"] == 21


async def test_nested_ambiguous_and_noninteractive_buttons_are_rejected(env, channel, alice):
    view = discord.ui.LayoutView()
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(label="same", custom_id="a"))
    row.add_item(discord.ui.Button(label="same", custom_id="b"))
    row.add_item(discord.ui.Button(label="link", url="https://example.invalid"))
    row.add_item(discord.ui.Button(label="disabled", custom_id="disabled", disabled=True))
    view.add_item(row)
    select_row = discord.ui.ActionRow()
    select_row.add_item(
        discord.ui.Select(
            custom_id="select",
            options=[
                discord.SelectOption(label="One", value="one"),
                discord.SelectOption(label="Two", value="two"),
            ],
            max_values=2,
            id=22,
        )
    )
    view.add_item(select_row)
    message = await env.bot.get_channel(channel.id).send(view=view)

    with pytest.raises(simcord.SetupError, match="Ambiguous"):
        await alice.click(message, label="same")
    with pytest.raises(simcord.SetupError, match="link or premium"):
        await alice.click(message, label="link")
    with pytest.raises(simcord.SetupError, match="disabled"):
        await alice.click(message, custom_id="disabled")
    with pytest.raises(simcord.SetupError, match="must be a sequence"):
        await alice.select(message, "one", custom_id="select")  # type: ignore[arg-type]
    with pytest.raises(simcord.SetupError, match="between 1 and 2"):
        await alice.select(message, [], custom_id="select")
    with pytest.raises(simcord.SetupError, match="duplicate values"):
        await alice.select(message, ["one", "one"], custom_id="select")
    with pytest.raises(simcord.SetupError, match="expects string values"):
        await alice.select(message, [1], custom_id="select")  # type: ignore[list-item]
    with pytest.raises(simcord.SetupError, match="option 'missing' does not exist"):
        await alice.select(message, ["missing"], custom_id="select")
    selected = await alice.select(message, ["one"], custom_id="select")
    assert selected.response is None


async def test_modern_modal_values_wrappers_and_component_open_source_message():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    captured = {}

    class Form(discord.ui.Modal, title="Form"):
        name = discord.ui.Label(
            text="Name", component=discord.ui.TextInput(custom_id="name", id=31, min_length=2)
        )
        color = discord.ui.Label(
            text="Color",
            component=discord.ui.Select(
                custom_id="color",
                options=[discord.SelectOption(label="Red", value="red")],
                id=32,
            ),
        )
        files = discord.ui.Label(
            text="Files", component=discord.ui.FileUpload(custom_id="files", required=False, id=33)
        )
        radio = discord.ui.Label(
            text="Radio",
            component=discord.ui.RadioGroup(
                custom_id="radio",
                options=[
                    discord.RadioGroupOption(label="One", value="one"),
                    discord.RadioGroupOption(label="Two", value="two"),
                ],
                id=34,
            ),
        )
        help_text = discord.ui.TextDisplay("This is not submitted", id=37)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            captured["values"] = {
                "name": self.name.component.value,
                "color": self.color.component.values,
                "files": [item.filename for item in self.files.component.values],
                "radio": self.radio.component.value,
            }
            captured["data"] = interaction.data
            await interaction.response.edit_message(content="submitted", view=None)

    @app_commands.command(name="form")
    async def form(interaction: discord.Interaction) -> None:
        async def open_form(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(Form())

        view = discord.ui.View()
        button = discord.ui.Button(label="Open", custom_id="open")
        button.callback = open_form
        view.add_item(button)
        await interaction.response.send_message("form", view=view)

    bot.tree.add_command(form)
    async with simcord.run(bot) as env:
        guild = env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(env.create_user("alice"))
        await env.bot.tree.sync()

        original = (await alice.slash(channel, "form")).response
        opened = await alice.click(original.message, custom_id="open")
        submitted = await alice.submit_modal(
            opened,
            {
                "name": "Alice",
                "color": ["red"],
                "files": [("note.txt", b"hello")],
                "radio": "one",
            },
        )
        assert submitted.response.id == original.id
        assert submitted.response.content == "submitted"
        assert captured["values"] == {
            "name": "Alice",
            "color": ["red"],
            "files": ["note.txt"],
            "radio": "one",
        }
        assert captured["data"]["components"][-1] == {"type": 10, "id": 37}
        assert captured["data"]["components"][0]["id"] > 0
        assert captured["data"]["components"][0]["component"]["id"] == 31
        assert captured["data"]["resolved"]["attachments"]


async def test_user_handle_can_drive_dm_components(env):
    user = env.create_user("dm-user")
    seen = {}

    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        view = discord.ui.View()
        button = discord.ui.Button(label="DM", custom_id="dm-button")

        async def callback(interaction: discord.Interaction) -> None:
            seen["guild"] = interaction.guild
            await interaction.response.send_message("dm clicked")

        button.callback = callback
        view.add_item(button)
        await message.channel.send("reply", view=view)

    env.bot.add_listener(on_message, "on_message")
    await user.send_dm("hello")
    response = user.dm_channel.last_message
    result = await user.click(response, custom_id="dm-button")
    assert result.response.content == "dm clicked"
    assert seen["guild"] is None


async def _open_modal(env, channel, alice, modal):
    @app_commands.command(name="show")
    async def show(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(modal)

    env.bot.tree.add_command(show)
    await env.bot.tree.sync()
    return await alice.slash(channel, "show")


async def test_modal_text_input_validation_is_reported_through_actor_api(env, channel, alice):
    class Form(discord.ui.Modal, title="Text"):
        name = discord.ui.Label(
            text="Name",
            component=discord.ui.TextInput(custom_id="name", min_length=2, max_length=4),
        )
        note = discord.ui.Label(
            text="Note",
            component=discord.ui.TextInput(custom_id="note", required=False),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_message("ok")

    opened = await _open_modal(env, channel, alice, Form())
    with pytest.raises(simcord.SetupError, match="dict keyed by custom_id"):
        await alice.submit_modal(opened, [])  # type: ignore[arg-type]
    with pytest.raises(simcord.SetupError, match="Unknown modal custom_id 'other'"):
        await alice.submit_modal(opened, {"name": "Amy", "other": "x"})
    with pytest.raises(simcord.SetupError, match="Required modal control 'name' was not supplied"):
        await alice.submit_modal(opened, {})
    with pytest.raises(simcord.SetupError, match="expects a string"):
        await alice.submit_modal(opened, {"name": 1})
    with pytest.raises(simcord.SetupError, match="cannot be empty"):
        await alice.submit_modal(opened, {"name": ""})
    with pytest.raises(simcord.SetupError, match="shorter than min_length=2"):
        await alice.submit_modal(opened, {"name": "A"})
    with pytest.raises(simcord.SetupError, match="exceeds max_length=4"):
        await alice.submit_modal(opened, {"name": "Alice"})
    result = await alice.submit_modal(opened, {"name": "Amy"})
    assert result.response.content == "ok"


async def test_modal_choice_validation_is_reported_through_actor_api(env, channel, alice):
    class Form(discord.ui.Modal, title="Choices"):
        color = discord.ui.Label(
            text="Color",
            component=discord.ui.Select(
                custom_id="color",
                options=[
                    discord.SelectOption(label="Red", value="red"),
                    discord.SelectOption(label="Blue", value="blue"),
                    discord.SelectOption(label="Green", value="green"),
                ],
                max_values=2,
            ),
        )
        radio = discord.ui.Label(
            text="Radio",
            component=discord.ui.RadioGroup(
                custom_id="radio",
                options=[
                    discord.RadioGroupOption(label="One", value="one"),
                    discord.RadioGroupOption(label="Two", value="two"),
                ],
            ),
        )
        checks = discord.ui.Label(
            text="Checks",
            component=discord.ui.CheckboxGroup(
                custom_id="checks",
                options=[
                    discord.CheckboxGroupOption(label="One", value="one"),
                    discord.CheckboxGroupOption(label="Two", value="two"),
                ],
                min_values=0,
                max_values=1,
                required=False,
            ),
        )
        person = discord.ui.Label(
            text="Person",
            component=discord.ui.UserSelect(custom_id="person", required=False),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_message("ok")

    opened = await _open_modal(env, channel, alice, Form())
    with pytest.raises(simcord.SetupError, match="Required modal control 'color' was not supplied"):
        await alice.submit_modal(opened, {"radio": "one"})
    with pytest.raises(simcord.SetupError, match="cannot contain duplicate values"):
        await alice.submit_modal(opened, {"color": ["red", "red"], "radio": "one"})
    with pytest.raises(simcord.SetupError, match="expects string values"):
        await alice.submit_modal(opened, {"color": [1], "radio": "one"})
    with pytest.raises(simcord.SetupError, match="expects between 1 and 2 values"):
        await alice.submit_modal(opened, {"color": ["red", "blue", "green"], "radio": "one"})
    with pytest.raises(simcord.SetupError, match="Select option 'missing' does not exist"):
        await alice.submit_modal(opened, {"color": ["missing"], "radio": "one"})
    with pytest.raises(simcord.SetupError, match="Required modal control 'radio' was not supplied"):
        await alice.submit_modal(opened, {"color": ["red"]})
    with pytest.raises(simcord.SetupError, match=r"RadioGroup .* expects a string"):
        await alice.submit_modal(opened, {"color": ["red"], "radio": 1})
    with pytest.raises(simcord.SetupError, match="RadioGroup option 'missing' does not exist"):
        await alice.submit_modal(opened, {"color": ["red"], "radio": "missing"})
    with pytest.raises(simcord.SetupError, match=r"CheckboxGroup .* expects string values"):
        await alice.submit_modal(opened, {"color": ["red"], "radio": "one", "checks": [1]})
    with pytest.raises(simcord.SetupError, match="CheckboxGroup option 'missing' does not exist"):
        await alice.submit_modal(opened, {"color": ["red"], "radio": "one", "checks": ["missing"]})
    with pytest.raises(simcord.SetupError, match="expects between 0 and 1 values"):
        await alice.submit_modal(opened, {"color": ["red"], "radio": "one", "checks": ["one", "two"]})
    with pytest.raises(simcord.SetupError, match="USER_SELECT expects"):
        await alice.submit_modal(opened, {"color": ["red"], "radio": "one", "person": ["alice"]})
    bob = env.guild.add_member(env.create_user("bob"))
    result = await alice.submit_modal(
        opened,
        {"color": ["red"], "radio": "one", "person": [bob]},
    )
    assert result.response.content == "ok"
    with pytest.raises(simcord.SetupError, match="did not respond with a modal"):
        await alice.submit_modal(result, {})


async def test_modal_upload_and_checkbox_validation_is_reported_through_actor_api(env, channel, alice):
    class Form(discord.ui.Modal, title="Files"):
        upload = discord.ui.Label(
            text="Upload",
            component=discord.ui.FileUpload(custom_id="upload", min_values=1, max_values=1),
        )
        accepted = discord.ui.Label(
            text="Accepted",
            component=discord.ui.Checkbox(custom_id="accepted"),
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_message("ok")

    opened = await _open_modal(env, channel, alice, Form())
    with pytest.raises(simcord.SetupError, match="Required modal control 'upload' was not supplied"):
        await alice.submit_modal(opened, {"accepted": True})
    with pytest.raises(simcord.SetupError, match=r"expects .*filename, bytes.* tuples"):
        await alice.submit_modal(opened, {"upload": "file", "accepted": True})
    with pytest.raises(simcord.SetupError, match=r"expects .*filename, bytes.* tuples"):
        await alice.submit_modal(opened, {"upload": [("bad", "text")], "accepted": True})
    with pytest.raises(simcord.SetupError, match="expects between 1 and 1 files"):
        await alice.submit_modal(
            opened,
            {"upload": [("one", b"1"), ("two", b"2")], "accepted": True},
        )
    with pytest.raises(simcord.SetupError, match="Checkbox 'accepted' expects a bool"):
        await alice.submit_modal(opened, {"upload": ("one", b"1"), "accepted": "yes"})
    result = await alice.submit_modal(opened, {"upload": ("one", b"1"), "accepted": True})
    assert result.response.content == "ok"
