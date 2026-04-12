"""Tests for admin and guild access service layer (access.py)."""

import pytest

from pixel_hawk.interface.access import (
    ErrorMsg,
    check_guild_access,
    coadmin,
    get_guild_quotas,
    get_user_quotas,
    grant_admin,
    set_guild_quotas,
    set_guild_role,
    set_user_quotas,
)
from pixel_hawk.models.guild import GuildConfig
from pixel_hawk.models.person import BotAccess, Person

# BotAccess enum tests


class TestBotAccess:
    def test_admin_value(self):
        assert BotAccess.ADMIN == 0x10000000

    def test_bitmask_set(self):
        access = 0 | BotAccess.ADMIN
        assert access & BotAccess.ADMIN

    def test_bitmask_unset(self):
        assert not (0 & BotAccess.ADMIN)

    def test_bitmask_preserves_other_flags(self):
        access = 0x1 | BotAccess.ADMIN
        assert access & BotAccess.ADMIN
        assert access & 0x1


# grant_admin tests


class TestGrantAdmin:
    async def test_creates_person(self):
        result = await grant_admin(99999, "NewUser")
        assert "NewUser" in result

        person = await Person.get_or_none_by_discord_id(99999)
        assert person is not None
        assert person.name == "NewUser"
        assert person.access & BotAccess.ADMIN

    async def test_reuses_existing_person(self):
        await Person.create(name="Existing", discord_id=88888)
        result = await grant_admin(88888, "Existing")
        assert result is not None

        # Should not create a new person
        count = await Person.count_by_discord_id(88888)
        assert count == 1

        # Should have admin access
        updated = await Person.get_by_discord_id(88888)
        assert updated.access & BotAccess.ADMIN

    async def test_idempotent_admin_grant(self):
        await grant_admin(77777, "Idempotent")
        await grant_admin(77777, "Idempotent")

        person = await Person.get_by_discord_id(77777)
        assert person.access & BotAccess.ADMIN

    async def test_preserves_existing_access_flags(self):
        await Person.create(name="Flagged", discord_id=66666, access=0x1)
        await grant_admin(66666, "Flagged")

        updated = await Person.get_by_discord_id(66666)
        assert updated.access & BotAccess.ADMIN
        assert updated.access & 0x1  # Original flag preserved


# coadmin tests


class TestCoadmin:
    async def test_no_person_raises(self):
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await coadmin(99999, 88888, "Target")

    async def test_non_admin_raises(self):
        await Person.create(name="User", discord_id=44001, access=0)
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await coadmin(44001, 44002, "Target")

    async def test_admin_grants_to_new_user(self):
        await Person.create(name="Admin", discord_id=44003, access=int(BotAccess.ADMIN))
        result = await coadmin(44003, 44004, "NewAdmin")
        assert "NewAdmin" in result

        target = await Person.get_or_none_by_discord_id(44004)
        assert target is not None
        assert target.access & BotAccess.ADMIN

    async def test_admin_grants_to_existing_user(self):
        await Person.create(name="Admin", discord_id=44005, access=int(BotAccess.ADMIN))
        await Person.create(name="Existing", discord_id=44006, access=int(BotAccess.ALLOWED))
        result = await coadmin(44005, 44006, "Existing")
        assert "Existing" in result

        target = await Person.get_by_discord_id(44006)
        assert target.access & BotAccess.ADMIN
        assert target.access & BotAccess.ALLOWED  # Preserved

    async def test_self_target_imprints_on_empty_db(self):
        result = await coadmin(44007, 44007, "SelfAdmin")
        assert "hatching_chick" in result

        person = await Person.get_or_none_by_discord_id(44007)
        assert person is not None
        assert person.access & BotAccess.ADMIN

    async def test_self_target_rejects_on_nonempty_db(self):
        await Person.create(name="Existing", discord_id=44008)
        with pytest.raises(ErrorMsg):
            await coadmin(44009, 44009, "LateArrival")


# Person discord fields tests


class TestPersonDiscordFields:
    async def test_discord_id_nullable(self):
        person = await Person.create(name="NoDiscord")
        assert person.discord_id is None

    async def test_discord_id_set(self):
        person = await Person.create(name="WithDiscord", discord_id=123456789)
        reloaded = await Person.get_by_id(person.id)
        assert reloaded.discord_id == 123456789

    async def test_discord_id_unique(self):
        await Person.create(name="First", discord_id=111111)
        with pytest.raises(Exception, match=r"(?i)unique|constraint"):
            await Person.create(name="Second", discord_id=111111)

    async def test_access_defaults_to_zero(self):
        person = await Person.create(name="Default")
        assert person.access == 0

    async def test_access_stores_bitmask(self):
        person = await Person.create(name="Admin", access=int(BotAccess.ADMIN))
        reloaded = await Person.get_by_id(person.id)
        assert reloaded.access & BotAccess.ADMIN


# GuildConfig model tests


