"""Rebuild the pixel-hawk database from filesystem artifacts.

Reconstructs Person, ProjectInfo, TileInfo, and TileProject records by scanning
the projects/ and tiles/ directories. Idempotent — safe to re-run on an existing database.

What's recovered:
  - Person IDs (from projects/{id}/ directories), with placeholder names
  - ProjectInfo coordinates and bounds (from filenames + image dimensions)
  - TileInfo coordinates and timestamps (from tiles/tile-{x}_{y}.png mtimes)
  - TileProject relationships (computed from project rectangles)
  - HistoryChange records inferred from snapshots/tiles for projects with completed pixels

What's permanently lost:
  - Person and project names (placeholders used)
  - Granular HistoryChange timeline (only a single inferred record per project)
  - HTTP ETags and queue heat assignments (tiles start as burning)

Usage:
    uv run python scripts/rebuild.py
"""

import asyncio

from pixel_hawk.models.config import load_config
from pixel_hawk.models.db import database
from pixel_hawk.models.geometry import Point, Rectangle, Size
from pixel_hawk.models.entities import (
    DiffStatus,
    HistoryChange,
    Person,
    ProjectInfo,
    ProjectState,
    TileInfo,
    TileProject,
)
from pixel_hawk.models.palette import PALETTE
from pixel_hawk.watcher.projects import get_flattened_data, stitch_tiles


async def rebuild() -> None:
    config = load_config()
    async with database():
        persons_created = 0
        projects_created = 0
        tiles_created = 0
        relations_created = 0

        # --- Persons ---
        person_dirs = sorted(
            (d for d in config.projects_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: int(d.name),
        )
        for person_dir in person_dirs:
            person_id = int(person_dir.name)
            _, created = await Person.get_or_create(id=person_id, defaults={"name": f"Person {person_id}"})
            if created:
                persons_created += 1
                print(f"  + Person {person_id}")

        # --- Projects ---
        for person_dir in person_dirs:
            person_id = int(person_dir.name)
            for png_path in sorted(person_dir.glob("*.png")):
                parts = png_path.stem.split("_")
                if len(parts) != 4:
                    print(f"  ! Skipping {png_path.name} (unexpected filename format)")
                    continue

                tx, ty, px, py = map(int, parts)
                point = Point.from4(tx, ty, px, py)

                with PALETTE.open_file(png_path) as image:
                    size = Size(*image.size)

                rect = Rectangle.from_point_size(point, size)
                name = png_path.stem
                mtime = round(png_path.stat().st_mtime)

                existing = await ProjectInfo.filter(owner_id=person_id, x=rect.point.x, y=rect.point.y).first()
                if existing:
                    continue

                info = ProjectInfo(
                    owner_id=person_id,
                    name=name,
                    state=ProjectState.ACTIVE,
                    x=rect.point.x,
                    y=rect.point.y,
                    width=rect.size.w,
                    height=rect.size.h,
                    first_seen=mtime,
                    last_check=mtime,
                )
                await info.save_as_new()
                projects_created += 1
                print(f"  + Project {person_id}/{name} ({size.w}x{size.h})")

        # --- Tiles from cache ---
        for tile_path in sorted(config.tiles_dir.glob("tile-*.png")):
            coords = tile_path.stem.removeprefix("tile-")
            parts = coords.split("_")
            if len(parts) != 2:
                print(f"  ! Skipping {tile_path.name} (unexpected filename format)")
                continue

            tx, ty = map(int, parts)
            tile_id = TileInfo.tile_id(tx, ty)
            mtime = round(tile_path.stat().st_mtime)

            _, created = await TileInfo.get_or_create(
                id=tile_id,
                defaults={"x": tx, "y": ty, "heat": 999, "last_checked": mtime, "last_update": 0, "etag": ""},
            )
            if created:
                tiles_created += 1
                print(f"  + Tile ({tx}, {ty})")

        # --- TileProject relationships ---
        all_projects = await ProjectInfo.exclude(state=ProjectState.CREATING).all()
        for info in all_projects:
            rect = info.rectangle
            for tile in rect.tiles:
                tile_id = TileInfo.tile_id(tile.x, tile.y)

                # Ensure TileInfo exists (some tiles may not be cached yet)
                await TileInfo.get_or_create(
                    id=tile_id,
                    defaults={
                        "x": tile.x,
                        "y": tile.y,
                        "heat": 999,
                        "last_checked": 0,
                        "last_update": 0,
                        "etag": "",
                    },
                )

                _, created = await TileProject.get_or_create(tile_id=tile_id, project_id=info.id)
                if created:
                    relations_created += 1

        # --- Infer HistoryChange for projects with completed pixels ---
        history_created = 0
        for person_dir in person_dirs:
            person_id = int(person_dir.name)
            projects = await ProjectInfo.filter(owner_id=person_id).exclude(state=ProjectState.CREATING).all()
            for info in projects:
                existing = await HistoryChange.filter(project=info).exists()
                if existing:
                    continue

                target_path = config.projects_dir / str(person_id) / info.filename
                with PALETTE.open_file(target_path) as target:
                    target_data = get_flattened_data(target)

                snapshot_path = config.snapshots_dir / str(person_id) / info.filename
                canvas_data = None
                timestamp = info.last_check

                if snapshot_path.exists():
                    with PALETTE.open_file(snapshot_path) as snapshot:
                        canvas_data = get_flattened_data(snapshot)
                    timestamp = round(snapshot_path.stat().st_mtime)
                else:
                    rect = info.rectangle
                    all_cached = all((config.tiles_dir / f"tile-{tile}.png").exists() for tile in rect.tiles)
                    if all_cached:
                        with await stitch_tiles(rect) as canvas:
                            canvas_data = get_flattened_data(canvas)

                if canvas_data is None:
                    continue

                num_target = sum(1 for v in target_data if v) or 1
                num_remaining = sum(1 for curr, tgt in zip(canvas_data, target_data) if tgt and curr != tgt)

                if num_remaining >= num_target:
                    continue

                completed = num_target - num_remaining
                percent = 100.0 - (num_remaining * 100.0 / num_target)
                status = DiffStatus.COMPLETE if num_remaining == 0 else DiffStatus.IN_PROGRESS

                await HistoryChange.create(
                    project=info,
                    timestamp=timestamp,
                    status=status,
                    num_remaining=num_remaining,
                    num_target=num_target,
                    completion_percent=percent,
                    progress_pixels=completed,
                    regress_pixels=0,
                )

                info.max_completion_pixels = num_remaining
                info.max_completion_percent = percent
                info.max_completion_time = timestamp
                info.total_progress = completed
                await info.save()

                history_created += 1
                print(f"  + History for {person_id}/{info.name} ({percent:.1f}% complete)")

        # --- Update person totals ---
        all_persons = await Person.all()
        for person in all_persons:
            await person.update_totals()

        # --- Summary ---
        print()
        print("Rebuild complete:")
        print(f"  Persons:       {persons_created} created, {len(person_dirs)} total")
        print(f"  Projects:      {projects_created} created, {len(all_projects)} total")
        total_tiles = await TileInfo.all().count()
        print(f"  Tiles:         {tiles_created} created, {total_tiles} total")
        total_relations = await TileProject.all().count()
        print(f"  Relationships: {relations_created} created, {total_relations} total")
        print(f"  History:       {history_created} inferred")


if __name__ == "__main__":
    asyncio.run(rebuild())
