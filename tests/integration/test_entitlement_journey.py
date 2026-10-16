import asyncio
from datetime import timedelta

import discord
import pytest
from discord.ext import commands

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
            reconciled.append({item.id async for item in self.entitlements(limit=None)})

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


async def test_sdk_sandbox_creation_survives_restart_and_is_not_test_mode_purchase(env):
    sku = env.create_sku("Premium")
    catalog = await env.bot.fetch_skus()
    assert {item.type for item in catalog} == {
        discord.SKUType.subscription,
        discord.SKUType.subscription_group,
    }
    result = await env.bot.create_entitlement(sku, env.guild, discord.EntitlementOwnerType.guild)
    assert result is None
    await env.settle()
    created = [item async for item in env.bot.entitlements(guild=env.guild)]
    assert len(created) == 1
    entitlement_id = created[0].id
    assert created[0].starts_at is None and created[0].ends_at is None
    await env.restart_bot(commands.Bot(command_prefix="!", intents=discord.Intents.default()))
    recovered = env.get_entitlement(entitlement_id)
    await recovered.snapshot().delete()
    await env.settle()
    assert recovered.snapshot().deleted
    ordinary = env.seed_entitlement(sku, guild=env.guild, type=discord.EntitlementType.test_mode_purchase)
    with pytest.raises(discord.HTTPException) as caught:
        await ordinary.snapshot().delete()
    assert caught.value.code == 40019


async def test_sdk_pagination_and_exclude_deleted_limitation_are_not_hidden(env):
    sku = env.create_sku("Premium")
    expected = [env.seed_entitlement(sku, guild=env.guild).id for _ in range(205)]
    tombstone = env.seed_entitlement(sku, guild=env.guild, deleted=True)
    actual = [item.id async for item in env.bot.entitlements(limit=None, guild=env.guild)]
    assert actual == expected
    # discord.py 2.7.1's default/after strategy omits exclude_deleted.
    default = [item.id async for item in env.bot.entitlements(limit=None, exclude_deleted=False)]
    assert default == expected
    before = discord.Object(id=tombstone.id + 1)
    included = [
        item.id async for item in env.bot.entitlements(limit=None, before=before, exclude_deleted=False)
    ]
    assert set(included) == {*expected, tombstone.id}
