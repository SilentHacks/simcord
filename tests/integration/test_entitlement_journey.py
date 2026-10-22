import asyncio
from datetime import timedelta

import discord
import pytest
from discord.ext import commands
from discord.http import Route

import simcord


async def test_entitlement_access_reaches_every_interaction_family(env, channel, alice):
    observed = []

    async def access(interaction: discord.Interaction):
        observed.append((interaction.type, {item.id for item in interaction.entitlements}))

    env.bot.add_listener(access, "on_interaction")
    premium = env.create_sku("Premium")
    granted = await env.grant_entitlement(premium, guild=env.guild)
    personal = env.create_sku("Personal", flags=discord.SKUFlags(user_subscription=True))
    bob = env.guild.add_member(env.create_user("bob"))
    await env.grant_entitlement(personal, user=bob)

    await alice.context_menu(channel, "Report Member", bob)
    await alice.autocomplete(channel, "tag", "name", "py")
    prompt = await alice.slash(channel, "delete-data")
    await alice.click(prompt.response.message, label="Confirm")
    assign = await alice.slash(channel, "assign")
    await alice.select(assign.response.message, [alice], custom_id="who")
    form = await alice.slash(channel, "feedback")
    await alice.submit_modal(form, {"name": "Alice"})

    @env.bot.tree.context_menu(name="Inspect entitlement")
    async def inspect(interaction: discord.Interaction, message: discord.Message):
        await interaction.response.send_message("inspected")

    await env.bot.tree.sync()
    target = await bob.send(channel, "target author has personal premium")
    await alice.context_menu(channel, "Inspect entitlement", target)
    assert {kind for kind, _ids in observed} == {
        discord.InteractionType.application_command,
        discord.InteractionType.autocomplete,
        discord.InteractionType.component,
        discord.InteractionType.modal_submit,
    }
    assert all(ids == {granted.id} for _kind, ids in observed)


async def test_guild_purchase_does_not_follow_purchaser_to_another_guild_or_dm(env, channel, alice):
    seen = []

    @env.bot.tree.command(description="Check premium")
    async def access(interaction: discord.Interaction):
        seen.append({item.id for item in interaction.entitlements})
        await interaction.response.send_message("checked")

    await env.bot.tree.sync()
    sku = env.create_sku("Guild Premium")
    entitlement = await env.grant_entitlement(sku, guild=env.guild, user=alice)
    await alice.slash(channel, "access")
    other = env.create_guild("Other")
    elsewhere = other.add_member(alice.user)
    await elsewhere.slash(other.create_text_channel("elsewhere"), "access")
    await alice.user.slash("access")
    assert seen == [{entitlement.id}, set(), set()]


async def test_seed_reconcile_consume_replay_end_restart_and_leave():
    seen = []
    reconciled = []

    class Bot(commands.Bot):
        def __init__(self):
            super().__init__(command_prefix="!", intents=discord.Intents(guilds=True))

        async def setup_hook(self):
            reconciled.append(
                {item.id async for item in self.entitlements(limit=None, after=discord.Object(id=0))}
            )

        async def on_entitlement_create(self, entitlement):
            seen.append((entitlement.id, entitlement.consumed))

    env = simcord.Env(Bot())
    guild = env.create_guild()
    premium = env.create_sku("Premium")
    seeded = env.seed_entitlement(premium, guild=guild)
    await env.start()
    try:
        assert reconciled == [{seeded.id}]
        assert seen == []
        topup = env.create_sku("Top-up", type=discord.SKUType.consumable)
        bought = await env.grant_entitlement(topup, user=123456789012345678)
        snapshot = bought.snapshot()
        await snapshot.consume()
        await env.settle()
        assert not snapshot.consumed  # SDK instances are snapshots, not live records.
        assert bought.snapshot().consumed
        await bought.replay_create()
        assert seen == [(bought.id, False), (bought.id, False)]
        assert (await env.bot.fetch_entitlement(bought.id)).consumed
        await seeded.schedule_end(env.now() + timedelta(seconds=10))
        assert seeded.snapshot().ends_at is None
        await env.advance_time(10)
        assert seeded.snapshot().ends_at == env.now()
        with env.time_context():
            assert seeded.snapshot().is_expired()
        await env.restart_bot(Bot())
        assert reconciled[-1] == {seeded.id, bought.id}
        assert seen == [(bought.id, False), (bought.id, False)]
        await env.bot.get_guild(guild.id).leave()
        await env.settle()
        await env.restart_bot(Bot())
        assert env.bot.get_guild(guild.id) is None
        assert seeded.snapshot().guild is None
        assert (await env.bot.fetch_entitlement(seeded.id)).id == seeded.id
        with pytest.raises(simcord.SetupError, match=r"CREATE|create"):
            await seeded.replay_create()
    finally:
        await env.shutdown()


