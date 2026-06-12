---
title: "Slash commands & interactions"
description: "Test discord.py slash commands, context menus and autocomplete with SimCord. Validated options, resolved data, the interaction lifecycle (defer, followups, double-ack), and ephemeral visibility."
---

# Slash commands & interactions

SimCord drives application commands through a real `INTERACTION_CREATE` gateway event, so
discord.py's transformers, namespaces, the command tree and the full response lifecycle all
run unmodified. This guide covers invoking commands and inspecting the result; for buttons,
selects and modals see [Components & modals](components.md).

## Invoking commands

```python
result = await alice.slash(channel, "ban", user=target, reason="spam")
result = await alice.slash(channel, "config set", key="lang", value="en")   # subcommands
result = await alice.context_menu(channel, "Report", target)                # context menus
```

- **Slash commands** — `actor.slash(channel, name, **options)`. Pass options as keyword
  arguments.
- **Subcommands and groups** — put spaces in the name: `"config set"`,
  `"config role add"`. SimCord walks the tree to the leaf for you.
- **Context menus** — `actor.context_menu(channel, name, target)`, where `target` is a
  `MemberActor` (user command) or a message (message command).

Options are **validated against the command's declared options** — names, types, choices,
required-ness — and snowflake options (members, channels, roles) carry full `resolved` data,
so discord.py's transformers and `interaction.namespace` run exactly as in production.

!!! warning "Sync is enforced"
    A command that exists in your tree but was never synced **cannot be invoked** — the
    test fails with a pointed message naming the command. This catches the classic
    forgot-to-`tree.sync()` bug. Opt out with
    [`simcord.run(bot, strict_sync=False)`](fixtures.md#configuration-options) for isolated
    unit tests, which auto-registers unsynced commands instead.

## Inspecting the result

Interaction verbs return an [`InteractionResult`](../api.md#simcord.InteractionResult) describing
everything the bot did in response:

```python
result.acknowledged          # did the bot respond at all?
result.deferred              # responded with "thinking…" (deferred)
result.ephemeral             # the response is only visible to the invoker
result.response              # the response message (ResponseMessage | None)
result.response.content      # its text
result.response.embeds       # list[discord.Embed]
result.response.message      # the full discord.Message, when you need it
result.followups             # messages sent via interaction.followup
result.modal                 # the modal payload, if one was shown
result.autocomplete_choices  # choices, for autocomplete interactions
```

A typical assertion:

```python
result = await mod.slash(channel, "ban", user=target, reason="spam")
assert result.acknowledged
assert result.ephemeral
assert result.response.content == f"Banned {target.mention}: spam"
```

## The interaction lifecycle is real

The full lifecycle behaves as on Discord:

- **Deferring** — `await interaction.response.defer()` sets `result.deferred`; a later
  `edit_original_response` / `followup.send` materialises the message.
- **Double-acknowledging** — responding twice raises the real Discord error `40060`. (A
  callback that *fails* — e.g. a 400 from an oversized embed — does **not** consume the
  interaction, so a retried response still gets through.)
- **Followups** — messages sent via `interaction.followup` land in `result.followups`.
- **`@original` operations** — editing, fetching and deleting the original response resolve
  to the right message, including for deferred component interactions.

```python
async def test_defer_then_followup(simcord_env):
    ...
    result = await alice.slash(channel, "slow-task")
    assert result.deferred
    assert result.followups[0].content == "Done!"
```

## Autocomplete

Type into an autocomplete option and get back the choices the bot offered:

```python
choices = await alice.autocomplete(channel, "tag", "name", "py")
assert [c["value"] for c in choices] == ["python", "pytest"]
```

The signature is `autocomplete(channel, command, option, value, **other_filled_options)` —
the last argument is what the user has typed so far into the focused `option`.

## Ephemeral visibility

Ephemeral responses are visible only to the invoker, and SimCord models that precisely.
Pass `viewer=` to `channel.history()` to see what a given user would see:

```python
assert len(channel.history(viewer=mod)) == len(channel.history(viewer=bystander)) + 1
```

A user also can't interact with *someone else's* ephemeral message — attempting it raises a
`SetupError`, just as it's impossible in the client. See
[Components & modals](components.md).

## Time control

App-command cooldowns and `View` timeouts are time-based. Fast-forward the virtual clock to
fire them instantly — no real waiting:

```python
result = await alice.slash(channel, "offer")   # sends a View(timeout=180)
await simcord_env.advance_time(180)            # instant — the view times out
assert "expired" in channel.last_message.content
```

See the dedicated [Time control](time-control.md) guide.

## Next

- [Components & modals](components.md) — clicking buttons, choosing in selects, submitting
  modals.
- [Permissions](permissions.md) — how command checks and server-side permissions interact.
- [Recipes](../cookbook.md) — confirm-button flows, paginators, and more.