class TestGuildConfig:
    async def test_create_and_retrieve(self):
        await GuildConfig.create(guild_id=100001, required_role="111")
        config = await GuildConfig.get_by_guild(100001)
        assert config is not None
        assert config.required_role == "111"

    async def test_update_existing(self):
        await GuildConfig.create(guild_id=100002, required_role="111")
        await GuildConfig.update_or_create(defaults={"required_role": "222"}, guild_id=100002)
        config = await GuildConfig.get_by_guild(100002)
        assert config.required_role == "222"

    async def test_different_guilds_independent(self):
        await GuildConfig.create(guild_id=100003, required_role="111")
        await GuildConfig.create(guild_id=100004, required_role="222")
        a = await GuildConfig.get_by_guild(100003)
        b = await GuildConfig.get_by_guild(100004)
        assert a.required_role == "111"
        assert b.required_role == "222"


# set_guild_role tests


class TestSetGuildRole:
    async def test_no_person_raises(self):
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_guild_role(99999, 200001, "111")

    async def test_non_admin_raises(self):
        await Person.create(name="User", discord_id=40001, access=0)
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_guild_role(40001, 200001, "111")

    async def test_allowed_only_raises(self):
        await Person.create(name="Allowed", discord_id=40002, access=int(BotAccess.ALLOWED))
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_guild_role(40002, 200001, "111")

    async def test_admin_sets_role(self):
        await Person.create(name="Admin", discord_id=40003, access=int(BotAccess.ADMIN))
        result = await set_guild_role(40003, 200002, "222")
        assert "222" in result
        config = await GuildConfig.get_by_guild(200002)
        assert config.required_role == "222"

    async def test_admin_updates_existing_role(self):
        await Person.create(name="Admin", discord_id=40004, access=int(BotAccess.ADMIN))
        await set_guild_role(40004, 200003, "111")
        result = await set_guild_role(40004, 200003, "222")
        assert "222" in result
        config = await GuildConfig.get_by_guild(200003)
        assert config.required_role == "222"


# check_guild_access tests


class TestCheckGuildAccess:
    async def test_no_config_denies(self):
        with pytest.raises(ErrorMsg, match="not been configured"):
            await check_guild_access(300001, 50001, "User", ["111"])

    async def test_has_role_auto_creates_person(self):
        await GuildConfig.create(guild_id=300002, required_role="111")
        person = await check_guild_access(300002, 50002, "NewUser", ["111", "222"])
        assert person.discord_id == 50002
        assert person.name == "NewUser"
        assert person.access & BotAccess.ALLOWED

    async def test_auto_created_gets_allowed_not_admin(self):
        await GuildConfig.create(guild_id=300003, required_role="111")
        person = await check_guild_access(300003, 50003, "User", ["111"])
        assert person.access & BotAccess.ALLOWED
        assert not (person.access & BotAccess.ADMIN)

    async def test_auto_created_inherits_guild_quotas(self):
        await GuildConfig.create(guild_id=300009, required_role="111", max_active_projects=25, max_watched_tiles=8)
        person = await check_guild_access(300009, 50009, "QuotaUser", ["111"])
        assert person.max_active_projects == 25
        assert person.max_watched_tiles == 8

    async def test_has_role_existing_person(self):
        await GuildConfig.create(guild_id=300004, required_role="111")
        existing = await Person.create(name="Existing", discord_id=50004, access=int(BotAccess.ALLOWED))
        person = await check_guild_access(300004, 50004, "Existing", ["111"])
        assert person.id == existing.id

    async def test_missing_role_denies(self):
        await GuildConfig.create(guild_id=300005, required_role="111")
        with pytest.raises(ErrorMsg, match="111"):
            await check_guild_access(300005, 50005, "User", ["222", "333"])

    async def test_missing_role_denies_existing_person(self):
        await GuildConfig.create(guild_id=300006, required_role="111")
        await Person.create(name="Existing", discord_id=50006, access=int(BotAccess.ALLOWED))
        with pytest.raises(ErrorMsg, match="111"):
            await check_guild_access(300006, 50006, "Existing", ["222"])

    async def test_admin_bypasses_no_config(self):
        await Person.create(name="Admin", discord_id=50007, access=int(BotAccess.ADMIN))
        person = await check_guild_access(399999, 50007, "Admin", [])
        assert person.access & BotAccess.ADMIN

    async def test_admin_bypasses_missing_role(self):
        await GuildConfig.create(guild_id=300008, required_role="111")
        await Person.create(name="Admin", discord_id=50008, access=int(BotAccess.ADMIN))
        person = await check_guild_access(300008, 50008, "Admin", ["222"])
        assert person.access & BotAccess.ADMIN


# get_user_quotas tests


class TestGetUserQuotas:
    async def test_unknown_user_raises(self):
        with pytest.raises(ErrorMsg, match="not found"):
            await get_user_quotas(99999)

    async def test_default_quotas(self):
        await Person.create(name="User", discord_id=60001)
        result = await get_user_quotas(60001)
        assert "50" in result
        assert "10" in result

    async def test_custom_quotas(self):
        await Person.create(name="User", discord_id=60002, max_active_projects=5, max_watched_tiles=100)
        result = await get_user_quotas(60002)
        assert "5" in result
        assert "100" in result


