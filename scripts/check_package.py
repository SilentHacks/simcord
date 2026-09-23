"""Check that built distributions contain the packaged Preview application."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

_REQUIRED = {
    "simcord/preview/static/index.html",
    "simcord/preview/static/app.js",
    "simcord/preview/static/components.js",
    "simcord/preview/static/preview.css",
}


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
    artifacts = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.*"))
    if not artifacts:
        raise SystemExit(f"no distributions found under {directory}")
    for artifact in artifacts:
        names = members(artifact)
        missing = {
            required
            for required in _REQUIRED
            if not any(name == required or name.endswith(f"/{required}") for name in names)
        }
        if missing:
            raise SystemExit(f"{artifact}: missing packaged Preview assets: {sorted(missing)}")
        print(f"{artifact}: Preview assets present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