async def test_live_transition_rejects_before_mutation_and_raw_owners_do_not_need_cache():
    env = simcord.Env(commands.Bot(command_prefix="!", intents=discord.Intents.none()))
    sku = env.create_sku("Premium")
    seeded = env.seed_entitlement(sku, guild=123456789012345678)
    with pytest.raises(simcord.SetupError, match=r"running|started"):
        await seeded.revoke()
    assert not seeded.snapshot().deleted
    with pytest.raises(simcord.SetupError, match=r"running|started"):
        await env.grant_entitlement(sku, guild=123456789012345678)
    assert len(env.backend.entitlements) == 1


@pytest.mark.parametrize("shards", [[0, 1], [1]])
async def test_entitlement_gateway_uses_only_shard_zero_even_for_guild_on_shard_one(shards):
    bot = commands.AutoShardedBot(
        command_prefix="!", intents=discord.Intents.none(), shard_count=2, shard_ids=shards
    )
    received = []

    @bot.listen("on_entitlement_create")
    async def receive(entitlement):
        received.append(entitlement.id)

    env = simcord.Env(bot)
    guild = env.create_guild(shard_id=1)
    sku = env.create_sku("Premium")
    await env.start()
    try:
        granted = await env.grant_entitlement(sku, guild=guild)
        assert received == ([granted.id] if 0 in shards else [])
        assert (await bot.fetch_entitlement(granted.id)).guild_id == guild.id
    finally:
        await env.shutdown()


async def test_grant_timeout_exposes_committed_id_for_recovery():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    release = asyncio.Event()

    @bot.listen("on_entitlement_create")
    async def blocked(entitlement):
        await release.wait()

    async with simcord.run(bot, settle_timeout=0.02) as env:
        sku = env.create_sku("Premium")
        with pytest.raises(TimeoutError) as caught:
            await env.grant_entitlement(sku, guild=123456789012345678)
        committed = [item async for item in bot.entitlements()]
        assert len(committed) == 1
        assert str(committed[0].id) in "\n".join(caught.value.__notes__)
        recovered = env.get_entitlement(committed[0].id)
        release.set()
        await env.settle()
        assert not recovered.snapshot().deleted
        await recovered.revoke()
        assert recovered.snapshot().deleted


def test_fixture_handles_reject_cross_world_owners_and_skus_before_insertion():
    def make_env():
        return simcord.Env(commands.Bot(command_prefix="!", intents=discord.Intents.none()))

    first, second = make_env(), make_env()
    sku = first.create_sku("Premium")
    guild = second.create_guild()
    with pytest.raises(simcord.SetupError, match="Env"):
        first.seed_entitlement(sku, guild=guild)
    with pytest.raises(simcord.SetupError, match="Env"):
        second.seed_entitlement(sku, guild=guild)
    assert not first.backend.entitlements
    assert not second.backend.entitlements


