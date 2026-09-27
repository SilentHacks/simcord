"""Capture registered SimCord product states from the served browser page.

The runner owns no Discord credentials and never opens a Discord connection.  It
builds a fresh local world from ``tests/fixtures/preview/catalog.py`` for each
row, then drives the actual preview page with ordinary browser interactions.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

import simcord

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional screenshot extra
    Image = None  # type: ignore[assignment]


def _validate_manifest(path: Path) -> None:
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    from compare_visual_reference import load_coverage_manifest

    load_coverage_manifest(path)


_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_PATH = _ROOT / "tests" / "fixtures" / "preview" / "catalog.py"
_COVERAGE_PATH = _ROOT / "tests" / "fixtures" / "preview" / "coverage.json"
_PROFILE_PATH = _ROOT / "tests" / "fixtures" / "preview" / "profiles.json"
_REFERENCE_IDS = {
    "00": "REF-00-INDEX",
    "10": "REF-10-LEGACY-EMBED",
    "11": "REF-11-ATTACHMENTS",
    "20": "REF-20-BUTTONS",
    "30": "REF-30-STRING-SELECT",
    "31": "REF-31-ENTITY-SELECTS",
    "40": "REF-40-V2-LAYOUT-MEDIA",
    "50": "REF-50-MODALS",
}
_CAPABILITY_RE = re.compile(r"(?i)(https?://(?:127\.0\.0\.1|localhost)(?::\d+)?)(?:[/#?][^\s\"']*)?")


def _load_catalog() -> Any:
    spec = importlib.util.spec_from_file_location("simcord_preview_fixture_catalog", _CATALOG_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"fixture catalog is unavailable: {_CATALOG_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _scrub(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _scrub(item) for key, item in value.items() if str(key).lower() != "capability"}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return _CAPABILITY_RE.sub("[scrubbed-url]", value)
    return value


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Reject malformed protocol-2 data before it can become a capture."""
    if snapshot.get("protocolVersion") != 2:
        raise ValueError("capture snapshot is not protocol 2")
    if not isinstance(snapshot.get("messages"), Mapping):
        raise ValueError("capture snapshot messages must be an object")
    if not isinstance(snapshot.get("profile"), Mapping):
        raise ValueError("capture snapshot profile is missing")
    diagnostics = snapshot.get("diagnostics", ())
    if not isinstance(diagnostics, (list, tuple)) or any(
        not isinstance(item, Mapping) for item in diagnostics
    ):
        raise ValueError("capture snapshot diagnostics are malformed")


def _profile(profiles: Mapping[str, Any], profile_id: str) -> dict[str, Any]:
    entries = profiles.get("profiles")
    if not isinstance(entries, list):
        raise ValueError("profiles metadata must contain profiles")
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("id") == profile_id:
            viewport = entry.get("viewport")
            if not isinstance(viewport, Mapping):
                raise ValueError(f"profile {profile_id} has no viewport")
            width, height = viewport.get("width"), viewport.get("height")
            if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                raise ValueError(f"profile {profile_id} has invalid viewport")
            return dict(entry)
    raise ValueError(f"unknown capture profile {profile_id!r}")


def _reference_id(row: Mapping[str, Any]) -> str:
    fixture_id = str(row["id"])
    match = re.search(r"ref\.(\d+)", fixture_id)
    if not match:
        raise ValueError(f"{fixture_id} has no catalog reference")
    try:
        return _REFERENCE_IDS[match.group(1)]
    except KeyError as exc:
        raise ValueError(f"unsupported catalog reference in {fixture_id}") from exc


def _capture_name(row: Mapping[str, Any]) -> str:
    historical = row.get("historicalCapture")
    if isinstance(historical, Mapping) and isinstance(historical.get("name"), str):
        name = str(historical["name"])
        if Path(name).name != name:
            raise ValueError(f"{row['id']} has an unsafe capture filename")
        return name
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(row["id"])).strip("-") + ".png"


