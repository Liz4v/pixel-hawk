"""Admin and guild access service layer for pixel-hawk.

Discord-agnostic functions for admin grants, guild role configuration,
and guild access checks.
"""

from loguru import logger

from ..models.entities import BotAccess, GuildConfig, Person


class ErrorMsg(Exception):
    """An error whose message is intended to be displayed to the user."""


async def imprint(discord_id: int, display_name: str) -> str:
    """Bootstrap the first admin on a fresh database.

    Only works when no Person records exist. Creates the caller as an admin.
    """
    count = await Person.count()
    if count > 0:
        raise ErrorMsg(":wing: I won't fall for _that_ one again...")
    await grant_admin(discord_id, display_name)
    return ":hatching_chick: Hello, parent!"


async def coadmin(admin_discord_id: int, target_discord_id: int, target_display_name: str) -> str:
    """Grant admin access to another user. Caller must be an admin."""
    if admin_discord_id == target_discord_id:
        return await imprint(target_discord_id, target_display_name)
    persons = await Person.filter(discord_id=admin_discord_id)
    person = persons[0] if persons else None
    if person is None or not (person.access & BotAccess.ADMIN):
        raise ErrorMsg("Admin access required.")
    return await grant_admin(target_discord_id, target_display_name)


async def grant_admin(discord_id: int, display_name: str) -> str:
    """Grant admin access to a Discord user. Creates a Person record if needed.

    Callers are responsible for authorization (no token flow — intended for
    manual use or a future installation flow).
    """
    persons = await Person.filter(discord_id=discord_id)
    person = persons[0] if persons else None
    if person is None:
        person = await Person.create(name=display_name, discord_id=discord_id)
        logger.info(f"Created new person '{display_name}' (discord_id={discord_id})")

    person.access = person.access | BotAccess.ADMIN
    await person.save()

    logger.info(f"Admin access granted to '{person.name}' (discord_id={discord_id})")
    return f"Admin access granted to {person.name}."


async def set_guild_role(discord_id: int, guild_id: int, role_id: str) -> str:
    """Set the required role for a guild. Caller must be an admin."""
    persons = await Person.filter(discord_id=discord_id)
    person = persons[0] if persons else None
    if person is None or not (person.access & BotAccess.ADMIN):
        raise ErrorMsg("Admin access required.")

    await GuildConfig.update_or_create(guild_id=guild_id, defaults={"required_role": role_id})
    logger.info(f"{person.name}: Set required role for guild {guild_id} to {role_id}")
    return f"Required role set to <@&{role_id}> for this server."


async def get_user_quotas(discord_id: int) -> str:
    """Format current quota usage for a Discord user. Raises ErrorMsg if not found."""
    persons = await Person.filter(discord_id=discord_id)
    person = persons[0] if persons else None
    if person is None:
        raise ErrorMsg("User not found.")
    return (
        f"**{person.name}** quotas:\n"
        f"  Active projects: {person.active_projects_count} / {person.max_active_projects}\n"
        f"  Watched tiles: {person.watched_tiles_count} / {person.max_watched_tiles}"
    )


async def set_user_quotas(
    admin_discord_id: int, target_discord_id: int, *, guild_id: int, projects: int | None, tiles: int | None
) -> str:
    """Set quota limits for a user. Caller must be admin.

    Enforces guild ceilings: requested values cannot exceed the guild's maximums.
    When both projects and tiles are None, returns current quotas (view mode).
    """
    if projects is None and tiles is None:
        return await get_user_quotas(target_discord_id)

    admins = await Person.filter(discord_id=admin_discord_id)
    admin = admins[0] if admins else None
    if admin is None or not (admin.access & BotAccess.ADMIN):
        raise ErrorMsg("Admin access required.")

    persons = await Person.filter(discord_id=target_discord_id)
    person = persons[0] if persons else None
    if person is None:
        raise ErrorMsg("User not found.")

    guild = await GuildConfig.get_by_guild(guild_id)
    changes: list[str] = []

    if projects is not None:
        if guild and projects > guild.max_active_projects:
            raise ErrorMsg(f"Exceeds guild ceiling of {guild.max_active_projects} active projects.")
        person.max_active_projects = projects
        changes.append(f"Active projects limit: {projects}")

    if tiles is not None:
        if guild and tiles > guild.max_watched_tiles:
            raise ErrorMsg(f"Exceeds guild ceiling of {guild.max_watched_tiles} watched tiles.")
        person.max_watched_tiles = tiles
        changes.append(f"Watched tiles limit: {tiles}")

    await person.save()
    logger.info(f"{admin.name}: Set quotas for {person.name}: {', '.join(changes)}")
    return f"Updated quotas for **{person.name}**:\n" + "\n".join(f"  {c}" for c in changes)


