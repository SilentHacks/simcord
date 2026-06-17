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