def _validate_row(row: Mapping[str, Any], catalog: Any, profiles: Mapping[str, Any]) -> None:
    if not isinstance(row.get("id"), str) or not row["id"]:
        raise ValueError("coverage row id must be a non-empty string")
    profile = _profile(profiles, str(row.get("profile")))
    if profile.get("theme") != "dark":
        raise ValueError(f"unsupported capture theme in {row['id']}")
    steps = row.get("steps")
    if not isinstance(steps, list) or any(not isinstance(step, Mapping) for step in steps):
        raise ValueError(f"{row['id']} steps must be an array of objects")
    for step in steps:
        if not isinstance(step.get("action"), str) or not step["action"]:
            raise ValueError(f"{row['id']} has an empty action recipe")
    for key in ("pointerSteps", "keyboardSteps"):
        values = row.get(key)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"{row['id']} {key} must contain non-empty strings")
    if row.get("referenceStatus") == "available":
        payload = catalog.gallery_payload(_reference_id(row), viewer_mention="@simcord-viewer")
        catalog.close_payload(payload)


def check_catalog() -> dict[str, Any]:
    """Validate manifests, recipe shape, catalog factories, and imports."""
    _validate_manifest(_COVERAGE_PATH)
    catalog = _load_catalog()
    coverage = _load_json(_COVERAGE_PATH)
    profiles = _load_json(_PROFILE_PATH)
    rows = coverage.get("rows")
    if not isinstance(rows, list):
        raise ValueError("coverage rows must be an array")
    for row in rows:
        if isinstance(row, Mapping):
            _validate_row(row, catalog, profiles)
    if tuple(catalog.REFERENCE_IDS) != (
        "REF-00-INDEX",
        "REF-10-LEGACY-EMBED",
        "REF-11-ATTACHMENTS",
        "REF-20-BUTTONS",
        "REF-30-STRING-SELECT",
        "REF-31-ENTITY-SELECTS",
        "REF-40-V2-LAYOUT-MEDIA",
        "REF-50-MODALS",
    ):
        raise ValueError("catalog reference IDs changed unexpectedly")
    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("capture runtime requires Playwright; install simcord[screenshot]") from exc
    if Image is None:
        raise RuntimeError("capture runtime requires Pillow; install simcord[preview]")
    return {"rows": len(rows), "catalog": len(catalog.REFERENCE_IDS), "playwright": True, "pillow": True}


