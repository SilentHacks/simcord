"""Shared helpers for the ``test_preview*`` integration suite.

PIL is imported lazily inside the image builders so importing this module
never requires the optional ``preview``/``screenshot`` extras.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

__all__ = ["action_body", "gif_bytes", "png_bytes", "preview_headers"]


def png_bytes() -> bytes:
    """A minimal valid 2x2 RGBA PNG."""
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (2, 2), (20, 40, 60, 255)).save(output, format="PNG")
    return output.getvalue()


def gif_bytes() -> bytes:
    """A minimal two-frame 2x2 animated GIF."""
    from PIL import Image

    first = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
    second = Image.new("RGBA", (2, 2), (0, 0, 255, 255))
    output = io.BytesIO()
    first.save(output, format="GIF", save_all=True, append_images=[second], duration=100, loop=0)
    return output.getvalue()


def preview_headers(preview: Any, context_id: str | None = None) -> dict[str, str]:
    """Request headers carrying the preview capability (and page context)."""
    headers = {"X-Simcord-Capability": preview.capability}
    if context_id is not None:
        headers["X-Simcord-Context"] = context_id
    return headers


def action_body(page: Any, kind: str, sequence: Any, **fields: Any) -> dict[str, Any]:
    """Build a ``preview.action`` request envelope for ``page``.

    ``sequence``/``request_id``/``generation``/``bot_generation``/``kind`` are
    filled in: the generation defaults from ``page`` — a ``_Page`` (whose
    ``preview.env`` supplies ``bot_generation``) or a ``/api/pages`` JSON
    ``context`` mapping (which needs ``env=`` or an explicit
    ``bot_generation=``). Every other keyword argument passes straight
    through, so kind-specific fields (``custom_id``, ``values``,
    ``modal_handle``, ``target_id``, ``viewer_id``, ``published_revision``)
    and deliberate bad values both work.
    """
    env = fields.pop("env", None)
    if isinstance(page, Mapping):
        generation = page["generation"]
    else:
        generation = page.generation
        if env is None:
            env = page.preview.env
    if env is None and "bot_generation" not in fields:
        raise TypeError("action_body() for a JSON page context needs env= or bot_generation=")
    body = {
        "sequence": sequence,
        "request_id": f"action-{sequence}",
        "generation": generation,
        "bot_generation": env._generation if env is not None else None,
        "kind": kind,
    }
    body.update(fields)
    return body
