"""Admin and guild access service layer for pixel-hawk.

Discord-agnostic functions for admin grants, guild role configuration,
and guild access checks.
"""

from loguru import logger

from ..models.config import get_config
from ..models.entities import BotAccess, GuildConfig, Person


class ErrorMsg(Exception):
    """An error whose message is intended to be displayed to the user."""


_command_prefix: str | None = None


def get_command_prefix() -> str:
    global _command_prefix
    if _command_prefix is None:
        _command_prefix = get_config().discord.command_prefix
    return _command_prefix


async def grant_admin(discord_id: int, display_name: str) -> str:
    """Grant admin access to a Discord user. Creates a Person record if needed.

    Callers are responsible for authorization (no token flow — intended for
    manual use or a future installation flow).
    """
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        person = await Person.create(name=display_name, discord_id=discord_id)
        logger.info(f"Created new person '{display_name}' (discord_id={discord_id})")

    person.access = person.access | BotAccess.ADMIN
    await person.save()

    logger.info(f"Admin access granted to '{person.name}' (discord_id={discord_id})")
    return f"Admin access granted to {person.name}."


async def set_guild_role(discord_id: int, guild_id: int, role_name: str) -> str:
    """Set the required role for a guild. Caller must be an admin."""
    person = await Person.filter(discord_id=discord_id).first()
    if person is None or not (person.access & BotAccess.ADMIN):
        raise ErrorMsg("Admin access required.")

    await GuildConfig.update_or_create(defaults={"required_role": role_name}, guild_id=guild_id)
    logger.info(f"{person.name}: Set required role for guild {guild_id} to '{role_name}'")
    return f"Required role set to **{role_name}** for this server."


async def check_guild_access(guild_id: int, discord_id: int, display_name: str, role_names: list[str]) -> Person:
    """Check if a user has access in the given guild. Returns the Person (auto-created if needed).

    Raises ErrorMsg if access is denied.
    """
    person = await Person.filter(discord_id=discord_id).first()
    if person and person.access & BotAccess.ADMIN:
        return person

    config = await GuildConfig.filter(guild_id=guild_id).first()
    if config is None:
        raise ErrorMsg("This server has not been configured. An admin must set a role first.")

    if config.required_role not in role_names:
        raise ErrorMsg(f"You need the **{config.required_role}** role to use this bot.")

    if person is None:
        person = await Person.create(name=display_name, discord_id=discord_id, access=int(BotAccess.ALLOWED))
        logger.info(f"Auto-created person '{display_name}' (discord_id={discord_id}) via guild role")

    return person
