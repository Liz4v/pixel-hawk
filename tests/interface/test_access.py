"""Tests for admin and guild access service layer (access.py)."""

import pytest

from pixel_hawk.interface.access import ErrorMsg, check_guild_access, grant_admin, set_guild_role
from pixel_hawk.models.entities import BotAccess, GuildConfig, Person

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

        person = await Person.filter(discord_id=99999).first()
        assert person is not None
        assert person.name == "NewUser"
        assert person.access & BotAccess.ADMIN

    async def test_reuses_existing_person(self):
        await Person.create(name="Existing", discord_id=88888)
        result = await grant_admin(88888, "Existing")
        assert result is not None

        # Should not create a new person
        count = await Person.filter(discord_id=88888).count()
        assert count == 1

        # Should have admin access
        updated = await Person.get(discord_id=88888)
        assert updated.access & BotAccess.ADMIN

    async def test_idempotent_admin_grant(self):
        await grant_admin(77777, "Idempotent")
        await grant_admin(77777, "Idempotent")

        person = await Person.get(discord_id=77777)
        assert person.access & BotAccess.ADMIN

    async def test_preserves_existing_access_flags(self):
        await Person.create(name="Flagged", discord_id=66666, access=0x1)
        await grant_admin(66666, "Flagged")

        updated = await Person.get(discord_id=66666)
        assert updated.access & BotAccess.ADMIN
        assert updated.access & 0x1  # Original flag preserved


# Person discord fields tests


class TestPersonDiscordFields:
    async def test_discord_id_nullable(self):
        person = await Person.create(name="NoDiscord")
        assert person.discord_id is None

    async def test_discord_id_set(self):
        person = await Person.create(name="WithDiscord", discord_id=123456789)
        reloaded = await Person.get(id=person.id)
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
        reloaded = await Person.get(id=person.id)
        assert reloaded.access & BotAccess.ADMIN


# GuildConfig model tests


class TestGuildConfig:
    async def test_create_and_retrieve(self):
        await GuildConfig.create(guild_id=100001, required_role="artists")
        config = await GuildConfig.filter(guild_id=100001).first()
        assert config is not None
        assert config.required_role == "artists"

    async def test_update_existing(self):
        await GuildConfig.create(guild_id=100002, required_role="old")
        await GuildConfig.update_or_create(defaults={"required_role": "new"}, guild_id=100002)
        config = await GuildConfig.get(guild_id=100002)
        assert config.required_role == "new"

    async def test_different_guilds_independent(self):
        await GuildConfig.create(guild_id=100003, required_role="role_a")
        await GuildConfig.create(guild_id=100004, required_role="role_b")
        a = await GuildConfig.get(guild_id=100003)
        b = await GuildConfig.get(guild_id=100004)
        assert a.required_role == "role_a"
        assert b.required_role == "role_b"


# set_guild_role tests


class TestSetGuildRole:
    async def test_no_person_raises(self):
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_guild_role(99999, 200001, "artists")

    async def test_non_admin_raises(self):
        await Person.create(name="User", discord_id=40001, access=0)
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_guild_role(40001, 200001, "artists")

    async def test_allowed_only_raises(self):
        await Person.create(name="Allowed", discord_id=40002, access=int(BotAccess.ALLOWED))
        with pytest.raises(ErrorMsg, match="Admin access required"):
            await set_guild_role(40002, 200001, "artists")

    async def test_admin_sets_role(self):
        await Person.create(name="Admin", discord_id=40003, access=int(BotAccess.ADMIN))
        result = await set_guild_role(40003, 200002, "painters")
        assert "painters" in result
        config = await GuildConfig.get(guild_id=200002)
        assert config.required_role == "painters"

    async def test_admin_updates_existing_role(self):
        await Person.create(name="Admin", discord_id=40004, access=int(BotAccess.ADMIN))
        await set_guild_role(40004, 200003, "old_role")
        result = await set_guild_role(40004, 200003, "new_role")
        assert "new_role" in result
        config = await GuildConfig.get(guild_id=200003)
        assert config.required_role == "new_role"


# check_guild_access tests


class TestCheckGuildAccess:
    async def test_no_config_denies(self):
        with pytest.raises(ErrorMsg, match="not been configured"):
            await check_guild_access(300001, 50001, "User", ["artists"])

    async def test_has_role_auto_creates_person(self):
        await GuildConfig.create(guild_id=300002, required_role="artists")
        person = await check_guild_access(300002, 50002, "NewUser", ["artists", "everyone"])
        assert person.discord_id == 50002
        assert person.name == "NewUser"
        assert person.access & BotAccess.ALLOWED

    async def test_auto_created_gets_allowed_not_admin(self):
        await GuildConfig.create(guild_id=300003, required_role="artists")
        person = await check_guild_access(300003, 50003, "User", ["artists"])
        assert person.access & BotAccess.ALLOWED
        assert not (person.access & BotAccess.ADMIN)

    async def test_has_role_existing_person(self):
        await GuildConfig.create(guild_id=300004, required_role="artists")
        existing = await Person.create(name="Existing", discord_id=50004, access=int(BotAccess.ALLOWED))
        person = await check_guild_access(300004, 50004, "Existing", ["artists"])
        assert person.id == existing.id

    async def test_missing_role_denies(self):
        await GuildConfig.create(guild_id=300005, required_role="artists")
        with pytest.raises(ErrorMsg, match="artists"):
            await check_guild_access(300005, 50005, "User", ["everyone", "bots"])

    async def test_missing_role_denies_existing_person(self):
        await GuildConfig.create(guild_id=300006, required_role="artists")
        await Person.create(name="Existing", discord_id=50006, access=int(BotAccess.ALLOWED))
        with pytest.raises(ErrorMsg, match="artists"):
            await check_guild_access(300006, 50006, "Existing", ["everyone"])

    async def test_admin_bypasses_no_config(self):
        await Person.create(name="Admin", discord_id=50007, access=int(BotAccess.ADMIN))
        person = await check_guild_access(399999, 50007, "Admin", [])
        assert person.access & BotAccess.ADMIN

    async def test_admin_bypasses_missing_role(self):
        await GuildConfig.create(guild_id=300008, required_role="artists")
        await Person.create(name="Admin", discord_id=50008, access=int(BotAccess.ADMIN))
        person = await check_guild_access(300008, 50008, "Admin", ["everyone"])
        assert person.access & BotAccess.ADMIN
