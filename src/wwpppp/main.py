from collections import defaultdict

from loguru import logger

from .ingest import search_tiles, stitch_tiles
from .projects import Project


def main():
    # Load projects and index by tile
    tile_to_project = defaultdict(list[Project])
    for proj in Project.iter():
        logger.info("Project found: %s at coords %s", proj.path, proj.coords)
        for tile in proj.rect.tiles:
            tile_to_project[tile].append(proj)
    # Find and save relevant updates from inbox directory
    seen_tiles = set[tuple[int, int]]()
    for found in search_tiles():
        if found.tile not in tile_to_project or found.tile in seen_tiles:
            continue  # no projects need this tile
        if found.obtain():
            seen_tiles.add(found.tile)
    # Rebuild partials as needed
    targets = {proj.path: proj for tile in seen_tiles for proj in tile_to_project[tile]}
    for proj in targets.values():
        current = stitch_tiles(proj.rect)
        proj.compare_with_current(current)


if __name__ == "__main__":
    main()
