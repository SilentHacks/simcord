"""Capture user-prepared Discord UI without automating Discord interactions.

Login, navigation, scrolling, clicks, typing and focus are exclusively manual.
Only viewport screenshots and rendered browser/font geometry are observed.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import io
import json
import platform
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


def write_json(path: Path, value: object) -> None:
    """Replace JSON atomically; interrupted writes cannot truncate prior captures."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def slug(value: str) -> str:
    if not _SLUG.fullmatch(value):
        raise ValueError(
            "Names need 1-80 letters/numbers/dots/dashes/underscores, starting with a letter or number"
        )
    return value.lower()


def crop_box(value: str, viewport: dict[str, int]) -> tuple[int, int, int, int]:
    try:
        x, y, width, height = (int(part) for part in value.replace(",", " ").split())
    except ValueError as exc:
        raise ValueError("Crop must be four CSS-pixel integers: x y width height") from exc
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("Crop must have nonnegative coordinates and positive dimensions")
    if x + width > viewport["width"] or y + height > viewport["height"]:
        raise ValueError(
            "Crop must fit entirely inside the visible viewport; scroll manually or capture sections"
        )
    return x, y, x + width, y + height


def load_fixtures(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("fixtures"), list):
        raise ValueError("Expected a version-1 bot-fixtures.json from discord_reference_bot.py")
    fixtures = {}
    for record in data["fixtures"]:
        key = record["referenceId"]
        slug(key)
        if key in fixtures:
            raise ValueError(f"Duplicate fixture {key}")
        canonical = json.dumps(
            record["normalizedPayload"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        if hashlib.sha256(canonical).hexdigest() != record["normalizedPayloadHash"]:
            raise ValueError(f"Fixture payload hash differs: {key}")
        record["generator"] = {key: data[key] for key in ("source", "discordPyVersion", "hashNormalization")}
        fixtures[key] = record
    return fixtures


def export_pack(directory: Path, destination: Path) -> None:
    """Export only declared evidence, never browser profiles or unrelated files."""
    pack = json.loads((directory / "reference-pack.json").read_text(encoding="utf-8"))
    names = {"reference-pack.json"}
    expected_hashes = {}
    for record in pack["references"]:
        expected_hashes[record["name"]] = record["sha256"]
        expected_hashes.update({f"assets/{name}": digest for name, digest in record["assetHashes"].items()})
        names.update((record["name"], Path(record["name"]).with_suffix(".json").as_posix()))
        names.update(record.get("assetFiles", []))
        if Path(record["name"]).name != record["name"] or not record["name"].endswith(".png"):
            raise ValueError("Capture names must be plain PNG filenames")
        expected_assets = {f"assets/{name}" for name in record["assetHashes"]}
        if set(record.get("assetFiles", [])) != expected_assets:
            raise ValueError("Only declared fixture assets can be exported")
        for name in expected_assets:
            if len(Path(name).parts) != 2:
                raise ValueError("Assets must be plain filenames within assets/")
    root = directory.resolve()
    files = []
    for name in sorted(names):
        relative = Path(name)
        source = directory / relative
        if relative.is_absolute() or ".." in relative.parts or not source.resolve().is_relative_to(root):
            raise ValueError("Evidence paths must stay inside the capture directory")
        if not source.is_file():
            raise ValueError(f"Missing evidence file: {name}")
        if (
            name in expected_hashes
            and hashlib.sha256(source.read_bytes()).hexdigest() != expected_hashes[name]
        ):
            raise ValueError(f"Evidence hash differs: {name}; preserve the original or recapture")
        files.append((source, relative.as_posix()))
    if destination.exists():
        raise ValueError("Export already exists; choose a new ZIP path")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in files:
            archive.write(source, name)


async def capture(
    page: Any,
    directory: Path,
    fixture: dict[str, Any],
    state: str,
    crop: str,
    profile: dict[str, str],
    trace: dict[str, str],
    *,
    offline: bool = False,
) -> dict[str, Any]:
    from PIL import Image

    url = urlsplit(page.url)
    if not offline and (
        url.scheme != "https" or url.hostname != "discord.com" or not url.path.startswith("/channels/")
    ):
        raise ValueError(
            "Capture refused: manually open the test channel; login/settings credentials must not be captured"
        )
    if not offline and url.path.rstrip("/").split("/")[-1] != fixture["channelId"]:
        raise ValueError("Capture refused: the open channel does not match this bot fixture")
    facts = await page.evaluate("""() => ({
      width: innerWidth, height: innerHeight, deviceScaleFactor: devicePixelRatio,
      focused: document.hasFocus(), locale: navigator.language,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      browser: navigator.userAgent, reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      loadedFonts: [...document.fonts].filter(f => f.status === 'loaded').map(f => ({family:f.family,style:f.style,weight:f.weight}))
    })""")
    if facts["deviceScaleFactor"] != 1:
        raise ValueError("Capture requires device scale 1: reset browser zoom to 100%; never rescale the PNG")
    if not offline and not facts["focused"]:
        raise ValueError("Discord was not focused: return to its window before the countdown ends")
    if crop.strip().lower() == "auto":
        bounds = await page.evaluate(
            """fixture => {
          const message = document.getElementById(`chat-messages-${fixture.channelId}-${fixture.messageId}`)
            || document.querySelector(`[data-list-item-id="chat-messages__${fixture.channelId}-${fixture.messageId}"]`);
          const node = fixture.referenceId === 'REF-50-MODALS'
            ? document.querySelector('[role="dialog"]') || message : message;
          if (!node) return null;
          const r = node.getBoundingClientRect();
          return [Math.floor(r.x), Math.floor(r.y), Math.ceil(r.right)-Math.floor(r.x), Math.ceil(r.bottom)-Math.floor(r.y)];
        }""",
            fixture,
        )
        if bounds is None:
            raise ValueError("No rendered fixture bounds found; supply an explicit x y width height crop")
        crop = " ".join(str(value) for value in bounds)
    box = crop_box(crop, facts)
    name = f"{slug(fixture['referenceId'])}-{slug(state)}.png"
    png_path = directory / name
    metadata_path = png_path.with_suffix(".json")
    if png_path.exists() or metadata_path.exists():
        raise ValueError(
            "Capture already exists: use a distinct state/section name; evidence is never overwritten"
        )
    pack_path = directory / "reference-pack.json"
    pack = (
        json.loads(await asyncio.to_thread(pack_path.read_text, encoding="utf-8"))
        if pack_path.exists()
        else {"schemaVersion": 1, "evidenceStatus": "observed-unreviewed", "references": []}
    )
    pixels = await page.screenshot(full_page=False, animations="allow", caret="initial", scale="device")
    with Image.open(io.BytesIO(pixels)) as image:
        if image.size != (facts["width"], facts["height"]):
            raise ValueError("Screenshot pixels differ from viewport geometry; do not normalize the image")
        cropped = image.crop(box)
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        png = buffer.getvalue()
    record = {
        "fixtureId": f"local.{slug(fixture['referenceId'])}.{slug(state)}",
        "name": name,
        "source": "offline-smoke" if offline else "discord-web",
        "state": state,
        "observedAt": dt.datetime.now(dt.UTC).isoformat(),
        "sha256": hashlib.sha256(png).hexdigest(),
        "profile": {"os": platform.platform(), "observed": facts, "userAttested": profile},
        "crop": {"x": box[0], "y": box[1], "width": box[2] - box[0], "height": box[3] - box[1]},
        "geometry": {"surface": [0, 0, box[2] - box[0], box[3] - box[1]]},
        "manualTrace": trace,
        "fixture": fixture,
        "normalizedPayloadHash": fixture["normalizedPayloadHash"],
        "assetHashes": fixture["assetHashes"],
        "assetFiles": [f"assets/{name}" for name in fixture["assetHashes"]],
        "evidenceStatus": "observed-unreviewed",
        "calibrated": False,
    }
    pack["references"].append(record)
    await asyncio.to_thread(png_path.write_bytes, png)
    await asyncio.to_thread(write_json, metadata_path, record)
    await asyncio.to_thread(write_json, pack_path, pack)
    return record


async def prompt(question: str) -> str:
    return await asyncio.to_thread(input, question)


async def run(args: argparse.Namespace) -> None:
    from playwright.async_api import async_playwright

    fixtures = await asyncio.to_thread(load_fixtures, args.fixtures)
    await asyncio.to_thread(args.output_dir.mkdir, parents=True, exist_ok=False)
    assets = args.output_dir / "assets"
    await asyncio.to_thread(assets.mkdir)
    for fixture in fixtures.values():
        for name, expected in fixture["assetHashes"].items():
            if Path(name).name != name or name in {".", ".."}:
                raise ValueError("Fixture asset names must be plain filenames")
            source = args.fixtures.parent / "assets" / name
            data = await asyncio.to_thread(source.read_bytes)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"Fixture asset hash differs: {name}")
            await asyncio.to_thread((assets / name).write_bytes, data)
    await asyncio.to_thread(args.browser_profile.mkdir, parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(args.browser_profile),
            headless=False,
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=1,
        )
        try:
            page = context.pages[0]
            await page.goto("https://discord.com/app")
            print("Log in and navigate MANUALLY in this tab. No Discord actions will be automated.")
            print(f"Private evidence: {args.output_dir}\nFixtures: {', '.join(fixtures)}")
            await prompt("Finish manual login and profile settings; press Enter here when ready: ")
            profile = {}
            for key, label in (
                ("clientBuild", "Discord build/version (copy manually; 'unknown' if unavailable)"),
                ("theme", "Discord theme"),
                ("density", "Cozy/compact display mode"),
                ("chatFontSize", "Discord chat font size"),
                ("clientLocale", "Discord UI language"),
                ("clientMotion", "Discord animation/reduced-motion setting"),
            ):
                profile[key] = (await prompt(f"{label}: ")).strip() or "unknown"
            while True:
                reference_id = (await prompt("Fixture ID (or q to quit): ")).strip().upper()
                if reference_id == "Q":
                    break
                if reference_id not in fixtures:
                    print("Choose one of the listed IDs.")
                    continue
                try:
                    state = slug(
                        (await prompt("State name (e.g. idle, user-open, text-validation): ")).strip()
                    )
                    crop = await prompt(
                        "Crop: 'auto' for message/modal, or CSS pixels x y width height (include open menus): "
                    )
                    trace = {
                        "actions": await prompt("Manual actions performed (or none): "),
                        "observations": await prompt(
                            "Commit/cancel/focus/selected values/callback observations: "
                        ),
                    }
                    print(
                        f"Capture in {args.delay:g}s. Return to Discord and prepare the state; do not switch back."
                    )
                    await asyncio.sleep(args.delay)
                    record = await capture(
                        page, args.output_dir, fixtures[reference_id], state, crop, profile, trace
                    )
                    print(f"Saved {record['name']}. No parity certification is implied.")
                except ValueError as exc:
                    print(f"Not captured: {exc}")
        finally:
            await context.close()


async def check() -> None:
    from PIL import Image
    from playwright.async_api import async_playwright

    assert crop_box("10 20 60 40", {"width": 100, "height": 100}) == (10, 20, 70, 60)
    for invalid in ("../escape", "", "x/y"):
        try:
            slug(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe capture name accepted")
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 100, "height": 100}, device_scale_factor=1)
                await page.set_content(
                    '<body style="margin:0;background:red"><input id="entry" value="manual-entry"><div id="chat-messages-123-456" style="position:absolute;left:10px;top:20px;width:60px;height:40px;background:blue"></div></body>'
                )
                await page.locator("#entry").focus()  # Only the generated offline page, never Discord.
                fixture = {
                    "referenceId": "REF-SMOKE",
                    "channelId": "123",
                    "messageId": "456",
                    "normalizedPayloadHash": "0" * 64,
                    "assetHashes": {},
                }
                result = await capture(
                    page, directory, fixture, "idle", "auto", {}, {"actions": "none"}, offline=True
                )
                assert await page.evaluate("document.activeElement.id") == "entry"
                assert await page.locator("#entry").input_value() == "manual-entry"
                with Image.open(directory / result["name"]) as image:
                    assert image.size == (60, 40)
                    assert image.convert("RGB").getpixel((30, 20)) == (0, 0, 255)
                assert result["source"] == "offline-smoke" and not result["calibrated"]
                try:
                    await capture(page, directory, fixture, "idle", "10 20 60 40", {}, {}, offline=True)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Existing evidence overwritten")
                try:
                    await capture(page, directory, fixture, "login", "10 20 60 40", {}, {})
                except ValueError:
                    pass
                else:
                    raise AssertionError("Non-Discord/login capture accepted")
                dense = await browser.new_page(viewport={"width": 100, "height": 100}, device_scale_factor=2)
                try:
                    await capture(dense, directory, fixture, "dense", "10 20 60 40", {}, {}, offline=True)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Scaled screenshot accepted")
                (directory / "browser-profile").mkdir()
                (directory / "browser-profile" / "Cookies").write_bytes(b"offline-private-marker")
                export_pack(directory, directory / "evidence.zip")
                with zipfile.ZipFile(directory / "evidence.zip") as archive:
                    assert set(archive.namelist()) == {
                        "reference-pack.json",
                        "ref-smoke-idle.png",
                        "ref-smoke-idle.json",
                    }
                (directory / result["name"]).write_bytes(b"altered-image")
                try:
                    export_pack(directory, directory / "altered.zip")
                except ValueError:
                    pass
                else:
                    raise AssertionError("Altered evidence exported")
            finally:
                await browser.close()
    print(
        "Local capture check passed: real browser pixels, crop, no overwrite/login capture, private export."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="exercise offline capture without Discord or credentials"
    )
    parser.add_argument(
        "--export", type=Path, help="export an evidence directory without launching a browser"
    )
    parser.add_argument("--zip", type=Path, help="destination for --export")
    parser.add_argument("--fixtures", type=Path, help="bot-fixtures.json written by the reference bot")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".discord-reference-captures/private")
        / dt.datetime.now(dt.UTC).strftime("local-%Y%m%d-%H%M%S"),
    )
    parser.add_argument(
        "--browser-profile", type=Path, default=Path(".discord-reference-captures/browser-profile")
    )
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=700)
    parser.add_argument(
        "--delay", type=float, default=8, help="seconds to return to Discord and prepare the state"
    )
    args = parser.parse_args()
    if args.check:
        asyncio.run(check())
    elif args.export:
        if args.zip is None:
            parser.error("--export requires --zip")
        export_pack(args.export, args.zip)
        print(
            f"Private evidence ZIP: {args.zip}. Review images before sharing; browser credentials are excluded."
        )
    else:
        if args.fixtures is None:
            parser.error("--fixtures is required")
        if args.width <= 0 or args.height <= 0 or args.delay <= 0:
            parser.error("width, height and delay must be positive")
        try:
            asyncio.run(run(args))
        except (KeyboardInterrupt, EOFError):
            print("Capture session ended; existing evidence is retained.")


if __name__ == "__main__":
    main()