async def get_guild_quotas(guild_id: int) -> str:
    """Format current guild quota ceilings. Raises ErrorMsg if not found."""
    guild = await GuildConfig.get_by_guild(guild_id)
    if guild is None:
        raise ErrorMsg("This server has not been configured.")
    return (
        f"Guild quota ceilings:\n"
        f"  Max active projects: {guild.max_active_projects}\n"
        f"  Max watched tiles: {guild.max_watched_tiles}"
    )


async def set_guild_quotas(admin_discord_id: int, guild_id: int, *, projects: int | None, tiles: int | None) -> str:
    """Set guild-level quota ceilings. Caller must be admin.

    When both projects and tiles are None, returns current ceilings (view mode).
    """
    if projects is None and tiles is None:
        return await get_guild_quotas(guild_id)

    admins = await Person.filter(discord_id=admin_discord_id)
    admin = admins[0] if admins else None
    if admin is None or not (admin.access & BotAccess.ADMIN):
        raise ErrorMsg("Admin access required.")

    guild = await GuildConfig.get_by_guild(guild_id)
    if guild is None:
        raise ErrorMsg("This server has not been configured. Set a role first.")

    changes: list[str] = []
    if projects is not None:
        guild.max_active_projects = projects
        changes.append(f"Max active projects: {projects}")
    if tiles is not None:
        guild.max_watched_tiles = tiles
        changes.append(f"Max watched tiles: {tiles}")

    await guild.save()
    logger.info(f"{admin.name}: Set guild {guild_id} quotas: {', '.join(changes)}")
    return "Updated guild quota ceilings:\n" + "\n".join(f"  {c}" for c in changes)


async def check_dm_access(discord_id: int) -> Person:
    """Check if a user has access via DMs. Returns the Person if they have ADMIN or ALLOWED access.

    Raises ErrorMsg if the user has no Person record or insufficient access.
    """
    persons = await Person.filter(discord_id=discord_id)
    person = persons[0] if persons else None
    if person is None or not (person.access & (BotAccess.ADMIN | BotAccess.ALLOWED)):
        raise ErrorMsg("Use a hawk command in a server first to get access.")
    return person


async def check_guild_access(guild_id: int, discord_id: int, display_name: str, role_ids: list[str]) -> Person:
    """Check if a user has access in the given guild. Returns the Person (auto-created if needed).

    Raises ErrorMsg if access is denied.
    """
    persons = await Person.filter(discord_id=discord_id)
    person = persons[0] if persons else None
    if person and person.access & BotAccess.ADMIN:
        return person

    config = await GuildConfig.get_by_guild(guild_id)
    if config is None:
        raise ErrorMsg("This server has not been configured. An admin must set a role first.")

    if config.required_role not in role_ids:
        raise ErrorMsg(f"You need the <@&{config.required_role}> role to use this bot.")

    if person is None:
        person = await Person.create(
            name=display_name,
            discord_id=discord_id,
            access=int(BotAccess.ALLOWED),
            max_active_projects=config.max_active_projects,
            max_watched_tiles=config.max_watched_tiles,
        )
        logger.info(f"Auto-created person '{display_name}' (discord_id={discord_id}) via guild role")

    return person
