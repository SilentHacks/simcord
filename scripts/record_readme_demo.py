"""Record a standalone showcase candidate; never change the README or its GIF.

Run: uv run python scripts/record_readme_demo.py
Writes docs/assets/simcord-showcase-candidate.gif for review before publication.
Requires simcord[screenshot], Playwright Chromium, and ffmpeg.
The bundled generated landscapes contain no people, animals, or faces.
The assertions exercise real browser callbacks and resulting Discord state.
"""

from __future__ import annotations

import asyncio
import io
import math
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

import discord
from discord.ext import commands
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

import simcord

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "docs/assets/adventure"
OUTPUT = ROOT / "docs/assets/simcord-showcase-candidate.gif"
DESTINATIONS = {
    "alpine": (
        "Alpine Dawn",
        "Turquoise water. Snow-capped peaks. A fresh start.",
        "3 days",
        "Lakeside cabin",
        0x45C7D6,
    ),
    "canyon": (
        "Starlight Canyon",
        "Rose-gold cliffs. Endless horizons. A sky full of stars.",
        "5 days",
        "Desert lodge",
        0xE99A76,
    ),
}


def card(destination: str, joined: bool = False, itinerary: bool = False) -> discord.Embed:
    title, description, duration, stay, color = DESTINATIONS[destination]
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name="WEEKEND EXPEDITIONS")
    embed.add_field(name="Duration", value=duration)
    embed.add_field(name="Stay", value=stay)
    embed.add_field(name="Going", value="13 adventurers" if joined else "12 adventurers")
    if itinerary:
        embed.add_field(name="Itinerary", value="Sunset overlook → canyon lodge → stargazing", inline=False)
    embed.set_image(url=f"attachment://{destination}.png")
    return embed


class Announcement(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        explore = discord.ui.Button(
            label="Explore destinations", style=discord.ButtonStyle.primary, custom_id="club:explore"
        )
        explore.callback = self.explore
        self.status = discord.ui.TextDisplay(
            "**Two landscapes. One unforgettable weekend.**\nChoose a destination and make it yours."
        )
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Adventure Club\n**THE WEEKEND COLLECTION** · Explore somewhere extraordinary."
                ),
                discord.ui.Separator(),
                discord.ui.Section(
                    "### Beyond the everyday\nAlpine sunrises and desert constellations.",
                    accessory=discord.ui.Thumbnail(
                        "attachment://alpine.png", description="Uninhabited alpine lake"
                    ),
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem("attachment://alpine.png", description="Alpine lake at sunrise"),
                    discord.MediaGalleryItem(
                        "attachment://canyon.png", description="Desert canyon under stars"
                    ),
                ),
                discord.ui.Separator(),
                discord.ui.Section(self.status, accessory=explore),
                accent_colour=discord.Colour(0x45C7D6),
            )
        )
        self.explored = False

    async def explore(self, interaction: discord.Interaction) -> None:
        self.explored = True
        planner = discord.ui.LayoutView(timeout=None)
        planner.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "### Adventure Club / Trip planner\nPick a landscape, RSVP, then make it yours."
                ),
                accent_colour=discord.Colour(0x45C7D6),
            )
        )
        await interaction.response.edit_message(view=planner, attachments=[])


class Confirmation(discord.ui.LayoutView):
    def __init__(self, destination: str, name: str, note: str) -> None:
        super().__init__(timeout=None)
        title, _, duration, stay, _ = DESTINATIONS[destination]
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## You're booked, {name}!\n**{title}** · Your weekend is taking shape."
                ),
                discord.ui.Separator(),
                discord.ui.Section(
                    f"**{duration} / {stay}**\n{note}",
                    accessory=discord.ui.Button(
                        label="Reserved", style=discord.ButtonStyle.success, disabled=True
                    ),
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        f"attachment://{destination}.png", description="Uninhabited destination scenery"
                    )
                ),
                discord.ui.ActionRow(
                    discord.ui.Button(
                        label="Booking confirmed", style=discord.ButtonStyle.success, disabled=True
                    ),
                    discord.ui.Button(
                        label="Edit details", style=discord.ButtonStyle.secondary, disabled=True
                    ),
                ),
                accent_colour=discord.Colour(0x57F287),
            )
        )


