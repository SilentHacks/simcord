# Slash commands & interactions

## Invoking commands

```python
result = await alice.slash(channel, "ban", user=target, reason="spam")
result = await alice.slash(channel, "config set", key="lang", value="en")   # subcommands
result = await alice.context_menu(channel, "Report", target)               # context menus
```

Options are validated against the command's declared options (names, types, choices,
required), and snowflake options carry full `resolved` data — so discord.py's
transformers and namespaces run for real.

!!! warning "Sync is enforced"
    A command that exists in your tree but was never synced cannot be invoked — the test
    fails with a pointed message. This catches the classic forgot-to-`tree.sync()` bug.
    Opt out with `dpt.run(bot, strict_sync=False)` for isolated unit tests.

## Inspecting the result

```python
result.acknowledged          # did the bot respond at all?
result.deferred              # responded with "thinking..."
result.ephemeral             # only the invoker can see it
result.response.content      # the response message
result.followups             # messages sent via interaction.followup
result.modal                 # the modal payload, if one was shown
```

Double-acknowledging an interaction raises the real Discord error (`40060`), and editing
a deferred response materialises the message — the full interaction lifecycle behaves as
in production.

## Components

```python
result = await alice.slash(channel, "delete-data")
confirm = await alice.click(result.response.message, label="Confirm")
picked  = await alice.select(message, ["green"], custom_id="color")
```

Clicks go through discord.py's real `View` dispatch. The framework only lets users do
what they physically could: clicking a missing or disabled button, selecting a
nonexistent option, or interacting with someone else's ephemeral message fails the test.

## Modals

```python
shown = await alice.slash(channel, "feedback")
submitted = await alice.submit_modal(shown, {"name": "Alice"})
assert submitted.response.content == "Thanks Alice"
```

## Autocomplete

```python
choices = await alice.autocomplete(channel, "tag", "name", "py")
assert [c["value"] for c in choices] == ["python", "pytest"]
```

## Ephemeral visibility

```python
assert len(channel.history(viewer=mod)) == len(channel.history(viewer=bystander)) + 1
```