# set_user_quotas tests


class TestSetUserQuotas:
    async def test_non_admin_raises(self):
        await Person.create(name="User", discord_id=70001, access=0)
        await Person.create(name="Target", discord_id=70002)
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_user_quotas(70001, 70002, guild_id=500001, projects=5, tiles=100)

    async def test_target_not_found_raises(self):
        await Person.create(name="Admin", discord_id=70003, access=int(BotAccess.ADMIN))
        with pytest.raises(ErrorMsg, match="not found"):
            await set_user_quotas(70003, 99999, guild_id=500001, projects=5, tiles=None)

    async def test_admin_sets_quotas(self):
        await Person.create(name="Admin", discord_id=70004, access=int(BotAccess.ADMIN))
        await Person.create(name="Target", discord_id=70005)
        result = await set_user_quotas(70004, 70005, guild_id=500001, projects=10, tiles=200)
        assert "10" in result
        target = await Person.get_by_discord_id(70005)
        assert target.max_active_projects == 10
        assert target.max_watched_tiles == 200

    async def test_no_args_returns_view(self):
        await Person.create(name="Admin", discord_id=70008, access=int(BotAccess.ADMIN))
        await Person.create(name="Target", discord_id=70009)
        result = await set_user_quotas(70008, 70009, guild_id=500001, projects=None, tiles=None)
        assert "50" in result  # Default quota

    async def test_exceeds_guild_projects_ceiling(self):
        await Person.create(name="Admin", discord_id=70010, access=int(BotAccess.ADMIN))
        await Person.create(name="Target", discord_id=70011)
        await GuildConfig.create(guild_id=500002, required_role="111", max_active_projects=20)
        with pytest.raises(ErrorMsg, match="Exceeds guild ceiling"):
            await set_user_quotas(70010, 70011, guild_id=500002, projects=25, tiles=None)

    async def test_exceeds_guild_tiles_ceiling(self):
        await Person.create(name="Admin", discord_id=70012, access=int(BotAccess.ADMIN))
        await Person.create(name="Target", discord_id=70013)
        await GuildConfig.create(guild_id=500003, required_role="111", max_watched_tiles=5)
        with pytest.raises(ErrorMsg, match="Exceeds guild ceiling"):
            await set_user_quotas(70012, 70013, guild_id=500003, projects=None, tiles=15)

    async def test_within_guild_ceiling_succeeds(self):
        await Person.create(name="Admin", discord_id=70014, access=int(BotAccess.ADMIN))
        await Person.create(name="Target", discord_id=70015)
        await GuildConfig.create(guild_id=500004, required_role="111", max_active_projects=30)
        result = await set_user_quotas(70014, 70015, guild_id=500004, projects=25, tiles=None)
        assert "25" in result


# get_guild_quotas tests


class TestGetGuildQuotas:
    async def test_not_found_raises(self):
        with pytest.raises(ErrorMsg, match="not been configured"):
            await get_guild_quotas(999999)

    async def test_defaults_shown(self):
        await GuildConfig.create(guild_id=600001, required_role="111")
        result = await get_guild_quotas(600001)
        assert "50" in result
        assert "10" in result

    async def test_custom_shown(self):
        await GuildConfig.create(guild_id=600002, required_role="111", max_active_projects=100, max_watched_tiles=25)
        result = await get_guild_quotas(600002)
        assert "100" in result
        assert "25" in result


# set_guild_quotas tests


class TestSetGuildQuotas:
    async def test_non_admin_raises(self):
        await Person.create(name="User", discord_id=80001, access=0)
        await GuildConfig.create(guild_id=700001, required_role="111")
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_guild_quotas(80001, 700001, projects=100, tiles=50)

    async def test_no_guild_raises(self):
        await Person.create(name="Admin", discord_id=80002, access=int(BotAccess.ADMIN))
        with pytest.raises(ErrorMsg, match="not been configured"):
            await set_guild_quotas(80002, 999999, projects=100, tiles=50)

    async def test_admin_sets_quotas(self):
        await Person.create(name="Admin", discord_id=80003, access=int(BotAccess.ADMIN))
        await GuildConfig.create(guild_id=700002, required_role="111")
        result = await set_guild_quotas(80003, 700002, projects=100, tiles=50)
        assert "100" in result
        assert "50" in result
        guild = await GuildConfig.get_by_guild(700002)
        assert guild.max_active_projects == 100
        assert guild.max_watched_tiles == 50

    async def test_no_args_returns_view(self):
        await Person.create(name="Admin", discord_id=80004, access=int(BotAccess.ADMIN))
        await GuildConfig.create(guild_id=700003, required_role="111")
        result = await set_guild_quotas(80004, 700003, projects=None, tiles=None)
        assert "50" in result  # Default