async def test_sdk_sandbox_creation_survives_restart_and_retains_tombstone(env):
    events = []

    @env.bot.listen("on_entitlement_create")
    async def created(entitlement):
        events.append(("create", entitlement))

    @env.bot.listen("on_entitlement_delete")
    async def deleted(entitlement):
        events.append(("delete", entitlement))

    sku = env.create_sku("Premium")
    group = env.create_sku("Premium group", type=discord.SKUType.subscription_group)
    catalog = await env.bot.fetch_skus()
    assert [(item.id, item.type) for item in catalog] == [
        (sku.id, discord.SKUType.subscription),
        (group.id, discord.SKUType.subscription_group),
    ]
    assert all(item.flags.guild_subscription for item in catalog)
    result = await env.bot.create_entitlement(sku, env.guild, discord.EntitlementOwnerType.guild)
    assert result is None  # The real SDK discards the POST response.
    await env.settle()
    rows = [item async for item in env.bot.entitlements(guild=env.guild)]
    assert len(rows) == 1
    entitlement = rows[0]
    assert entitlement.type.value == 8
    assert entitlement.starts_at is None and entitlement.ends_at is None
    assert entitlement.user_id == env.bot.user.id
    assert entitlement.user_id != env.bot.application_id
    assert entitlement.guild_id == env.guild.id
    assert not entitlement.consumed
    raw = await env.bot.http.get_entitlement(env.bot.application_id, entitlement.id)
    assert raw["starts_at"] is None and raw["ends_at"] is None
    assert raw["consumed"] is False
    assert [(kind, item.id) for kind, item in events] == [("create", entitlement.id)]
    await env.restart_bot(commands.Bot(command_prefix="!", intents=discord.Intents.default()))
    env.bot.add_listener(deleted, "on_entitlement_delete")
    recovered = await env.bot.fetch_entitlement(entitlement.id)
    assert recovered.user_id == env.bot.user.id
    assert await recovered.delete() is None
    await env.settle()
    assert (await env.bot.fetch_entitlement(entitlement.id)).deleted
    with pytest.raises(discord.NotFound) as caught:
        await recovered.delete()
    assert (caught.value.status, caught.value.code, caught.value.text) == (404, 10029, "Unknown Entitlement")
    assert not entitlement.deleted
    assert [item async for item in env.bot.entitlements(guild=env.guild)] == []
    await env.settle()
    assert [(kind, item.id) for kind, item in events] == [
        ("create", entitlement.id),
        ("delete", entitlement.id),
    ]


async def test_sdk_malformed_owner_returns_structured_error_before_sku_lookup(env):
    events = []

    @env.bot.listen("on_entitlement_create")
    async def created(entitlement):
        events.append(entitlement.id)

    with pytest.raises(discord.HTTPException) as caught:
        await env.bot.http.request(
            Route(
                "POST",
                "/applications/{application_id}/entitlements",
                application_id=env.bot.application_id,
            ),
            json={"sku_id": "1", "owner_id": "not-a-snowflake", "owner_type": 1},
        )
    assert (caught.value.status, caught.value.code) == (400, 50035)
    assert caught.value.text == ('Invalid Form Body\nIn owner_id: Value "not-a-snowflake" is not snowflake.')
    await env.settle()
    assert events == []
    assert [item async for item in env.bot.entitlements()] == []


async def test_sdk_consumption_removes_access_but_retains_history(env, channel, alice):
    updates = []
    seen = []

    @env.bot.listen("on_entitlement_update")
    async def updated(entitlement):
        updates.append(entitlement)

    @env.bot.tree.command(description="Check consumable access")
    async def access(interaction: discord.Interaction):
        seen.append([item.id for item in interaction.entitlements])
        await interaction.response.send_message("checked")

    await env.bot.tree.sync()
    sku = env.create_sku("Top-up", type=discord.SKUType.consumable)
    granted = await env.grant_entitlement(sku, user=alice)
    original = await env.bot.fetch_entitlement(granted.id)
    await alice.slash(channel, "access")
    assert await original.consume() is None
    await env.settle()
    await alice.slash(channel, "access")
    assert seen == [[granted.id], []]
    assert [(item.id, item.consumed) for item in updates] == [(granted.id, True)]
    assert not original.consumed
    assert (await env.bot.fetch_entitlement(granted.id)).consumed
    assert [item.id async for item in env.bot.entitlements()] == [granted.id]
    with pytest.raises(discord.HTTPException) as caught:
        await original.consume()
    assert (caught.value.status, caught.value.code, caught.value.text) == (
        400,
        40022,
        "Entitlement already consumed",
    )
    await env.settle()
    assert len(updates) == 1


