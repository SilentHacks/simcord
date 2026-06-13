---
title: "Components & modals"
description: "Test discord.py buttons, select menus and modals with SimCord. Real View dispatch, disabled/missing component rejection, ephemeral access rules, and modal submission."
---

# Components & modals

Buttons, select menus and modals go through discord.py's **real `View` dispatch** and modal
handling. SimCord only lets a user do what they physically could in the client: you can't
click a button that's missing, disabled, or on a message you can't see. Trying to raises a
`SetupError` against your test setup — surfacing the kind of bug that would otherwise only
show up in production.

## Clicking buttons

`actor.click(message, *, label=… | custom_id=…)` clicks a button by its visible label or its
`custom_id`:

```python
result  = await alice.slash(channel, "delete-data")
confirm = await alice.click(result.response.message, label="Confirm")
assert confirm.response.content == "Deleted."
```

The click runs through your `View.button` callback for real, returning an
[`InteractionResult`](../api.md#simcord.InteractionResult) just like a slash command — so you can
assert on `confirm.response`, `confirm.ephemeral`, `confirm.deferred`, and so on.

!!! info "You can only click what's clickable"
    - A button that doesn't exist (wrong label/`custom_id`) → `SetupError`.
    - A **disabled** button → `SetupError` ("a real user could not interact with it").
    - A button on an **ephemeral** message not addressed to this user → `SetupError`.

    These are deliberate: each corresponds to something impossible in the real client, so
    catching it keeps your test honest.

## Selecting in menus

`actor.select(message, values, *, custom_id=…)` chooses one or more values in a string
select menu:

```python
picked = await alice.select(message, ["green"], custom_id="color")
assert picked.response.content == "You picked green"
```

Selecting a value that isn't an option fails with a `SetupError` listing the valid options.

For **entity selects** (user / role / channel / mentionable), pass the handles a real user
could pick instead of strings — `actor.select` builds the resolved data so the bot's callback
receives real `discord.Member` / `Role` / channel objects:

```python
result = await alice.slash(channel, "assign")
await alice.select(result.response.message, [alice, bob], custom_id="who")   # UserSelect
await alice.select(result.response.message, [helper_role], custom_id="role") # RoleSelect
await alice.select(result.response.message, [general], custom_id="chan")     # ChannelSelect
```

Passing the wrong handle kind for a select, or more values than its `max_values`, fails with
a `SetupError`.

## Submitting modals

When the bot responds to an interaction with a modal, the result captures it as
`result.modal`. Fill it in and submit with `actor.submit_modal(shown, values)`, keyed by
each input's `custom_id`:

```python
shown = await alice.slash(channel, "feedback")
assert shown.modal is not None

submitted = await alice.submit_modal(shown, {"name": "Alice", "comment": "Great bot"})
assert submitted.response.content == "Thanks Alice"
```

`submit_modal` dispatches a real `MODAL_SUBMIT` interaction, so your `Modal.on_submit`
callback runs and you assert on the returned result the same way as everywhere else.

## A full confirm-flow example

```python
import discord

async def test_confirm_flow(simcord_env):
    guild = simcord_env.create_guild()
    channel = guild.create_text_channel("general")
    alice = guild.add_member(simcord_env.create_user("alice"))

    # The command posts a message with Confirm / Cancel buttons.
    started = await alice.slash(channel, "purge")
    prompt = started.response.message
    assert "Are you sure?" in prompt.content

    # Cancel does nothing destructive…
    cancelled = await alice.click(prompt, label="Cancel")
    assert cancelled.response.content == "Cancelled."

    # …and a disabled Confirm after cancel can't be clicked.
    with pytest.raises(simcord.SetupError):
        await alice.click(prompt, label="Confirm")
```

## Component timeouts

A `View(timeout=…)` fires `on_timeout` after the timeout elapses. Don't wait in real time —
fast-forward the virtual clock:

```python
await alice.slash(channel, "offer")    # posts a View(timeout=60)
await simcord_env.advance_time(60)     # instant
assert channel.last_message.components == []   # bot disabled the buttons on timeout
```

See [Time control](time-control.md) for the details.

## Persistent views across a restart

Persistent views (`timeout=None` with a fixed `custom_id`, registered with `bot.add_view` in
`setup_hook`) must keep working after the bot restarts. `env.restart_bot(new_bot)` simulates
that: it detaches the current bot, attaches a freshly built one, and replays the world so the
new client repopulates its cache — without rebuilding the guilds, channels or messages.

```python
result = await alice.slash(channel, "panel")   # posts a persistent View
panel = result.response.message

await env.restart_bot(create_bot())             # a brand-new instance

clicked = await alice.click(panel, custom_id="panel:refresh")
assert clicked.response.content == "Refreshed"  # the new bot re-attached its view
```

Pass a freshly built client — re-running the same instance would re-execute `setup_hook`
(reloading extensions). The virtual clock is preserved, so the world's time does not rewind.

## Next

- [Slash commands](interactions.md) — invoking commands and the interaction lifecycle.
- [Time control](time-control.md) — firing view timeouts and cooldowns instantly.
- [Recipes](../cookbook.md) — a reusable paginator test, among others.
