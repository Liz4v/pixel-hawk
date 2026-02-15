#!/usr/bin/env python3
"""Helper script to add new projects to pixel-hawk.

This script guides you through creating a Person (if needed) and a ProjectInfo record,
then tells you where to place your PNG file.

Usage:
    uv run python scripts/add_project.py
"""

import asyncio
from pathlib import Path

from pixel_hawk.config import get_config
from pixel_hawk.db import database
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import Person, ProjectInfo, ProjectState


async def list_persons() -> list[Person]:
    """List all persons in the database."""
    persons = await Person.all()
    if not persons:
        print("No persons found in database.")
    else:
        print("\nExisting persons:")
        for person in persons:
            print(f"  {person.id}: {person.name} (watching {person.watched_tiles_count} tiles)")
    return persons


async def create_person(name: str) -> Person:
    """Create a new person."""
    person = await Person.create(name=name)
    print(f"✓ Created person: {person.name} (ID: {person.id})")
    return person


async def get_or_create_person() -> Person:
    """Prompt user to select existing person or create new one."""
    persons = await list_persons()

    if persons:
        print("\nOptions:")
        print("  1. Use existing person")
        print("  2. Create new person")
        choice = input("\nChoice (1 or 2): ").strip()

        if choice == "1":
            person_id = int(input("Enter person ID: ").strip())
            person = await Person.get(id=person_id)
            print(f"✓ Selected: {person.name} (ID: {person.id})")
            return person

    # Create new person
    name = input("\nEnter person name: ").strip()
    return await create_person(name)


def get_project_bounds() -> Rectangle:
    """Prompt user for project coordinates and size."""
    print("\nProject bounds (canvas coordinates):")
    print("Example: Top-left (500, 500), size 100x100 pixels")

    x = int(input("Top-left X: ").strip())
    y = int(input("Top-left Y: ").strip())
    width = int(input("Width: ").strip())
    height = int(input("Height: ").strip())

    return Rectangle.from_point_size(Point(x, y), Size(width, height))


def get_project_state() -> ProjectState:
    """Prompt user for project state."""
    print("\nProject state:")
    print("  1. ACTIVE (monitored for changes)")
    print("  2. PASSIVE (loaded but not monitored)")
    print("  3. INACTIVE (not loaded)")

    choice = input("\nChoice (1/2/3, default 1): ").strip() or "1"

    if choice == "2":
        return ProjectState.PASSIVE
    elif choice == "3":
        return ProjectState.INACTIVE
    else:
        return ProjectState.ACTIVE


async def create_project(person: Person) -> ProjectInfo:
    """Create a new project for the given person."""
    name = input("\nProject name (human-readable, stored in DB): ").strip()
    rect = get_project_bounds()
    state = get_project_state()

    # Create ProjectInfo
    info = await ProjectInfo.from_rect(rect, person.id, name, state)
    print(f"\n✓ Created project: {info.name}")
    print(f"  State: {info.state}")
    print(f"  Bounds: ({info.x}, {info.y}) {info.width}x{info.height}")
    print(f"  Filename: {info.filename}")

    return info


async def main():
    """Main entry point."""
    print("=" * 60)
    print("pixel-hawk: Add Project Helper")
    print("=" * 60)

    async with database():
        # Get or create person
        person = await get_or_create_person()

        # Create project
        info = await create_project(person)

        # Show where to place the file
        config = get_config()
        person_dir = config.projects_dir / str(person.id)
        file_path = person_dir / info.filename

        print("\n" + "=" * 60)
        print("Next steps:")
        print("=" * 60)
        print(f"1. Create your project image using the WPlace palette")
        print(f"   (First color = transparent)")
        print(f"\n2. Create directory (if needed):")
        print(f"   {person_dir}")
        print(f"\n3. Save your PNG file to:")
        print(f"   {file_path}")
        print(f"\n4. Restart pixel-hawk to load the project")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
