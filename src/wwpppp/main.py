from pathlib import Path

from loguru import logger
from watchfiles import Change, watch

from . import DIRS
from .ingest import search_tiles
from .projects import Project


class Main:
    def __init__(self):
        """Initialize the main application, loading existing projects and indexing tiles."""
        self.projects = {p.path: p for p in Project.iter()}
        logger.info(f"Loaded {len(self.projects)} projects.")
        self.tiles = self._load_tiles()

    def _load_tiles(self) -> dict[tuple[int, int], set[Project]]:
        """Index tiles to projects for quick lookup."""
        tile_to_project = {}
        for proj in self.projects.values():
            for tile in proj.rect.tiles:
                tile_to_project.setdefault(tile, set()).add(proj)
        logger.info(f"Indexed {len(tile_to_project)} tiles.")
        return tile_to_project

    def consume_new_tiles(self, path: Path | None = None) -> None:
        """Consume new tiles from the inbox directory (or given path), updating projects as needed."""
        # Find and save relevant updates from inbox directory
        seen_tiles = set()
        for found in search_tiles(path):
            if found.tile not in self.tiles or found.tile in seen_tiles:
                continue  # no projects need this tile
            if found.obtain():
                seen_tiles.add(found.tile)
        if not seen_tiles:
            return
        logger.info(f"Matched {len(seen_tiles)} new tiles.")

        # Rebuild partials as needed
        targets = {proj for tile in seen_tiles for proj in self.tiles[tile]}
        for proj in targets:
            proj.run_diff()

    def watch_for_updates(self) -> None:
        """Watch inbox and projects directories for changes, processing as needed."""
        logger.info("Watching for new tiles and projects...")
        for change, path in self.watch_loop():
            if path.parent == DIRS.user_downloads_path:
                if change == Change.added:
                    self.consume_new_tiles(path)
            else:
                if change != Change.added:
                    self.forget_project(path)
                if change != Change.deleted:
                    self.load_project(path)

    def watch_loop(self):
        """Yields file changes from watching the inbox and projects directories."""
        inbox_path = DIRS.user_downloads_path
        wplace_path = DIRS.user_pictures_path / "wplace"
        try:
            for batch in watch(inbox_path, wplace_path):
                for change, path_str in batch:
                    yield change, Path(path_str)
        except KeyboardInterrupt:
            pass

    def forget_project(self, path: Path) -> None:
        """Clears cached data about the project at the given path."""
        proj = self.projects.pop(path, None)
        if not proj:
            return
        for tile in proj.rect.tiles:
            projs = self.tiles.get(tile)
            if projs:
                projs.discard(proj)
                if not projs:
                    del self.tiles[tile]
        proj.forget()
        logger.info(f"{path.name}: Forgot project")

    def load_project(self, path: Path) -> None:
        """Loads or reloads a project at the given path."""
        self.forget_project(path)
        proj = Project.try_open(path)
        if not proj:
            return
        self.projects[path] = proj
        for tile in proj.rect.tiles:
            self.tiles.setdefault(tile, set()).add(proj)
        logger.info(f"{path.name}: Loaded project")


def main():
    """Main entry point for wwpppp."""
    worker = Main()
    worker.consume_new_tiles()
    worker.watch_for_updates()
    logger.info("Exiting.")


if __name__ == "__main__":
    main()