@pytest.mark.parametrize("kind", [discord.SKUType.consumable, discord.SKUType.durable])
async def test_sdk_test_mode_one_time_delete_and_consume_tombstone(env, kind):
    events = []

    @env.bot.listen("on_entitlement_delete")
    async def deleted(entitlement):
        events.append(("delete", entitlement.id, entitlement.deleted))

    @env.bot.listen("on_entitlement_update")
    async def updated(entitlement):
        events.append(("update", entitlement.id, entitlement.deleted))

    sku = env.create_sku("One-time purchase", type=kind)
    purchase = await env.grant_entitlement(
        sku, user=123456789012345678, type=discord.EntitlementType.test_mode_purchase
    )
    snapshot = purchase.snapshot()
    assert await snapshot.delete() is None
    await env.settle()
    assert events == [("delete", purchase.id, True)]
    tombstone = await env.bot.fetch_entitlement(purchase.id)
    assert tombstone.deleted and not tombstone.consumed
    for operation in (snapshot.delete, snapshot.consume):
        with pytest.raises(discord.NotFound) as caught:
            await operation()
        assert (caught.value.status, caught.value.code, caught.value.text) == (
            404,
            10029,
            "Unknown Entitlement",
        )
    await env.settle()
    assert events == [("delete", purchase.id, True)]
    assert not snapshot.deleted


async def test_sdk_pagination_enumerates_nondeleted_and_raw_history_in_order(env):
    sku = env.create_sku("Premium")
    rows = [env.seed_entitlement(sku, guild=env.guild, deleted=index % 3 == 0) for index in range(215)]
    expected = [row.id for index, row in enumerate(rows) if index % 3 != 0]
    actual = [
        item.id
        async for item in env.bot.entitlements(limit=None, guild=env.guild, after=discord.Object(id=0))
    ]
    assert actual == expected
    after = [
        item.id
        async for item in env.bot.entitlements(
            limit=None, guild=env.guild, after=discord.Object(id=rows[100].id)
        )
    ]
    assert after == [item_id for item_id in expected if item_id > rows[100].id]
    # Use the SDK's low-level HTTP method for history; descending pages advance
    # from their oldest row, not the SDK 2.7.1 iterator's newest-row cursor.
    history = []
    cursor = None
    page_sizes = []
    for _ in range(3):
        page = await env.bot.http.get_entitlements(
            env.bot.application_id, limit=100, before=cursor, exclude_deleted=False
        )
        page_ids = [int(item["id"]) for item in page]
        assert page_ids == sorted(page_ids, reverse=True)
        if cursor is not None:
            assert all(item_id < cursor for item_id in page_ids)
        history.extend(page_ids)
        page_sizes.append(len(page))
        if len(page) < 100:
            break
        cursor = page_ids[-1]
    assert page_sizes == [100, 100, 15]
    assert history == [row.id for row in reversed(rows)]


async def test_user_first_grants_project_guild_first_without_purchaser_leak(env, channel, alice):
    seen = []

    @env.bot.tree.command(description="Check mixed premium access")
    async def access(interaction: discord.Interaction):
        seen.append([item.id for item in interaction.entitlements])
        await interaction.response.send_message("checked")

    await env.bot.tree.sync()
    personal = env.create_sku("Personal", flags=discord.SKUFlags(user_subscription=True))
    user_grant = await env.grant_entitlement(personal, user=alice)
    guild_sku = env.create_sku("Guild")
    guild_grant = await env.grant_entitlement(guild_sku, guild=env.guild, user=alice)
    await alice.slash(channel, "access")
    other = env.create_guild("Other")
    elsewhere = other.add_member(alice.user)
    await elsewhere.slash(other.create_text_channel("elsewhere"), "access")
    await alice.user.slash("access")
    assert seen == [[guild_grant.id, user_grant.id], [user_grant.id], [user_grant.id]]


