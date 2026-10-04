"""Replay supplied Discord capture payloads in a local SimCord preview.

This never connects to Discord. Evidence remains unreviewed: comparison results
are diagnostics, not certification.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import re
import struct
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

import simcord

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_REFERENCE = _ROOT / ".discord-reference-captures/calibration-batch-01/reference"
_CATALOG = _ROOT / "tests/fixtures/preview/catalog.py"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _catalog() -> Any:
    spec = importlib.util.spec_from_file_location("simcord_reference_replay_catalog", _CATALOG)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"fixture catalog is unavailable: {_CATALOG}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _payload_matches(
    fixture: Mapping[str, Any], catalog: Any, reference_dir: Path
) -> tuple[dict[str, Any], str | None]:
    normalized = fixture.get("normalizedPayload")
    expected_hash = fixture.get("normalizedPayloadHash")
    if not isinstance(normalized, Mapping) or _canonical_hash(normalized) != expected_hash:
        return {}, "normalized payload hash is invalid"
    posted_content = fixture.get("postedContent")
    viewer_mention = fixture.get("viewerMention")
    if (
        isinstance(posted_content, str)
        and isinstance(viewer_mention, str)
        and posted_content.replace(viewer_mention, "@simcord-viewer") != normalized.get("content")
    ):
        return {}, "postedContent does not normalize to the recorded payload content"
    payload = catalog.gallery_payload(str(fixture["referenceId"]), viewer_mention="@simcord-viewer")
    try:
        candidate: dict[str, Any] = {"content": str(payload.get("content", ""))}
        if "embed" in payload:
            candidate["embed"] = payload["embed"].to_dict()
        if "view" in payload:
            candidate["components"] = payload["view"].to_components()
            candidate["flags"] = 32768 if isinstance(payload["view"], discord.ui.LayoutView) else 0
        assets: dict[str, str] = {}
        for file in payload.get("files", []):
            position = file.fp.tell()
            data = file.fp.read()
            file.fp.seek(position)
            assets[file.filename] = hashlib.sha256(data).hexdigest()
        candidate["attachments"] = assets
        if _canonical_hash(candidate) != expected_hash:
            catalog.close_payload(payload)
            return {}, "catalog payload differs from captured normalized payload; refusing substitute"
        for name, digest in assets.items():
            asset = (reference_dir / "assets" / name).resolve()
            if (
                not asset.is_relative_to(reference_dir.resolve())
                or not asset.is_file()
                or hashlib.sha256(asset.read_bytes()).hexdigest() != digest
            ):
                catalog.close_payload(payload)
                return {}, f"exact attachment bytes unavailable or hash mismatch: {name}"
        return payload, None
    except Exception:
        catalog.close_payload(payload)
        raise


def _select_label(fixture: Mapping[str, Any], state: str) -> str | None:
    payload = fixture.get("normalizedPayload")
    if not isinstance(payload, Mapping):
        return None

    def components(value: Any):
        if isinstance(value, Mapping):
            if value.get("type") in {3, 5, 6, 7, 8} and isinstance(value.get("placeholder"), str):
                yield value
            for child in value.values():
                yield from components(child)
        elif isinstance(value, list):
            for child in value:
                yield from components(child)

    selects = [item for item in components(payload.get("components", [])) if not item.get("disabled")]
    reference_id = str(fixture.get("referenceId", ""))
    if reference_id == "REF-30-STRING-SELECT":
        match = next((item for item in selects if item.get("type") == 3), None)
    elif reference_id == "REF-31-ENTITY-SELECTS":
        kind = state.split("-", 1)[0]
        match = next(
            (
                item
                for item in selects
                if isinstance(item.get("custom_id"), str) and item["custom_id"].rsplit(":", 1)[-1] == kind
            ),
            None,
        )
    else:
        return None
    return match.get("placeholder") if match else None


def _history_chain(
    target: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> tuple[list[Mapping[str, Any]], bool]:
    target_fixture = target.get("fixture")
    if not isinstance(target_fixture, Mapping):
        return [target], False
    channel_id = str(target_fixture.get("channelId", ""))
    by_message: dict[str, Mapping[str, Any]] = {}
    for record in records:
        fixture = record.get("fixture")
        if isinstance(fixture, Mapping) and str(fixture.get("channelId", "")) == channel_id:
            message_id = str(fixture.get("messageId", ""))
            if message_id:
                by_message.setdefault(message_id, record)
    chain = sorted(by_message.values(), key=lambda row: str(row["fixture"]["createdAt"]))
    previous_id = str(chain[0]["fixture"].get("previousMessageId", "")) if chain else ""
    return chain, bool(previous_id and previous_id not in by_message)


async def _ready(page: Any) -> None:
    await page.wait_for_function(
        "() => window.simcordPreview?.ready === true && !window.simcordPreview.pendingAction && "
        "!window.simcordPreview.pendingQuery && !document.querySelector('[role=listbox][aria-busy=true]') && "
        "['healthy', 'recovered'].includes(window.simcordPreview.transport.state)",
        timeout=30_000,
    )
    await page.wait_for_function(
        "() => !document.fonts || document.fonts.status === 'loaded'", timeout=30_000
    )
    await page.wait_for_function(
        "() => [...document.images].every(image => image.complete && image.naturalWidth > 0)", timeout=30_000
    )


async def _capture_scope(
    page: Any, row: Mapping[str, Any], state: str, message: Any
) -> tuple[bytes, dict[str, Any], str]:
    fixture = row["fixture"]
    reference_id = str(fixture.get("referenceId", ""))
    target = page.locator(f'article[data-message-id="{message.id}"]')
    if state in {"text-empty", "text-focus", "text-validation", "text-filled"}:
        modal = page.get_by_role("dialog", name="REF-51-TEXT-MODAL", exact=True)
        if await modal.count() != 1 or not await modal.is_visible():
            raise ValueError("text modal has no visible capture scope")
        geometry = await modal.bounding_box()
        if geometry is None:
            raise ValueError("text modal has no natural geometry")
        return await modal.screenshot(), geometry, '[role="dialog"][aria-label="REF-51-TEXT-MODAL"]'
    if state in {"closed", "escape-cancel"}:
        label = _select_label(fixture, state)
        if label:
            control = target.get_by_role("combobox", name=label, exact=True)
            box = await control.bounding_box()
            if box is None:
                raise ValueError("closed select control has no natural geometry")
            return await page.screenshot(clip=box), box, f'[role="combobox"][aria-label="{label}"]'
    if reference_id == "REF-40-V2-LAYOUT-MEDIA" and state in {
        "idle-top",
        "idle-media",
        "spoiler-revealed",
    }:
        selector = ".component-container" if state == "idle-top" else ".component-gallery:visible"
        scope = target.locator(selector)
        if state == "idle-top":
            scope = scope.filter(has=page.get_by_role("heading", name="Expedition status", exact=True))
        if await scope.count() != 1 or not await scope.is_visible():
            raise ValueError(f"named V2 section {selector} is unavailable")
        geometry = await scope.evaluate("""el => {
          const r=el.getBoundingClientRect();
          return {x:r.x,y:r.y,width:r.width,height:r.height};
        }""")
        return await scope.screenshot(), geometry, f'article[data-message-id="{message.id}"] {selector}'
    if state == "user-selected":
        label = _select_label(fixture, state)
        control = target.get_by_role("combobox", name=label, exact=True)
        box = await control.bounding_box()
        if box is None:
            raise ValueError("selected invoking-user control has no natural geometry")
        return await page.screenshot(clip=box), box, f'[role="combobox"][aria-label="{label}"]'
    if state in {"open", "two-selected"} or state.endswith("-open"):
        listbox = page.get_by_role("listbox")
        if await listbox.count() != 1 or not await listbox.is_visible():
            raise ValueError("state requires the native visible select listbox")
        label = _select_label(fixture, state)
        if not label:
            raise ValueError("select capture has no fixture-derived accessible label")
        control = target.get_by_role("combobox", name=label, exact=True)
        control_box = await control.bounding_box()
        menu_box = await page.locator(".select-list:popover-open").bounding_box()
        if control_box is None or menu_box is None:
            raise ValueError("select control or menu has no visible natural geometry")
        left = min(control_box["x"], menu_box["x"])
        top = min(control_box["y"], menu_box["y"])
        right = max(control_box["x"] + control_box["width"], menu_box["x"] + menu_box["width"])
        bottom = max(control_box["y"] + control_box["height"], menu_box["y"] + menu_box["height"])
        viewport = await page.evaluate("() => ({width: innerWidth, height: innerHeight})")
        if left < 0 or top < 0 or right > viewport["width"] or bottom > viewport["height"]:
            raise ValueError("complete select/menu capture is outside the browser viewport")
        if right <= left or bottom <= top:
            raise ValueError("select/menu capture has no natural geometry")
        clip = {"x": left, "y": top, "width": right - left, "height": bottom - top}
        return await page.screenshot(clip=clip), clip, '.select-list:popover-open [role="listbox"]'
    if await target.count() != 1 or not await target.is_visible():
        raise ValueError("target message surface is unavailable")
    geometry = await target.evaluate("""el => {
      const r=el.getBoundingClientRect();
      return {x:r.x,y:r.y,width:r.width,height:r.height};
    }""")
    return await target.screenshot(), geometry, f'article[data-message-id="{message.id}"]'


async def _platform_fonts(page: Any, selector: str) -> dict[str, Any]:
    try:
        session = await page.context.new_cdp_session(page)
        await session.send("DOM.enable")
        await session.send("CSS.enable")
        document = await session.send("DOM.getDocument")
        node = await session.send(
            "DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector}
        )
        if not node.get("nodeId"):
            return {"available": False, "reason": "captured element did not resolve in Chromium CDP"}
        ids = await session.send("DOM.querySelectorAll", {"nodeId": node["nodeId"], "selector": "*"})
        families = set()
        for node_id in [node["nodeId"], *ids["nodeIds"]]:
            fonts = await session.send("CSS.getPlatformFontsForNode", {"nodeId": node_id})
            families.update(
                str(item["familyName"]) for item in fonts.get("fonts", []) if item.get("familyName")
            )
        return {
            "available": bool(families),
            "platformFonts": sorted(families),
            "ggSansResolved": any("gg sans" in name.lower() for name in families) if families else None,
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


async def _act(page: Any, row: Mapping[str, Any], state: str, scene: Mapping[str, Any], message: Any) -> str:
    fixture = row["fixture"]
    target = page.locator(f'article[data-message-id="{message.id}"]')
    reference_id = str(fixture.get("referenceId", ""))
    if state in {"text-empty", "text-focus", "text-validation", "text-filled"}:
        if reference_id != "REF-50-MODALS":
            raise ValueError("text-modal recipe requires the modal fixture")
        await target.get_by_role("button", name="Open text modal", exact=True).click()
        await _ready(page)
        modal = page.get_by_role("dialog", name="REF-51-TEXT-MODAL", exact=True)
        await modal.wait_for(state="visible")
        name = modal.get_by_role("textbox", name=re.compile(r"^Display name(?:\s*\*)?$"))
        feedback = modal.get_by_role("textbox", name="Optional feedback", exact=True)
        if state == "text-focus":
            await name.focus()
        elif state == "text-validation":
            await modal.get_by_role("button", name="Submit", exact=True).click()
            await page.wait_for_function(
                "() => document.querySelector('[role=dialog] [aria-invalid=true]') !== null"
            )
        elif state == "text-filled":
            values = scene.get("textModalValues")
            if not isinstance(values, Mapping) or any(
                not isinstance(values.get(key), str) for key in ("reference:name", "reference:feedback")
            ):
                raise ValueError("text-filled requires explicitly observed textModalValues for both fields")
            await name.fill(values["reference:name"])
            await feedback.fill(values["reference:feedback"])
        await _ready(page)
        return f"opened the real text-modal callback and prepared {state}; submission outcome unclaimed"
    if state in {"idle", "idle-top", "idle-media", "closed"}:
        if state == "closed":
            label = _select_label(fixture, state)
            if not label or await target.get_by_role("combobox", name=label, exact=True).count() != 1:
                raise ValueError("closed select state has no unique fixture control")
            if await page.get_by_role("listbox").count():
                raise ValueError("closed select state unexpectedly has an open listbox")
        return "none; captured the requested closed/idle presentation"
    if state in {"primary-hover", "primary-focus"}:
        button = target.get_by_role("button", name="Primary", exact=True)
        if await button.count() != 1:
            raise ValueError("primary button not uniquely available")
        if state.endswith("hover"):
            await button.hover()
            if not await button.evaluate("el => el.matches(':hover')"):
                raise ValueError("primary button did not enter hover state")
            return "hover:Primary"
        await button.focus()
        await page.keyboard.press("Tab")
        await page.keyboard.press("Shift+Tab")
        if not await button.evaluate("el => el.matches(':focus-visible')"):
            raise ValueError("primary button did not receive visible keyboard focus")
        return "focus:Primary"
    if state in {"open", "two-selected", "escape-cancel", "user-selected"} or state.endswith("-open"):
        label = _select_label(fixture, state)
        if not label:
            raise ValueError(f"no supported select control for {reference_id}/{state}")
        control = target.get_by_role("combobox", name=label, exact=True)
        if await control.count() != 1:
            raise ValueError(f"select control {label!r} not uniquely available")
        await control.click()
        await _ready(page)
        listbox = page.get_by_role("listbox")
        if await listbox.count() != 1 or not await listbox.is_visible():
            raise ValueError("select menu did not open")
        if state == "two-selected":
            selected_labels = scene.get("stringSelectLabels")
            if (
                not isinstance(selected_labels, list)
                or len(selected_labels) != 2
                or any(not isinstance(value, str) or not value for value in selected_labels)
                or len(set(selected_labels)) != 2
            ):
                raise ValueError("two-selected requires two explicitly observed stringSelectLabels")
            options = listbox.get_by_role("option")
            labels = [
                await options.nth(index).locator(".option-label").inner_text()
                for index in range(await options.count())
            ]
            if any(labels.count(value) != 1 for value in selected_labels):
                raise ValueError("observed selected labels are not uniquely available")
            for selected in (False, True):
                for index, option_label in enumerate(labels):
                    if (option_label in selected_labels) != selected:
                        continue
                    option = options.nth(index)
                    if (await option.get_attribute("aria-selected") == "true") != selected:
                        await option.click()
                        await _ready(page)
            if not await listbox.is_visible():
                raise ValueError("multi-select menu did not remain open")
            return f"open with observed selections: {', '.join(selected_labels)}"
        if state == "escape-cancel":
            await page.keyboard.press("Escape")
            await _ready(page)
            if await listbox.is_visible():
                raise ValueError("Escape did not close the select menu")
            return "opened then Escape-cancelled the current menu; source callback semantics unclaimed"
        if state == "user-selected":
            viewer_label = scene.get("viewerLabel")
            if not isinstance(viewer_label, str) or not viewer_label:
                raise ValueError("observed invoking-viewer label is unavailable")
            options = listbox.get_by_role("option").filter(
                has=page.locator(".entity-name", has_text=re.compile(f"^{re.escape(viewer_label)}$"))
            )
            if await options.count() != 1:
                raise ValueError("observed invoking-viewer option is unavailable")
            await options.click()
            await _ready(page)
            await control.filter(has_text=viewer_label).wait_for(state="visible", timeout=10_000)
            return "invoking viewer selected by exact observed label; callback receipt unknown"
        return f"open:{label}"
    if state == "spoiler-revealed":
        gallery = target.locator(".component-gallery:visible")
        if await gallery.count() != 1:
            raise ValueError("the V2 gallery section is unavailable")
        reveal = gallery.get_by_role("button", name=re.compile("Reveal .* spoiler", re.I))
        if await reveal.count() != 1:
            raise ValueError("spoiler reveal control is unavailable")
        await reveal.click()
        return "revealed gallery spoiler through the live preview control"
    raise ValueError(f"state has no evidence-supported interaction recipe: {state}")


def _scrub(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): _scrub(v)
            for k, v in value.items()
            if str(k).lower() not in {"capability", "messageid", "channelid", "previousmessageid"}
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?[^\s\"']*", "[local-url]", value)
    return value


async def _replay_one(
    sidecar_path: Path,
    reference_dir: Path,
    output_dir: Path,
    catalog: Any,
    source_batch: str | None,
    history_records: Sequence[Mapping[str, Any]],
    scene: Mapping[str, Any],
) -> dict[str, Any]:
    sidecar = _load_json(sidecar_path)
    fixture = sidecar.get("fixture")
    if not isinstance(fixture, Mapping):
        return {"capture": sidecar_path.name, "status": "blocked", "reason": "fixture payload missing"}
    name = str(sidecar.get("name", sidecar_path.with_suffix(".png").name))
    output_png = output_dir / name
    output_json = output_png.with_suffix(".json")
    reference_png = reference_dir / name
    if not reference_png.is_file():
        return {"capture": name, "status": "blocked", "reason": "reference PNG missing"}
    profile = sidecar.get("profile")
    observed = profile.get("observed") if isinstance(profile, Mapping) else None
    presentation = scene.get("presentation")
    if not isinstance(presentation, Mapping) or any(
        type(presentation.get(key)) is not int or presentation[key] <= 0 for key in ("width", "height")
    ):
        return {
            "capture": name,
            "status": "blocked",
            "reason": "scene requires measured presentation.width and height",
        }
    if not isinstance(observed, Mapping) or any(
        type(observed.get(key)) is not int or observed[key] <= 0 for key in ("width", "height")
    ):
        return {"capture": name, "status": "blocked", "reason": "source browser viewport is unobserved"}
    if (
        type(observed.get("deviceScaleFactor")) not in {int, float}
        or observed["deviceScaleFactor"] != 1
        or type(observed.get("reducedMotion")) is not bool
    ):
        return {"capture": name, "status": "blocked", "reason": "source scale-1/motion profile is unobserved"}
    if any(not isinstance(observed.get(key), str) or not observed[key] for key in ("locale", "timezone")):
        return {"capture": name, "status": "blocked", "reason": "source locale/timezone is unobserved"}
    payload, reason = _payload_matches(fixture, catalog, reference_dir)
    if reason:
        return {"capture": name, "status": "blocked", "reason": reason}
    state = str(sidecar.get("state", "unknown"))
    history, missing_predecessor = _history_chain(sidecar, history_records)
    target_source_id = str(fixture.get("messageId", ""))
    try:
        bot = commands.Bot(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents(guilds=True),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        async with simcord.run(bot) as env:
            viewer_label = scene.get("viewerLabel")
            if not isinstance(viewer_label, str) or not viewer_label:
                raise ValueError("observed invoking-viewer identity is unavailable")
            viewer = env.create_user(str(scene.get("viewerUsername", viewer_label)), global_name=viewer_label)
            guild = env.create_guild("reference-replay", owner=viewer)
            channel_names = scene.get("channels", [])
            channel = guild.create_text_channel(channel_names[0] if channel_names else "reference-gallery")
            viewer_member = guild.add_member(viewer)
            for role in scene.get("roles", []):
                if isinstance(role, Mapping) and isinstance(role.get("label"), str):
                    guild.create_role(
                        role["label"],
                        color=int(str(role.get("observedIconColor", "0")).removeprefix("#"), 16),
                    )
            for channel_name in scene.get("channels", []):
                if isinstance(channel_name, str) and channel_name != channel.name:
                    guild.create_text_channel(channel_name)
            await env.settle()
            bot_guild = env.bot.get_guild(guild.id)
            if bot_guild is None or bot_guild.me is None:
                raise RuntimeError("SimCord bot guild member unavailable")
            author_data = fixture.get("author", {})
            author_display = (
                str(author_data.get("displayName", "")) if isinstance(author_data, Mapping) else ""
            )
            if not author_display:
                raise ValueError("source author display identity is unavailable in this sidecar")
            await env.bot.user.edit(username=str(author_data.get("username", author_display)))
            await bot_guild.me.edit(nick=author_display)
            bot_channel = env.bot.get_channel(channel.id)
            if bot_channel is None:
                raise RuntimeError("SimCord bot channel unavailable")
            missing_predecessor_time = None
            if missing_predecessor and history:
                first_fixture = history[0].get("fixture")
                previous_id = (
                    first_fixture.get("previousMessageId") if isinstance(first_fixture, Mapping) else None
                )
                if isinstance(previous_id, str) and previous_id.isdigit():
                    missing_predecessor_time = discord.utils.snowflake_time(int(previous_id))
                    current_time = datetime.fromisoformat(env.backend.now_iso())
                    await env.advance_time(
                        max(0.0, (missing_predecessor_time - current_time).total_seconds())
                    )
                    first_reference = str(first_fixture["referenceId"])
                    reference_index = catalog.REFERENCE_IDS.index(first_reference)
                    if reference_index:
                        predecessor = catalog.gallery_payload(
                            catalog.REFERENCE_IDS[reference_index - 1], viewer_mention=viewer.mention
                        )
                        try:
                            await bot_channel.send(**predecessor)
                        finally:
                            catalog.close_payload(predecessor)
            target_history_index = next(
                index
                for index, item in enumerate(history)
                if isinstance(item.get("fixture"), Mapping)
                and str(item["fixture"].get("messageId", "")) == target_source_id
            )
            local_messages: dict[str, Any] = {}
            missing_context = 0
            for entry in history:
                entry_fixture = entry.get("fixture")
                if not isinstance(entry_fixture, Mapping):
                    continue
                source_id = str(entry_fixture.get("messageId", ""))
                if source_id in local_messages:
                    continue
                if source_id == target_source_id:
                    entry_payload = payload
                    close_entry_payload = False
                else:
                    entry_payload, entry_reason = _payload_matches(entry_fixture, catalog, reference_dir)
                    close_entry_payload = entry_payload != {}
                    if entry_reason:
                        missing_context += 1
                        continue
                try:
                    source_time = datetime.fromisoformat(str(entry_fixture["createdAt"]))
                    if source_time.tzinfo is None:
                        missing_context += 1
                        continue
                    current_time = datetime.fromisoformat(env.backend.now_iso())
                    await env.advance_time(max(0.0, (source_time - current_time).total_seconds()))
                    posted = dict(entry_payload)
                    posted["content"] = str(posted.get("content", "")).replace(
                        "@simcord-viewer", viewer.mention
                    )
                    local_messages[source_id] = await bot_channel.send(**posted)
                finally:
                    if close_entry_payload:
                        catalog.close_payload(entry_payload)
            message = local_messages.get(target_source_id)
            if message is None:
                raise ValueError("target reference message could not be replayed")
            id_mapping = {
                str(fixture["messageId"]): str(message.id),
                str(author_data["id"]): str(env.bot.user.id),
                str(fixture["viewerMention"]).removeprefix("<@").removesuffix(">"): str(viewer.id),
                str(fixture["channelId"]): str(channel.id),
                **{source_id: str(item.id) for source_id, item in local_messages.items()},
            }
            predecessor_count = sum(
                1
                for item in history[:target_history_index]
                if isinstance(item.get("fixture"), Mapping)
                and str(item["fixture"].get("messageId", "")) in local_messages
            )
            async with env.preview(
                channel,
                viewers=[viewer_member],
                layout="channel",
                width=presentation["width"],
                height=presentation["height"],
                display="fixed",
                locale=observed["locale"],
                timezone=observed["timezone"],
            ) as preview:
                await preview.show(message)
                from playwright.async_api import async_playwright

                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    context = await browser.new_context(
                        viewport={"width": observed["width"], "height": observed["height"]},
                        device_scale_factor=1,
                        reduced_motion="reduce" if observed["reducedMotion"] else "no-preference",
                        locale=observed["locale"],
                        timezone_id=observed["timezone"],
                    )
                    page = await context.new_page()
                    try:
                        await page.goto(preview.url, wait_until="domcontentloaded", timeout=30_000)
                        await _ready(page)
                        target_row = page.locator(f'article[data-message-id="{message.id}"]')
                        await target_row.evaluate(
                            "el => el.scrollIntoView({block: 'start', behavior: 'instant'})"
                        )
                        action = await _act(page, sidecar, state, scene, message)
                        capture_bytes, geometry, selector = await _capture_scope(
                            page, sidecar, state, message
                        )
                        font_info = await _platform_fonts(page, selector)
                        browser_profile = await page.evaluate("""() => ({
                          viewport:{width:innerWidth,height:innerHeight},
                          deviceScaleFactor:devicePixelRatio,
                          userAgent:navigator.userAgent,
                          reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,
                          locale:navigator.language,
                          timezone:Intl.DateTimeFormat().resolvedOptions().timeZone
                        })""")
                        timestamp = message.created_at.isoformat()
                        pixel_width, pixel_height = struct.unpack_from(">II", capture_bytes, 16)
                        target_time = datetime.fromisoformat(str(fixture["createdAt"]))
                        metadata = {
                            "fixtureId": sidecar.get("fixtureId"),
                            "evidenceStatus": "observed-unreviewed",
                            "calibrated": False,
                            "sourceCapture": name,
                            "sourceState": state,
                            "sourceBatch": source_batch,
                            "normalizedPayloadHash": fixture.get("normalizedPayloadHash"),
                            "assetHashes": fixture.get("assetHashes", {}),
                            "normalizedContent": fixture.get("normalizedPayload", {}).get("content", ""),
                            "originalToLocalIds": id_mapping,
                            "sourceGroup": "same captured channel; replayed known predecessor fixtures in source order",
                            "predecessorCountReplayed": predecessor_count,
                            "sourcePredecessorRecorded": bool(fixture.get("previousMessageId")),
                            "missingPredecessorPayload": missing_predecessor,
                            "missingPredecessorSnowflakeTime": missing_predecessor_time.isoformat()
                            if missing_predecessor_time
                            else None,
                            "predecessorReconstruction": "canonical preceding catalog fixture from producing bot's ordered send loop; original predecessor sidecar not supplied"
                            if missing_predecessor
                            else None,
                            "additionalContextMessagesUnavailable": missing_context,
                            "sourceBrowserProfile": sidecar.get("profile"),
                            "observedCreatedAt": fixture.get("createdAt"),
                            "replayedCreatedAt": timestamp,
                            "timestampDeltaMilliseconds": round(
                                (message.created_at - target_time).total_seconds() * 1000, 3
                            ),
                            "postedContentReplay": "recorded content preserved; source viewer mention remapped to local viewer ID",
                            "timestampMatch": timestamp == fixture.get("createdAt"),
                            "action": action,
                            "sourceCrop": sidecar.get("crop"),
                            "candidateCrop": geometry,
                            "candidateCropSelector": selector,
                            "candidatePixelDimensions": {"width": pixel_width, "height": pixel_height},
                            "sceneDifferences": [
                                "source author is represented by SimCord bot member; exact source avatar bytes are unavailable",
                                "role-to-member assignments and permissions are unobserved; roles are not assigned speculatively",
                                "local viewer is the replay guild owner to avoid inventing another visible candidate; source ownership is unobserved",
                                "source channel's own private name is unavailable; first observed channel candidate is used as the local replay target",
                                *(
                                    ["source predecessor exists but its payload/sidecar was not supplied"]
                                    if missing_predecessor
                                    else []
                                ),
                            ],
                            "entityRoster": {
                                "seededRoleCount": len(scene.get("roles", [])),
                                "seededChannelCount": len(scene.get("channels", [])),
                                "roleAssignmentsObserved": False,
                            },
                            "themeEvidence": {
                                "observed": sidecar.get("profile", {}).get("userAttested", {}).get("theme"),
                                "candidate": "SimCord dark default",
                                "themeControlAvailable": False,
                            },
                            "densityEvidence": {
                                "sourceAttested": sidecar.get("profile", {})
                                .get("userAttested", {})
                                .get("density"),
                                "candidate": "SimCord preview default",
                                "verified": False,
                            },
                            "fontEvidence": {
                                "sourceDeclaredFamilies": sidecar.get("profile", {})
                                .get("observed", {})
                                .get("loadedFonts", []),
                                "candidatePlatformFonts": font_info,
                                "ggSansResolved": font_info.get("ggSansResolved"),
                                "sourceGlyphUsageUnverified": True,
                            },
                            "candidateBrowserProfile": browser_profile,
                            "candidateLogicalViewport": {
                                "width": presentation["width"],
                                "height": presentation["height"],
                            },
                            "avatarEvidence": "source avatar URL is remote; no matching local avatar bytes supplied; default avatar used",
                            "localIdentity": {
                                "authorDisplayFromEvidence": True,
                                "viewerDisplayFromEvidence": True,
                            },
                        }
                        output_png.write_bytes(capture_bytes)
                        output_json.write_text(
                            json.dumps(_scrub(metadata), indent=2) + "\n", encoding="utf-8"
                        )
                        return {
                            "capture": name,
                            "status": "captured",
                            "action": action,
                            "reference": str(reference_png),
                            "actual": str(output_png),
                            "timestampMatch": timestamp == fixture.get("createdAt"),
                            "timestampDeltaMilliseconds": round(
                                (message.created_at - target_time).total_seconds() * 1000, 3
                            ),
                            "fontEvidence": font_info,
                            "candidateCrop": geometry,
                        }
                    finally:
                        await context.close()
                        await browser.close()
    except Exception as exc:
        return {"capture": name, "status": "blocked", "reason": str(exc)}
    finally:
        catalog.close_payload(payload)


async def replay(
    reference_dir: Path,
    output_dir: Path,
    state: str | None,
    capture: str | None,
    scene_path: Path | None = None,
) -> dict[str, Any]:
    from compare_visual_reference import compare

    if output_dir == reference_dir or reference_dir in output_dir.parents:
        raise ValueError("output directory must not be the reference directory or a child of it")
    if await asyncio.to_thread(lambda: output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("output directory must be empty; existing files are never overwritten")
    pack = _load_json(reference_dir / "reference-pack.json")
    scene = _load_json(scene_path or reference_dir.parent / "replay-scene.json")
    manifest_references = pack.get("references", [])
    if not isinstance(manifest_references, list):
        raise ValueError("reference pack references must be an array")
    source_batches = pack.get("sourceCaptureBatches", {})
    if not isinstance(source_batches, Mapping):
        raise ValueError("reference pack sourceCaptureBatches must be an object")
    sidecars = await asyncio.to_thread(lambda: sorted(reference_dir.glob("ref-*.json")))
    if any(not path.resolve().is_relative_to(reference_dir) for path in sidecars):
        raise ValueError("capture sidecars must resolve inside the reference directory")
    records = [_load_json(path) for path in sidecars]
    manifest_by_name = {
        reference["name"]: reference
        for reference in manifest_references
        if isinstance(reference, Mapping) and isinstance(reference.get("name"), str)
    }
    manifest_names = {
        reference["name"]
        for reference in manifest_references
        if isinstance(reference, Mapping) and isinstance(reference.get("name"), str)
    }
    sidecar_names = {record["name"] for record in records if isinstance(record.get("name"), str)}
    inventory = {
        "manifestReferenceCount": len(manifest_references),
        "sidecarCount": len(sidecars),
        "manifestReferencesWithoutSidecars": sorted(manifest_names - sidecar_names),
        "sidecarsWithoutManifestReferences": sorted(sidecar_names - manifest_names),
    }
    selected = [
        (path, record)
        for path, record in zip(sidecars, records, strict=True)
        if (state is None or record.get("state") == state)
        and (capture is None or record.get("fixtureId") == capture or record.get("name") == capture)
        and record.get("name") in manifest_by_name
    ]
    if not selected:
        raise ValueError("selection matched no supplied capture sidecars")
    for _, record in selected:
        name = record.get("name")
        if not isinstance(name, str) or Path(name).name != name or "\\" in name or name in {".", ".."}:
            raise ValueError("capture sidecar contains an unsafe output filename")
        if record != manifest_by_name[name]:
            raise ValueError(f"capture sidecar differs from the complete manifest record: {name}")
        reference_path = (reference_dir / name).resolve()
        expected_hash = manifest_by_name[name].get("sha256")
        if (
            not reference_path.is_relative_to(reference_dir)
            or not isinstance(expected_hash, str)
            or hashlib.sha256(reference_path.read_bytes()).hexdigest() != expected_hash
        ):
            raise ValueError(f"reference PNG does not match its manifest SHA-256: {name}")
        if (output_dir / name).exists() or (output_dir / Path(name).with_suffix(".json")).exists():
            raise ValueError(f"refusing to overwrite existing output for {name}")
    catalog = _catalog()
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    rows = []
    for path, sidecar in selected:
        result = await _replay_one(
            path,
            reference_dir,
            output_dir,
            catalog,
            source_batches.get(str(sidecar.get("name", ""))),
            records,
            scene,
        )
        if result["status"] == "captured":
            folder = output_dir / Path(str(result["capture"])).stem
            comparison = compare(
                Path(result["reference"]),
                Path(result["actual"]),
                folder,
                metadata=_load_json(Path(result["actual"]).with_suffix(".json")),
            )
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "comparison.json").write_text(
                json.dumps(_scrub(comparison), indent=2) + "\n", encoding="utf-8"
            )
            result["comparison"] = comparison
            result["comparisonStatus"] = comparison.get("status")
        rows.append(result)
    report = {
        "status": "complete"
        if (
            all(row["status"] == "captured" for row in rows)
            and not inventory["manifestReferencesWithoutSidecars"]
            and not inventory["sidecarsWithoutManifestReferences"]
        )
        else "partial",
        "evidenceStatus": "observed-unreviewed",
        "calibrated": False,
        "inventory": inventory,
        "selected": len(rows),
        "rows": rows,
    }
    (output_dir / "replay-report.json").write_text(
        json.dumps(_scrub(report), indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay observed captures locally in SimCord; never connects to Discord."
    )
    parser.add_argument("--reference-dir", type=Path, default=_DEFAULT_REFERENCE)
    parser.add_argument("--output-dir", type=Path, default=Path(".discord-reference-captures/replay"))
    parser.add_argument(
        "--scene", type=Path, help="Private observed scene JSON; defaults beside the reference directory"
    )
    parser.add_argument("--state")
    parser.add_argument("--fixture", help="exact fixture ID or reference PNG filename")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(
            replay(
                args.reference_dir.resolve(), args.output_dir.resolve(), args.state, args.fixture, args.scene
            )
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
