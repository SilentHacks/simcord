"""Run a real panel -> modal -> message edit preview flow.

Install the optional browser tools before running this example::

    python -m pip install "simcord[screenshot]"
    playwright install chromium
    python examples/preview_example.py

The callback is dispatched by SimCord's actor, not mocked by the example. The
capture report is printed so automation can inspect completeness diagnostics.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import simcord

try:
    from .bot import create_bot
except ImportError:  # Executed directly as ``python examples/preview_example.py``.
    from bot import create_bot


async def main(destination: str = "preview-example.png") -> None:
    async with simcord.run(create_bot()) as env:
        guild = env.create_guild("Preview example")
        channel = guild.create_text_channel("preview")
        alice = guild.add_member(env.create_user("alice"))

        await alice.send(channel, "!panel")
        panel = channel.last_message

        async with env.preview(channel, viewers=[alice]) as preview:
            opened = await alice.click(panel, custom_id="panel:edit")
            updated = await alice.submit_modal(opened, {"title": "Edited from the preview"})
            await preview.refresh()

            capture = await preview.screenshot(Path(destination), allow_incomplete=True)
            print(
                json.dumps(
                    {
                        "message": updated.response.message.content,
                        "capture": {
                            "path": capture.path,
                            "complete": capture.complete,
                            "diagnostics": list(capture.diagnostics),
                            "profile": dict(capture.profile),
                        },
                    },
                    indent=2,
                    default=str,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
