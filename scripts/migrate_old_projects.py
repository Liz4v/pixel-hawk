#!/usr/bin/env python3
"""Migrate old-format project files to new database-first multi-user structure.

This script:
1. Initializes the database if needed
2. Creates a Person record (default: "Kiva")
3. Parses old filenames: "{name} {tx} {ty} {px} {py}.png"
4. Creates ProjectInfo records in the database
5. Moves files to new structure: "projects/{person_id}/{tx}_{ty}_{px}_{py}.png"
"""

import asyncio
import re
import shutil
from pathlib import Path

from loguru import logger
from tortoise import Tortoise

from pixel_hawk.config import get_config
from pixel_hawk.db import TORTOISE_ORM
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import Person, ProjectInfo, ProjectState


# Regex to parse old filename format: "name tx ty px py.png"
OLD_FILENAME_PATTERN = re.compile(r"^(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\.png$")


async def migrate_projects(owner_name: str = "Kiva", dry_run: bool = False) -> None:
    """Migrate all projects from old format to new database structure.

    Args:
        owner_name: Name of the person who owns these projects (default: "Kiva")
        dry_run: If True, only show what would be done without making changes
    """
    # Initialize database
    await Tortoise.init(config=TORTOISE_ORM)
    await Tortoise.generate_schemas()

    config = get_config()
    old_projects_dir = config.home / "projects"

    if not old_projects_dir.exists():
        logger.error(f"Projects directory not found: {old_projects_dir}")
        await Tortoise.close_connections()
        return

    # Get or create the person
    person, created = await Person.get_or_create(name=owner_name)
    if created:
        logger.info(f"Created person: {person.name} (ID: {person.id})")
    else:
        logger.info(f"Using existing person: {person.name} (ID: {person.id})")

    # Find all old-format PNG files
    old_files = list(old_projects_dir.glob("*.png"))

    if not old_files:
        logger.warning("No PNG files found in projects directory")
        await Tortoise.close_connections()
        return

    logger.info(f"Found {len(old_files)} project files to migrate")

    # Create person directory
    person_dir = config.projects_dir / str(person.id)
    if not dry_run:
        person_dir.mkdir(parents=True, exist_ok=True)

    migrated = 0
    skipped = 0

    for old_path in old_files:
        # Parse filename
        match = OLD_FILENAME_PATTERN.match(old_path.name)
        if not match:
            logger.warning(f"Skipping file with unrecognized format: {old_path.name}")
            skipped += 1
            continue

        name, tx, ty, px, py = match.groups()
        tx, ty, px, py = int(tx), int(ty), int(px), int(py)

        # Calculate rectangle from tile coordinates
        point = Point.from4(tx, ty, px, py)

        # Load image to get size
        from PIL import Image
        try:
            with Image.open(old_path) as im:
                width, height = im.size
        except Exception as e:
            logger.error(f"Failed to open {old_path.name}: {e}")
            skipped += 1
            continue

        rect = Rectangle.from_point_size(point, Size(width, height))

        # Create or get ProjectInfo
        if dry_run:
            logger.info(f"[DRY RUN] Would create project: {name} ({tx}_{ty}_{px}_{py}.png)")
            logger.info(f"[DRY RUN]   Bounds: {rect}")
            migrated += 1
            continue

        # Check if project already exists
        existing = await ProjectInfo.filter(owner=person, name=name).first()
        if existing:
            logger.warning(f"Project '{name}' already exists for {person.name}, skipping")
            skipped += 1
            continue

        # Create project info
        info = await ProjectInfo.from_rect(
            rect=rect,
            owner_id=person.id,
            name=name,
            state=ProjectState.ACTIVE
        )

        # Move file to new location
        new_filename = info.filename  # {tx}_{ty}_{px}_{py}.png
        new_path = person_dir / new_filename

        try:
            shutil.move(str(old_path), str(new_path))
            logger.info(f"Migrated: {name} -> {new_filename}")
            migrated += 1
        except Exception as e:
            logger.error(f"Failed to move {old_path.name}: {e}")
            # Clean up database entry if file move failed
            await info.delete()
            skipped += 1

    # Update watched tiles count
    if not dry_run:
        await person.update_watched_tiles_count()
        logger.info(f"{person.name}: Watching {person.watched_tiles_count} tiles")

    logger.info(f"Migration complete: {migrated} migrated, {skipped} skipped")

    await Tortoise.close_connections()


if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    owner_name = "Kiva"

    # Check for custom owner name
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            owner_name = arg
            break

    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")

    asyncio.run(migrate_projects(owner_name, dry_run))
