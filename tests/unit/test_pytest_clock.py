def test_pytest_clock_selection_uses_execution_context_and_aliases(pytester):
    pytester.makeconftest(
        """
        import discord
        import pytest
        import simcord
        from discord.ext import commands

        @pytest.fixture
        def simcord_bot():
            return commands.Bot(command_prefix='!', intents=discord.Intents.none())

        @pytest.fixture
        def world(simcord_env):
            return simcord_env

        @pytest.fixture
        def foreign():
            return simcord.Env(commands.Bot(command_prefix='!', intents=discord.Intents.none()))
        """
    )
    pytester.makepyfile(
        """
        import datetime
        import discord

        async def test_clock(world):
            assert discord.utils.utcnow() == world.now()
            start = world.now()
            await world.advance_time(10)
            assert discord.utils.utcnow() == start + datetime.timedelta(seconds=10)
            # Driver selection must not prohibit fixture operations.
            world.create_user('driver')

        async def test_next_fixture(world):
            assert discord.utils.utcnow() == world.now()

        async def test_inactive_foreign_fixture_keeps_running_clock(world, foreign):
            sku = world.create_sku('Premium')
            grant = world.seed_entitlement(
                sku, guild=123, ends_at=world.now() + datetime.timedelta(days=7)
            )
            assert not grant.snapshot().is_expired()
            assert discord.utils.utcnow() == world.now()
        """
    )
    result = pytester.runpytest("-o", "asyncio_mode=auto", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=3)
