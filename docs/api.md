---
title: "API reference"
description: "Complete SimCord API reference: run, Env, the builder handles (Guild, Channel, Role, User), the MemberActor verbs, InteractionResult, and the error types — generated from the source."
---

# API reference

The complete public API, generated from the source. Everything here is importable from the
top-level `simcord` package.

!!! tip "New here?"
    Read [Core concepts](concepts.md) first — it explains how these objects relate
    (builders arrange the world, actors act as users, queries assert) before you dive into
    signatures.

## Entry point

::: simcord.run

::: simcord.Env

### `Env(settle_timeout=5.0)`

`settle_timeout` is the default maximum time spent joining runnable bot work. A direct
`await env.settle(timeout=...)` override is available; `idle=` is only the polling interval.
Use `await env.external_wait(awaitable, reason="...")` for one explicitly scoped external
input wait. Unknown waits remain active and produce a diagnostic timeout.

## Builders

Synchronous, omnipotent handles for arranging the virtual Discord. Returned by `env`/`guild`
methods. See [Core concepts → Builders](concepts.md#builders-arrange-the-world).

::: simcord.GuildHandle

::: simcord.ChannelHandle

::: simcord.RoleHandle

::: simcord.UserHandle

## Actors

The simulated human that drives your bot. Created by `guild.add_member(...)`. See
[Core concepts → Actors](concepts.md#actors-act-as-a-real-user).

::: simcord.MemberActor

## Local preview

The optional [`Env.preview`](guides/preview.md#start-and-stop-a-session) context manager serves
viewer-authorized messages and real component callbacks from a loopback browser. It is not a
Discord connection. Install `simcord[preview]` for the bridge, or
`simcord[screenshot]` and `playwright install chromium` for managed PNG capture.

::: simcord.preview.Preview

::: simcord.preview.PreviewCapture

`PreviewCapture` is an immutable report. Its `ready`, `complete`, and `calibrated` fields are
independent; inspect `diagnostics`, `action`, `profile`, and `geometry` rather than inferring
success from a PNG path. Internal `/api/*` payloads and DOM/CSS names are not extension APIs.

## Results

Returned by the interaction verbs (`slash`, `context_menu`, `click`, `select`,
`submit_modal`). See [Slash commands → Inspecting the result](guides/interactions.md#inspecting-the-result).

::: simcord.InteractionResult

::: simcord.ResponseMessage

## Assertions

Runner-agnostic helpers whose failure messages print what the bot actually did. See
[Errors & diagnostics → Assertions](guides/diagnostics.md#assertions).

::: simcord.assert_sent

::: simcord.assert_responded

::: simcord.assert_message

::: simcord.assert_error

::: simcord.assert_no_errors

## Errors

::: simcord.BackendError

::: simcord.SetupError

::: simcord.RouteNotImplemented

::: simcord.UnsupportedField
