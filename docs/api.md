---
title: "API reference"
description: "Complete SimCord API reference: run, Env, the builder handles (Guild, Channel, Role, User), the MemberActor verbs, InteractionResult, and the error types — generated from the source."
---

# API reference

The complete public API, generated from the source. Everything here is importable from the
top-level `simcord` package (conventionally aliased `import simcord as dpt`).

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

## Errors

::: simcord.BackendError

::: simcord.SetupError

::: simcord.RouteNotImplemented