async def test_type_one_tier_replacement_retains_ended_history_and_snapshots(env, channel, alice):
    events = []
    seen = []

    @env.bot.listen("on_entitlement_create")
    async def created(entitlement):
        events.append(("create", entitlement))

    @env.bot.listen("on_entitlement_update")
    async def updated(entitlement):
        events.append(("update", entitlement))

    @env.bot.tree.command(description="Check current tier")
    async def access(interaction: discord.Interaction):
        seen.append([item.id for item in interaction.entitlements])
        await interaction.response.send_message("checked")

    await env.bot.tree.sync()
    tier_a = env.create_sku("Tier A")
    tier_b = env.create_sku("Tier B")
    old = await env.grant_entitlement(tier_a, guild=env.guild, type=discord.EntitlementType(1))
    original = await env.bot.fetch_entitlement(old.id)
    await alice.slash(channel, "access")
    # Fixture entitlement transitions model an observed upgrade, not a
    # subscription cancellation, downgrade, or billing API.
    await old.end()
    replacement = await env.grant_entitlement(tier_b, guild=env.guild, type=discord.EntitlementType(1))
    await alice.slash(channel, "access")
    assert seen == [[old.id], [replacement.id]]
    assert [(kind, item.id) for kind, item in events] == [
        ("create", old.id),
        ("update", old.id),
        ("create", replacement.id),
    ]
    assert events[0][1].ends_at is None
    assert events[1][1].ends_at == env.now()
    assert not events[1][1].deleted
    assert original.ends_at is None and not original.deleted
    history = [item async for item in env.bot.entitlements(guild=env.guild, after=discord.Object(id=0))]
    assert [item.id for item in history] == [old.id, replacement.id]
    assert history[0].ends_at == env.now() and not history[0].deleted
    assert history[1].ends_at is None
    raw_old = await env.bot.http.get_entitlement(env.bot.application_id, old.id)
    assert raw_old["fulfillment_status"] == 6
    assert raw_old["source_type"] == 9
    await env.restart_bot(commands.Bot(command_prefix="!", intents=discord.Intents.default()))
    restarted = [item async for item in env.bot.entitlements(guild=env.guild, after=discord.Object(id=0))]
    assert [(item.id, item.deleted, item.ends_at) for item in restarted] == [
        (old.id, False, history[0].ends_at),
        (replacement.id, False, None),
    ]
    assert original.ends_at is None and events[0][1].ends_at is None
    env.bot.tree.add_command(access)
    await env.bot.tree.sync()
    await alice.slash(channel, "access")
    assert seen == [[old.id], [replacement.id], [replacement.id]]


async def test_duplicate_gateway_events_reach_independent_consumers_without_resurrection(env):
    first = []
    second = []

    @env.bot.listen("on_entitlement_create")
    async def first_create(entitlement):
        first.append(("create", entitlement.id, entitlement.deleted))

    @env.bot.listen("on_entitlement_delete")
    async def first_delete(entitlement):
        first.append(("delete", entitlement.id, entitlement.deleted))

    @env.bot.listen("on_entitlement_create")
    async def second_create(entitlement):
        second.append(("create", entitlement.id, entitlement.deleted))

    @env.bot.listen("on_entitlement_delete")
    async def second_delete(entitlement):
        second.append(("delete", entitlement.id, entitlement.deleted))

    sku = env.create_sku("Premium")
    granted = await env.grant_entitlement(sku, guild=env.guild)
    original = await env.bot.http.get_entitlement(env.bot.application_id, granted.id)
    env.backend.emit("ENTITLEMENT_CREATE", original)
    await env.settle()
    assert not (await env.bot.fetch_entitlement(granted.id)).deleted
    await granted.revoke()
    tombstone = await env.bot.http.get_entitlement(env.bot.application_id, granted.id)
    env.backend.emit("ENTITLEMENT_DELETE", tombstone)
    await env.settle()
    env.backend.emit("ENTITLEMENT_CREATE", original)
    await env.settle()
    expected = [
        ("create", granted.id, False),
        ("create", granted.id, False),
        ("delete", granted.id, True),
        ("delete", granted.id, True),
        ("create", granted.id, False),
    ]
    assert first == expected
    assert second == expected
    assert (await env.bot.fetch_entitlement(granted.id)).deleted
    assert granted.snapshot().deleted
    assert [item async for item in env.bot.entitlements()] == []
