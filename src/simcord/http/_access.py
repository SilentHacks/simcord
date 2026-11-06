"""Bot-authenticated access to a channel's text actions."""

from __future__ import annotations

from ..backend.models import Channel
from ..enums import ChannelType
from .router import RequestContext


def require_text_access(ctx: RequestContext, channel_id: int) -> Channel:
    channel = ctx.require_channel_permissions(channel_id)
    if channel.type in (ChannelType.VOICE, ChannelType.STAGE_VOICE):
        ctx.require_channel_permissions(channel_id, "connect", denied_code=50001)
    return channel
