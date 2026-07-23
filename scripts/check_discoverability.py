"""Validate the rendered documentation's public discovery contract."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


class HeadMetadata(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.meta: dict[tuple[str, str], str] = {}
        self.links: dict[tuple[str, str], str] = {}
        self.json_ld: list[dict[str, object]] = []
        self._json_ld_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            if name := values.get("name"):
                self.meta[("name", name)] = values.get("content", "")
            if prop := values.get("property"):
                self.meta[("property", prop)] = values.get("content", "")
        elif tag == "link":
            if rel := values.get("rel"):
                self.links[("rel", rel)] = values.get("href", "")
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._json_ld_parts is not None:
            self.json_ld.append(json.loads("".join(self._json_ld_parts)))
            self._json_ld_parts = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)


def parse_html(path: Path) -> HeadMetadata:
    parser = HeadMetadata()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    failures: list[str] = []
    html_files = [path for path in site.rglob("*.html") if path.name != "404.html"]
    require(bool(html_files), f"no rendered HTML found under {site}", failures)

    for path in html_files:
        metadata = parse_html(path)
        relative = path.relative_to(site)
        require(bool(metadata.title.strip()), f"{relative}: missing title", failures)
        require(
            bool(metadata.meta.get(("name", "description"))),
            f"{relative}: missing meta description",
            failures,
        )
        require(
            bool(metadata.links.get(("rel", "canonical"))),
            f"{relative}: missing canonical URL",
            failures,
        )

    home = parse_html(site / "index.html")
    for prop in ("og:title", "og:description", "og:url", "og:image"):
        require(bool(home.meta.get(("property", prop))), f"home: missing {prop}", failures)
    for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        require(bool(home.meta.get(("name", name))), f"home: missing {name}", failures)
    require(
        any(item.get("@type") == "SoftwareSourceCode" for item in home.json_ld),
        "home: missing SoftwareSourceCode JSON-LD",
        failures,
    )

    required_paths = (
        "guides/testing-discord-py-bots/index.html",
        "guides/test-without-token/index.html",
        "guides/testing-slash-commands/index.html",
        "guides/mocks-vs-simulation/index.html",
        "guides/ai-coding-agents/index.html",
        "llms.txt",
        "robots.txt",
        "sitemap.xml",
    )
    for relative in required_paths:
        require((site / relative).is_file(), f"missing rendered {relative}", failures)

    if (site / "sitemap.xml").is_file():
        locations = {
            node.text for node in ET.parse(site / "sitemap.xml").getroot().iter() if node.tag.endswith("loc")
        }
        for slug in (
            "guides/testing-discord-py-bots/",
            "guides/test-without-token/",
            "guides/testing-slash-commands/",
            "guides/mocks-vs-simulation/",
            "guides/ai-coding-agents/",
        ):
            require(
                f"https://simcord.readthedocs.io/{slug}" in locations,
                f"sitemap: missing {slug}",
                failures,
            )

    if failures:
        print("Discoverability checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"Discoverability checks passed for {len(html_files)} HTML pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
