from pathlib import Path

from loguru import logger
from watchfiles import Change, watch

from . import DIRS
from .ingest import search_tiles
from .projects import Project


class Main:
    def __init__(self):
        self.projects = {p.path: p for p in Project.iter()}
        logger.info(f"Loaded {len(self.projects)} projects.")
        self.tiles = self._load_tiles()

    def _load_tiles(self) -> dict[tuple[int, int], set[Project]]:
        tile_to_project = {}
        for proj in self.projects.values():
            for tile in proj.rect.tiles:
                tile_to_project.setdefault(tile, set()).add(proj)
        logger.info(f"Indexed {len(tile_to_project)} tiles.")
        return tile_to_project

    def consume_new_tiles(self, path: Path | None = None) -> None:
        # Find and save relevant updates from inbox directory
        seen_tiles = set()
        for found in search_tiles(path):
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

    def watch_for_updates(self) -> None:
        inbox_path = DIRS.user_downloads_path
        logger.info("Watching for new tiles and projects...")
        for changes in watch(inbox_path, DIRS.user_pictures_path / "wplace"):
            for change, pstr in changes:
                path = Path(pstr)
                if path.parent == inbox_path:
                    if change == Change.added:
                        self.consume_new_tiles(path)
                else:
                    if change != Change.added:
                        self.forget_project(path)
                    if change != Change.deleted:
                        self.load_project(path)

    def forget_project(self, path: Path) -> None:
        proj = self.projects.pop(path, None)
        if not proj:
            return
        for tile in proj.rect.tiles:
            projs = self.tiles.get(tile)
            if projs:
                projs.discard(proj)
                if not projs:
                    del self.tiles[tile]
        logger.info(f"{path.name}: Forgot project")

    def load_project(self, path: Path) -> None:
        self.forget_project(path)
        proj = Project.try_open(path)
        if not proj:
            return
        self.projects[path] = proj
        for tile in proj.rect.tiles:
            self.tiles.setdefault(tile, set()).add(proj)
        logger.info(f"{path.name}: Loaded project")


def main():
    worker = Main()
    worker.consume_new_tiles()
    worker.watch_for_updates()


if __name__ == "__main__":
    main()