def _bot() -> commands.Bot:
    return commands.Bot(
        command_prefix=commands.when_mentioned,
        intents=discord.Intents(guilds=True),
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _ready(page: Any, deadline_ms: float = 30_000) -> None:
    await page.wait_for_function("() => window.simcordPreview?.ready === true", timeout=deadline_ms)
    await page.wait_for_function(
        "() => !document.fonts || document.fonts.status === 'loaded'", timeout=deadline_ms
    )
    await page.wait_for_function(
        "() => [...document.images].every((image) => image.complete && image.naturalWidth > 0)",
        timeout=deadline_ms,
    )


async def _status(page: Any) -> dict[str, Any]:
    value = await page.evaluate("() => window.simcordPreview")
    if not isinstance(value, Mapping):
        raise ValueError("served page did not expose simcordPreview status")
    return dict(value)


async def _settle_action(page: Any, before: Mapping[str, Any], deadline_ms: float = 30_000) -> None:
    await page.wait_for_function(
        """(previous) => {
            const state = window.simcordPreview;
            const action = state?.lastAction;
            return state?.ready === true && action?.settlement === 'settled' &&
                (state.publishedRevision > previous.revision || state.renderGeneration > previous.generation);
        }""",
        arg={
            "revision": int(before.get("publishedRevision", 0) or 0),
            "generation": int(before.get("renderGeneration", 0) or 0),
        },
        timeout=deadline_ms,
    )
    await _ready(page, deadline_ms)


def _button_label(fixture_id: str) -> str | None:
    if ".modals.button.open." in fixture_id:
        key = fixture_id.split(".modals.button.open.", 1)[1].split(".", 1)[0]
        return {
            "choice": "Open choice modal",
            "entity": "Open entity modal",
            "text": "Open text modal",
            "upload": "Open upload modal",
        }.get(key)
    if ".button." not in fixture_id:
        return None
    key = fixture_id.split(".button.", 1)[1].rsplit(".", 1)[0]
    return {
        "danger": "Danger",
        "link": "Link",
        "primary": "Primary",
        "secondary": "Secondary",
        "success": "Success",
        "with.emoji": "With emoji",
    }.get(key)


def _select_label(fixture_id: str) -> str | None:
    for fragment, label in (
        (".string.select.", "Choose up to two destinations"),
        (".channel.select.", "Choose a text channel"),
        (".entity.channel.", "Choose a text channel"),
        (".mentionable.select.", "Choose a user or role"),
        (".entity.mentionable.", "Choose a user or role"),
        (".role.select.", "Choose a role"),
        (".entity.role.", "Choose a role"),
        (".user.select.", "Choose a user"),
        (".entity.user.", "Choose a user"),
    ):
        if fragment in fixture_id:
            return label
    return None


def _recipe(row: Mapping[str, Any]) -> tuple[str, str | None]:
    """Return a recipe only when the registered expected state has a real driver."""
    fixture_id = str(row["id"])
    variant = row.get("variant")
    expected = row.get("expected")
    steps = row.get("steps")
    if (
        not isinstance(expected, Mapping)
        or expected.get("backend") != "unchanged"
        or expected.get("visible") != f"historical {variant} surface"
        or not isinstance(steps, list)
        or not steps
        or any(
            step.get("input") != "none"
            or step.get("action") != "capture historical image without mutating state"
            for step in steps
            if isinstance(step, Mapping)
        )
        or any(not isinstance(step, Mapping) for step in steps)
    ):
        raise ValueError(f"no supported capture recipe for {fixture_id} ({variant})")

    button = _button_label(fixture_id)
    select = _select_label(fixture_id)
    if variant == "idle" and fixture_id.endswith(".idle"):
        return "idle", None
    if variant == "active" and fixture_id.endswith(".active") and button is not None:
        return "active-button", button
    if variant == "hover" and fixture_id.endswith(".hover") and button is not None:
        return "hover-button", button
    if variant == "hover" and fixture_id.endswith(".hover") and select is not None:
        return "hover-select", select
    if variant == "focus" and fixture_id.endswith(".focus") and select is not None:
        return "focus-select", select
    if variant == "open" and ".open." in fixture_id and select is not None:
        return "open-select", select
    raise ValueError(f"no supported capture recipe for {fixture_id} ({variant})")


async def _act(page: Any, recipe: tuple[str, str | None]) -> str:
    """Exercise one prevalidated recipe and reject an absent or incorrect state."""
    kind, label = recipe
    if kind == "idle":
        if (
            await page.locator(".modal-dialog").count()
            or not await page.locator(".message-surface").is_visible()
        ):
            raise ValueError("expected idle message surface is unavailable")
        return "none"

    role = "link" if label == "Link" else "button"
    if kind not in {"active-button", "hover-button"}:
        role = "combobox"
    locator = page.get_by_role(role, name=label, exact=True)
    if await locator.count() != 1 or not await locator.is_visible():
        raise ValueError(f"expected control {label!r} is unavailable")

    if kind == "active-button":
        if label == "Link":
            if await locator.get_attribute("aria-disabled") == "true" or not await locator.get_attribute(
                "href"
            ):
                raise ValueError("expected active link is unavailable")
        elif not await locator.is_enabled():
            raise ValueError(f"expected active button {label!r} is disabled")
        return f"check:button-enabled:{label}"
    if kind in {"hover-button", "hover-select"}:
        if not await locator.is_enabled():
            raise ValueError(f"expected hover control {label!r} is disabled")
        await locator.hover()
        if not await locator.evaluate("(element) => element.matches(':hover')"):
            raise ValueError(f"control {label!r} did not enter hover state")
        return f"hover:{label}"
    if kind == "focus-select":
        await locator.focus()
        if not await locator.evaluate("(element) => element.matches(':focus')"):
            raise ValueError(f"control {label!r} did not receive focus")
        return f"focus:{label}"
    if kind == "open-select":
        await locator.click()
        await _ready(page)
        listbox = page.get_by_role("listbox")
        if (
            await locator.get_attribute("aria-expanded") != "true"
            or await listbox.count() != 1
            or not await listbox.is_visible()
        ):
            raise ValueError(f"select {label!r} did not open")
        return f"click:select:{label}"
    raise ValueError(f"unsupported capture recipe {kind!r}")


def _assert_backend_unchanged(
    row: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    expected = row.get("expected")
    if not isinstance(expected, Mapping) or expected.get("backend") != "unchanged":
        raise ValueError(f"capture recipe has no supported backend expectation for {row['id']}")
    if before.get("messages") != after.get("messages"):
        raise ValueError(f"capture recipe changed backend message state for {row['id']}")


def _blocked_capture(row: Mapping[str, Any], path: Path, metadata_path: Path, reason: str) -> dict[str, Any]:
    path.unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)
    return {"fixtureId": row["id"], "status": "blocked", "reason": reason}


async def _capture_row(
    row: Mapping[str, Any], output_dir: Path, catalog: Any, profiles: Mapping[str, Any]
) -> dict[str, Any]:
    path = output_dir / _capture_name(row)
    metadata_path = path.with_suffix(".json")
    if row.get("referenceStatus") != "available":
        return _blocked_capture(row, path, metadata_path, str(row.get("reason", "reference is blocked")))
    if Image is None:
        return _blocked_capture(row, path, metadata_path, "Pillow is unavailable")

    payload: dict[str, Any] | None = None
    try:
        recipe = _recipe(row)
        profile = _profile(profiles, str(row["profile"]))
        payload = catalog.gallery_payload(_reference_id(row), viewer_mention="@simcord-viewer")
        bot = _bot()
        async with simcord.run(bot) as env:
            guild = env.create_guild("visual-reference-capture")
            channel = guild.create_text_channel("reference-gallery")
            viewer = guild.add_member(env.create_user("simcord-viewer"))
            await env.settle()
            bot_channel = env.bot.get_channel(channel.id)
            if bot_channel is None:
                raise RuntimeError("SimCord bot channel was not created")
            message = await bot_channel.send(**payload)
            await env.settle()
            async with env.preview(
                channel,
                viewers=[viewer],
                width=int(profile["viewport"]["width"]),
                height=int(profile["viewport"]["height"]),
                locale=str(profile.get("locale", "en-GB"))
                if profile.get("locale") not in {None, "unknown"}
                else "en-GB",
                timezone=str(profile.get("timezone", "UTC"))
                if profile.get("timezone") not in {None, "unknown"}
                else "UTC",
            ) as preview:
                await preview.show(message)
                snapshot = await preview.snapshot()
                validate_snapshot(snapshot)
                from playwright.async_api import async_playwright

                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    context = await browser.new_context(
                        viewport={
                            "width": int(profile["viewport"]["width"]),
                            "height": int(profile["viewport"]["height"]),
                        },
                        device_scale_factor=1,
                        reduced_motion="reduce",
                        locale="en-GB",
                        timezone_id="UTC",
                    )
                    page = await context.new_page()
                    try:
                        await page.goto(preview.url, wait_until="domcontentloaded", timeout=30_000)
                        await _ready(page)
                        action = await _act(page, recipe)
                        final_snapshot = await preview.snapshot()
                        validate_snapshot(final_snapshot)
                        _assert_backend_unchanged(row, snapshot, final_snapshot)

                        crop = row.get("crop")
                        scope = crop.get("scope") if isinstance(crop, Mapping) else None
                        surface = (
                            page.get_by_role("listbox")
                            if recipe[0] == "open-select" and scope == "dialog"
                            else page.locator(".message-surface")
                        )
                        if not await surface.is_visible():
                            raise ValueError("expected capture surface is unavailable")
                        if action == "none":
                            capture = await preview.screenshot(path, mode="surface")
                            geometry = dict(capture.geometry)
                            capture_action = capture.action
                            capture_profile = dict(capture.profile)
                        else:
                            await surface.screenshot(path=str(path), type="png")
                            box = await surface.bounding_box()
                            geometry = {"surface": dict(box) if isinstance(box, Mapping) else {}}
                            status = await _status(page)
                            capture_action = status.get("lastAction")
                            capture_profile = dict(snapshot.get("profile", {}))
                        metadata = _scrub(
                            {
                                "schemaVersion": 1,
                                "profile": capture_profile,
                                "action": capture_action,
                                "actionRecipe": action,
                                "geometry": geometry,
                                "state": final_snapshot,
                                "publishedRevision": final_snapshot.get("publishedRevision"),
                                "renderGeneration": final_snapshot.get("renderGeneration"),
                                "capturedAt": datetime.now(UTC).isoformat(),
                            }
                        )
                        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
                        return {
                            "fixtureId": row["id"],
                            "status": "captured",
                            "path": path.name,
                            "action": action,
                        }
                    finally:
                        await context.close()
                        await browser.close()
    except Exception as exc:
        return _blocked_capture(row, path, metadata_path, str(exc))
    finally:
        if payload is not None:
            with suppress(Exception):
                catalog.close_payload(payload)


async def capture(rows: Sequence[Mapping[str, Any]], output_dir: Path) -> tuple[list[dict[str, Any]], int]:
    catalog = _load_catalog()
    profiles = _load_json(_PROFILE_PATH)
    results = [await _capture_row(row, output_dir, catalog, profiles) for row in rows]
    blocked = any(result.get("status") == "blocked" for result in results)
    (output_dir / "capture-report.json").write_text(
        json.dumps(_scrub({"rows": results}), indent=2) + "\n", encoding="utf-8"
    )
    return results, 2 if blocked else 0


def _select_rows(args: argparse.Namespace) -> list[Mapping[str, Any]]:
    coverage = _load_json(_COVERAGE_PATH)
    rows = coverage.get("rows")
    if not isinstance(rows, list):
        raise ValueError("coverage rows must be an array")
    if args.fixture:
        selected = [row for row in rows if isinstance(row, Mapping) and row.get("id") == args.fixture]
    elif args.family:
        selected = [row for row in rows if isinstance(row, Mapping) and row.get("family") == args.family]
    else:
        selected = [row for row in rows if isinstance(row, Mapping)]
    if not selected:
        raise ValueError("selection matched no coverage rows")
    return selected


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture served SimCord visual reference states.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--check", action="store_true", help="validate catalog, recipes and runtime imports"
    )
    selection.add_argument("--family", help="capture one coverage family")
    selection.add_argument("--all", action="store_true", help="capture every runnable coverage row")
    selection.add_argument("--fixture", help="capture one exact coverage fixture ID")
    parser.add_argument("--output-dir", type=Path, default=Path(".discord-reference-captures/current"))
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(check_catalog(), indent=2))
        return 0
    if not (args.family or args.all or args.fixture):
        parser.error("one of --check, --family, --all or --fixture is required")
    try:
        rows = _select_rows(args)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        results, code = asyncio.run(capture(rows, args.output_dir))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}))
        return 2
    print(json.dumps({"rows": results, "exitCode": code}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