class Booking(discord.ui.Modal, title="Customise your adventure"):
    traveller = discord.ui.TextInput(label="Traveller name", custom_id="traveller", placeholder="Your name")
    note = discord.ui.TextInput(
        label="Trip note",
        custom_id="note",
        style=discord.TextStyle.paragraph,
        placeholder="What are you looking forward to?",
    )

    def __init__(self, view: Adventure) -> None:
        super().__init__()
        self.adventure = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        destination = self.adventure.destination
        await interaction.response.send_message(
            view=Confirmation(destination, self.traveller.value, self.note.value),
            file=discord.File(ART / f"{destination}.png"),
        )
        self.adventure.confirmation = await interaction.original_response()
        poll = discord.Poll(question="How should we spend the evening?", duration=timedelta(hours=24))
        poll.add_answer(text="Sunset hike")
        poll.add_answer(text="Stargazing")
        poll.add_answer(text="Campfire stories")
        assert isinstance(interaction.channel, discord.TextChannel)
        self.adventure.poll_message = await interaction.channel.send(
            "### Make it our kind of weekend\n**Vote for the evening plan.**\nSecret stop: ||The sandstone arch at blue hour||",
            poll=poll,
        )


class Adventure(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.destination = "alpine"
        self.joined = False
        self.itinerary = False
        self.confirmation: discord.InteractionMessage | None = None
        self.poll_message: discord.Message | None = None

    @discord.ui.select(
        placeholder="Choose your destination",
        custom_id="adventure:destination",
        options=[
            discord.SelectOption(label=title, value=key, description=description)
            for key, (title, description, *_rest) in DESTINATIONS.items()
        ],
    )
    async def choose(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.destination = select.values[0]
        for option in select.options:
            option.default = option.value == self.destination
        await interaction.response.edit_message(
            embed=card(self.destination, self.joined, self.itinerary),
            attachments=[discord.File(ART / f"{self.destination}.png")],
            view=self,
        )

    @discord.ui.button(
        label="Customise trip", custom_id="adventure:customise", style=discord.ButtonStyle.primary, row=1
    )
    async def customise(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(Booking(self))

    @discord.ui.button(
        label="Itinerary", custom_id="adventure:itinerary", style=discord.ButtonStyle.secondary, row=1
    )
    async def show_itinerary(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.itinerary = not self.itinerary
        await interaction.response.edit_message(
            embed=card(self.destination, self.joined, self.itinerary), view=self
        )

    @discord.ui.button(label="RSVP", custom_id="adventure:rsvp", style=discord.ButtonStyle.success, row=1)
    async def rsvp(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.joined = True
        button.label = "Joined"
        button.disabled = True
        await interaction.response.edit_message(embed=card(self.destination, True, self.itinerary), view=self)

    @discord.ui.button(
        label="Leave trip", custom_id="adventure:leave", style=discord.ButtonStyle.danger, row=1
    )
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.joined = False
        self.rsvp.label = "RSVP"
        self.rsvp.disabled = False
        await interaction.response.edit_message(
            embed=card(self.destination, False, self.itinerary), view=self
        )


async def ready(page) -> None:
    await page.wait_for_function(
        "() => window.simcordPreview?.ready && !window.simcordPreview.pendingAction && "
        "['healthy','recovered'].includes(window.simcordPreview.transport.state) && "
        "document.fonts.status === 'loaded' && [...document.images].every(i => i.complete && i.naturalWidth > 0)"
    )


async def main() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents(guilds=True))
    async with simcord.run(bot) as env:
        avatar = Image.new("RGB", (96, 96), "#243750")
        ImageDraw.Draw(avatar).polygon([(12, 73), (45, 20), (80, 73)], fill="#70DBD5")
        avatar_bytes = io.BytesIO()
        avatar.save(avatar_bytes, "PNG")
        assert bot.user is not None
        await bot.user.edit(username="Wayfinder")
        await env.settle()
        guild = env.create_guild("Adventure Club")
        channel = guild.create_text_channel("weekend-adventures")
        alice = guild.add_member(env.create_user("alex"))
        bot_channel = bot.get_channel(channel.id)
        assert isinstance(bot_channel, discord.TextChannel)
        announcement = Announcement()
        intro = await bot_channel.send(
            view=announcement, files=[discord.File(ART / "alpine.png"), discord.File(ART / "canyon.png")]
        )
        view = Adventure()
        message = await bot_channel.send(
            embed=card("alpine"), file=discord.File(ART / "alpine.png"), view=view
        )
        assets = {
            f"https://cdn.simcord.invalid/embed/avatars/{(user_id >> 22) % 6}.png": (
                "mountain-avatar.png",
                avatar_bytes.getvalue(),
            )
            for user_id in (bot.user.id, alice.id)
        }
        async with env.preview(
            channel, viewers=[alice], layout="channel", width=720, height=680, display="fixed", assets=assets
        ) as preview:
            await preview.show(intro)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                page = await browser.new_page(viewport={"width": 1000, "height": 850}, device_scale_factor=1)
                await page.goto(preview.url, wait_until="domcontentloaded")
                await ready(page)
                chrome = await page.evaluate(
                    "() => {const s=document.querySelector('#preview-stage');return [innerWidth-s.clientWidth,innerHeight-s.clientHeight]}"
                )
                await page.set_viewport_size({"width": 720 + chrome[0], "height": 680 + chrome[1]})
                await ready(page)
                clip = await page.locator("#preview-app").bounding_box()
                assert clip is not None
                pointer = [clip["x"] + 660, clip["y"] + 600]
                recording = True
                with tempfile.TemporaryDirectory(prefix="simcord-showcase-") as temporary:
                    frames = Path(temporary)

                    async def capture() -> None:
                        index = 0
                        while recording:
                            started = asyncio.get_running_loop().time()
                            image = Image.open(io.BytesIO(await page.screenshot(clip=clip))).convert("RGB")
                            draw = ImageDraw.Draw(image)
                            x, y = pointer[0] - clip["x"], pointer[1] - clip["y"]
                            draw.polygon(
                                [
                                    (x, y),
                                    (x + 1, y + 21),
                                    (x + 6, y + 16),
                                    (x + 11, y + 25),
                                    (x + 15, y + 23),
                                    (x + 10, y + 14),
                                    (x + 18, y + 14),
                                ],
                                fill="white",
                                outline="#111111",
                                width=2,
                            )
                            image.save(frames / f"{index:05d}.png")
                            index += 1
                            await asyncio.sleep(max(0, 0.1 - (asyncio.get_running_loop().time() - started)))

                    async def move(locator) -> None:
                        box = await locator.bounding_box()
                        assert box is not None
                        target = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
                        start = pointer.copy()
                        for step in range(1, 9):
                            t = (1 - math.cos(math.pi * step / 8)) / 2
                            pointer[:] = [start[i] + (target[i] - start[i]) * t for i in range(2)]
                            await page.mouse.move(*pointer)
                            await asyncio.sleep(0.035)

                    async def click(locator) -> None:
                        await move(locator)
                        await locator.click()
                        await ready(page)

                    async def reveal(locator) -> None:
                        await page.locator(".channel-header").click(position={"x": 650, "y": 20})
                        box = await locator.bounding_box()
                        timeline = await page.locator("#channel-timeline").bounding_box()
                        assert box is not None and timeline is not None
                        delta = box["y"] + box["height"] / 2 - timeline["y"] - timeline["height"] / 2
                        await page.mouse.move(
                            timeline["x"] + timeline["width"] - 30, timeline["y"] + timeline["height"] / 2
                        )
                        for _ in range(8):
                            await page.mouse.wheel(0, delta / 8)
                            await asyncio.sleep(0.07)
                        await asyncio.sleep(0.3)

                    task = asyncio.create_task(capture())
                    try:
                        await asyncio.sleep(3)
                        await click(page.get_by_role("button", name="Explore destinations", exact=True))
                        assert announcement.explored
                        await (
                            page.locator("#message-picker button").filter(has_text=f"ID {message.id}").click()
                        )
                        await ready(page)
                        select = page.get_by_role("combobox", name="Choose your destination")
                        await reveal(select)
                        await asyncio.sleep(0.4)
                        await move(select)
                        await select.click()
                        await ready(page)
                        await asyncio.sleep(0.8)
                        await click(page.get_by_role("option", name="Starlight Canyon"))
                        apply = page.get_by_role("button", name="Apply", exact=True)
                        if await apply.count() and await apply.is_visible():
                            await click(apply)
                        await (
                            page.locator(".markdown-paragraph")
                            .filter(has_text="Rose-gold cliffs.")
                            .wait_for()
                        )
                        assert view.destination == "canyon"
                        await asyncio.sleep(1.2)
                        await click(page.get_by_role("button", name="RSVP", exact=True))
                        await page.get_by_text("13 adventurers", exact=True).wait_for()
                        assert view.joined and view.rsvp.disabled
                        await asyncio.sleep(1)
                        await click(page.get_by_role("button", name="Itinerary", exact=True))
                        await page.get_by_text(
                            "Sunset overlook → canyon lodge → stargazing", exact=True
                        ).wait_for()
                        assert view.itinerary
                        await asyncio.sleep(1)
                        await click(page.get_by_role("button", name="Customise trip", exact=True))
                        await page.get_by_role("dialog").wait_for()
                        await asyncio.sleep(0.7)
                        traveller = page.get_by_label("Traveller name")
                        await move(traveller)
                        await traveller.press_sequentially("Alex", delay=110)
                        note = page.get_by_label("Trip note")
                        await move(note)
                        await note.press_sequentially("Sunsets and stargazing.", delay=40)
                        await asyncio.sleep(0.7)
                        await click(page.get_by_role("button", name="Submit", exact=True))
                        confirmed = page.get_by_role("heading", name="You're booked, Alex!", exact=True)
                        await confirmed.wait_for()
                        await reveal(confirmed)
                        assert view.confirmation is not None
                        confirmation = await bot_channel.fetch_message(view.confirmation.id)
                        assert confirmation.flags.components_v2
                        assert "Sunsets and stargazing." in str(
                            [component.to_dict() for component in confirmation.components]
                        )
                        await asyncio.sleep(2.4)
                        poll = page.locator(".message-poll")
                        await reveal(poll)
                        await asyncio.sleep(0.6)
                        await click(poll.get_by_role("button", name="Stargazing", exact=True))
                        await asyncio.sleep(0.5)
                        await click(poll.get_by_role("button", name="Vote", exact=True))
                        assert view.poll_message is not None
                        stored_poll = env.backend.get_message(channel.id, view.poll_message.id).poll
                        assert stored_poll is not None and alice.id in stored_poll.votes[2]
                        await asyncio.sleep(1.4)
                        spoiler = page.locator(".markdown-spoiler")
                        await click(spoiler)
                        assert await spoiler.evaluate("element => element.classList.contains('is-revealed')")
                        await asyncio.sleep(2)
                        print(
                            "Verified: V2 accessory callback, destination embed edit, RSVP count + disabled button, itinerary, modal, V2 confirmation, poll vote, spoiler reveal"
                        )
                    finally:
                        recording = False
                        await task
                    await asyncio.to_thread(
                        subprocess.run,
                        [
                            "ffmpeg",
                            "-y",
                            "-loglevel",
                            "error",
                            "-framerate",
                            "10",
                            "-i",
                            str(frames / "%05d.png"),
                            "-filter_complex",
                            "[0:v]split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a",
                            "-loop",
                            "0",
                            str(OUTPUT),
                        ],
                        check=True,
                    )
                    print(f"Saved standalone candidate: {OUTPUT}")
                await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
