from loguru import logger

from .ingest import search_tiles
from .projects import Project


class Main:
    def __init__(self):
        self.projects = Project.list()
        logger.info(f"Loaded {len(self.projects)} projects.")
        self.tiles = self._load_tiles()

    def _load_tiles(self) -> dict[tuple[int, int], set[Project]]:
        tile_to_project = {}
        for proj in self.projects:
            for tile in proj.rect.tiles:
                tile_to_project.setdefault(tile, set()).add(proj)
        logger.info(f"Indexed {len(tile_to_project)} tiles.")
        return tile_to_project

    def consume_new_tiles(self) -> None:
        # Find and save relevant updates from inbox directory
        seen_tiles = set()
        for found in search_tiles():
            if found.tile not in self.tiles or found.tile in seen_tiles:
                continue  # no projects need this tile
            if found.obtain():
                seen_tiles.add(found.tile)
        logger.info(f"Obtained {len(seen_tiles)} new tiles.")
        if not seen_tiles:
            return

        # Rebuild partials as needed
        targets = {proj for tile in seen_tiles for proj in self.tiles[tile]}
        logger.info(f"Rebuilding {len(targets)} project(s) from tiles")
        for proj in targets:
            proj.compare_with_current()


def main():
    Main().consume_new_tiles()


if __name__ == "__main__":
    main()
