from pathlib import Path

from loguru import logger
from watchfiles import Change, watch

from . import DIRS
from .geometry import Tile
from .ingest import TilePoller
from .projects import Project


class Main:
    def __init__(self):
        """Initialize the main application, loading existing projects and indexing tiles."""
        self.projects = {p.path: p for p in Project.iter()}
        logger.info(f"Loaded {len(self.projects)} projects.")
        self.tiles = self._load_tiles()

    def _load_tiles(self) -> dict[Tile, set[Project]]:
        """Index tiles to projects for quick lookup."""
        tile_to_project = {}
        for proj in self.projects.values():
            for tile in proj.rect.tiles:
                tile_to_project.setdefault(tile, set()).add(proj)
        logger.info(f"Indexed {len(tile_to_project)} tiles.")
        return tile_to_project

    def consume_new_tile(self, found: Tile) -> None:
        """Consume new tile provided by a downloader, updating projects as needed."""
        for proj in self.tiles.get(found) or ():
            proj.run_diff()

    def watch_for_updates(self) -> None:
        """Watch projects directory for changes, processing as needed."""
        logger.info("Watching for new tiles and projects...")
        with TilePoller(self.consume_new_tile, list(self.tiles.keys())) as poller:
            for change, path in self.watch_loop():
                if change != Change.added:
                    self.forget_project(path)
                if change != Change.deleted:
                    self.load_project(path)
                poller.tiles = list(self.tiles.keys())

    def watch_loop(self):
        """Yields file changes from watching the projects directory."""
        wplace_path = DIRS.user_pictures_path / "wplace"
        try:
            for batch in watch(wplace_path):
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
    worker.watch_for_updates()
    logger.info("Exiting.")


if __name__ == "__main__":
    main()
