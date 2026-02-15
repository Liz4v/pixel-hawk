# Migration Guide: Filesystem-based to Database-first

This guide helps you migrate from the old filesystem-based pixel-hawk to the new multi-user, database-first architecture.

## What Changed

### Before (Old Version)
- Drop PNG files into `projects/` directory
- Filenames: `projectname_<tx>_<ty>_<px>_<py>.png`
- pixel-hawk auto-discovers files on each polling cycle
- Metadata stored in YAML files (`.metadata.yaml`)
- Single-user only

### After (New Version)
- Create ProjectInfo records in SQLite database
- Filenames: `<tx>_<ty>_<px>_<py>.png` (coordinates only)
- Files organized by person ID: `projects/{person_id}/`
- pixel-hawk loads from database at startup
- Metadata stored in SQLite (`data/pixel-hawk.db`)
- Multi-user support with Person table

## Migration Steps

### Step 1: Back up your data

```powershell
# Back up your entire pixel-hawk-data directory
cp -r pixel-hawk-data pixel-hawk-data.backup
```

### Step 2: Initialize the new database

```powershell
# Initialize database schema
uv run aerich init-db

# This creates:
# - data/pixel-hawk.db (SQLite database)
# - Person table (empty)
# - ProjectInfo table (empty)
# - HistoryChange table (empty)
```

### Step 3: Create your first Person

Use the helper script or Python REPL:

```powershell
# Option 1: Helper script
uv run python scripts/add_project.py
# Follow prompts to create a person

# Option 2: Python REPL
uv run python
```

```python
from pixel_hawk.db import database
from pixel_hawk.models import Person

async def create_person():
    async with database():
        person = await Person.create(name="YourName")
        print(f"Created person ID: {person.id}")

import asyncio
asyncio.run(create_person())
```

### Step 4: Migrate your projects

For each old project file:

1. **Extract coordinates from filename**
   - Old: `myproject_0_0_500_500.png` → coordinates: `0_0_500_500`

2. **Create ProjectInfo record**

```python
from pixel_hawk.db import database
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import Person, ProjectInfo

async def migrate_project():
    async with database():
        # Get your person (created in Step 3)
        person = await Person.get(id=1)  # Use your person ID

        # Create ProjectInfo with coordinates from old filename
        rect = Rectangle.from_point_size(
            Point(500, 500),      # x, y from old filename
            Size(100, 100)        # width, height of your project
        )

        info = await ProjectInfo.from_rect(
            rect=rect,
            owner_id=person.id,
            name="MyProject"      # Use the prefix from old filename
        )

        print(f"New filename: {info.filename}")
        print(f"Create directory: projects/{person.id}/")
        print(f"Move file to: projects/{person.id}/{info.filename}")

import asyncio
asyncio.run(migrate_project())
```

3. **Reorganize files**

```powershell
# Create person directory
mkdir projects/1

# Rename and move file
# Old: projects/myproject_0_0_500_500.png
# New: projects/1/0_0_500_500.png
mv projects/myproject_0_0_500_500.png projects/1/0_0_500_500.png
```

### Step 5: Migrate YAML metadata (automatic)

YAML metadata files (`.metadata.yaml`) are automatically migrated to SQLite when:
- A ProjectInfo record exists for the project name
- The YAML file is present in `metadata/` directory
- pixel-hawk loads the project for the first time

The YAML file is renamed to `.yaml.migrated` after successful migration.

**Note:** You must create the ProjectInfo record FIRST (Step 4) before the YAML migration can occur.

### Step 6: Clean up old files (optional)

After verifying everything works:

```powershell
# Remove old project files (if you moved them all)
rm projects/*.png

# Remove migrated YAML files
rm metadata/*.yaml.migrated
```

## Using the Helper Script

The easiest way to migrate is using the helper script:

```powershell
uv run python scripts/add_project.py
```

For each old project:
1. Enter the person name (or select existing)
2. Enter the project name (from old filename prefix)
3. Enter coordinates and size
4. Note the new filename and location
5. Move your PNG file to the new location

## Troubleshooting

### "No persons found in database"

You haven't created a Person yet. Run:

```python
from pixel_hawk.db import database
from pixel_hawk.models import Person

async def create():
    async with database():
        await Person.create(name="YourName")

import asyncio
asyncio.run(create())
```

### "Project not loading"

Check:
1. ProjectInfo record exists in database
2. PNG file is in correct location: `projects/{person_id}/{tx}_{ty}_{px}_{py}.png`
3. File uses WPlace palette (first color = transparent)
4. File size matches ProjectInfo bounds

### "YAML metadata not migrating"

YAML migration only happens if:
1. ProjectInfo record exists with matching name
2. YAML file exists at `metadata/{name}.metadata.yaml`
3. Project is loaded for the first time

Create the ProjectInfo record first, then restart pixel-hawk.

## New Workflow Reference

### Adding a new project

```powershell
# Use helper script
uv run python scripts/add_project.py

# Or manually create in Python:
# 1. Create/get Person
# 2. Create ProjectInfo with from_rect()
# 3. Place PNG file at projects/{person_id}/{filename}
# 4. Restart pixel-hawk
```

### Changing project state

```python
from pixel_hawk.db import database
from pixel_hawk.models import ProjectInfo, ProjectState

async def change_state():
    async with database():
        info = await ProjectInfo.get(id=1)  # Your project ID
        info.state = ProjectState.PASSIVE   # or INACTIVE, ACTIVE
        await info.save()

import asyncio
asyncio.run(change_state())
```

### Viewing database contents

```python
from pixel_hawk.db import database
from pixel_hawk.models import Person, ProjectInfo

async def list_all():
    async with database():
        persons = await Person.all()
        for person in persons:
            print(f"\nPerson: {person.name} (ID: {person.id})")
            projects = await ProjectInfo.filter(owner=person).all()
            for proj in projects:
                print(f"  - {proj.name}: {proj.state}, {proj.filename}")

import asyncio
asyncio.run(list_all())
```

## Benefits of New Architecture

- **Multi-user support**: Multiple people can track different projects
- **Named projects**: Human-readable names in database, clean filenames
- **State management**: Pause projects without deleting (passive/inactive)
- **Better tracking**: Watched tiles counted per person with overlap deduplication
- **Future-ready**: Foundation for quota enforcement and additional features
