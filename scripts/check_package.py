"""Check that built distributions contain the packaged Preview application."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

_PREVIEW = Path(__file__).resolve().parents[1] / "src" / "simcord" / "preview"
_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "preview"
_TOOLS = (
    "scripts/capture_visual_reference.py",
    "scripts/check_package.py",
    "scripts/compare_visual_reference.py",
    "scripts/discord_reference_bot.py",
)


def source_files(directory: Path) -> set[str]:
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def wheel_requirements() -> set[str]:
    static = source_files(_PREVIEW / "static")
    if not {"index.html", "app.js", "preview.css", "fonts/manifest.json"} <= static:
        raise SystemExit(f"required Preview assets missing from source: {sorted(static)}")
    return {
        *(f"simcord/preview/static/{name}" for name in static),
        "simcord/preview/protocol.schema.json",
    }


def sdist_requirements() -> set[str]:
    preview = source_files(_PREVIEW)
    fixtures = source_files(_FIXTURES)
    if not fixtures:
        raise SystemExit(f"Preview release fixtures missing from source: {_FIXTURES}")
    return {
        *(f"src/simcord/preview/{name}" for name in preview),
        *(f"tests/fixtures/preview/{name}" for name in fixtures),
        *_TOOLS,
    }


def has_project_license(names: set[str], *, wheel: bool) -> bool:
    if wheel:
        return any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
    return "LICENSE" in names


def check(path: Path) -> None:
    wheel = path.suffix == ".whl"
    names = members(path)
    required = wheel_requirements() if wheel else sdist_requirements()
    missing = {
        name
        for name in required
        if not any(member == name or member.endswith(f"/{name}") for member in names)
    }
    if not has_project_license(names, wheel=wheel):
        missing.add("project LICENSE")
    if missing:
        description = "wheel assets" if wheel else "sdist Preview release files"
        raise SystemExit(f"{path}: missing packaged {description}: {sorted(missing)}")
    print(f"{path}: Preview release files present")


def members(path: Path) -> set[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {name for name in archive.namelist() if not name.endswith("/")}
    if path.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")):
        with tarfile.open(path, "r:*") as archive:
            return {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}
    raise SystemExit(f"unsupported distribution: {path}")


def main() -> int:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.*"))
    if not wheels or not sdists:
        raise SystemExit(f"expected at least one wheel and one sdist under {directory}")
    for artifact in (*wheels, *sdists):
        check(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
