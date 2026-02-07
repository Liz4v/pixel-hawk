import time
from pathlib import Path

from loguru import logger

from .geometry import Tile
from .ingest import has_tile_changed
from .projects import Project, ProjectShim

# Polling cycle period: 60φ = 30(1 + √5) ≈ 97.08 seconds
# Chosen to be maximally dissonant with 27s and 30s periods
POLLING_CYCLE_SECONDS = 30 * (1 + 5**0.5)


class Main:
    def __init__(self):
        """Initialize the main application, loading existing projects and indexing tiles."""
        self.projects = {p.path: p for p in Project.iter()}
        logger.info(f"Loaded {len(self.projects)} projects.")
        self.tiles = self._load_tiles()
        self.current_tile_index = 0  # Track which tile to check next in round-robin

    def _load_tiles(self) -> dict[Tile, set[ProjectShim]]:
        """Index tiles to projects for quick lookup."""
        tile_to_project = {}
        for proj in self.projects.values():
            for tile in proj.rect.tiles:
                tile_to_project.setdefault(tile, set()).add(proj)
        logger.info(f"Indexed {len(tile_to_project)} tiles.")
        return tile_to_project

    def check_tiles(self) -> None:
        """Check one tile for changes (round-robin) and update affected projects."""
        if not self.tiles:
            return  # No tiles to check

        tiles_list = list(self.tiles.keys())
        # Handle case where tiles were removed and index is now out of bounds
        if self.current_tile_index >= len(tiles_list):
            self.current_tile_index = 0

        tile = tiles_list[self.current_tile_index]
        if has_tile_changed(tile):
            for proj in self.tiles.get(tile) or ():
                proj.run_diff()
        # Advance to next tile for next cycle
        self.current_tile_index = (self.current_tile_index + 1) % len(tiles_list)

    def check_projects(self) -> None:
        """Check projects directory for added, modified, or deleted files."""
        current_files = Project.scan_directory()
        known_files = set(self.projects.keys())
        for path in known_files - current_files:
            self.forget_project(path)  # deleted file
        for path in current_files:
            self.maybe_load_project(path)

    def run_forever(self) -> None:
        """Run the main polling loop, checking tiles and projects every ~97 seconds (60φ)."""
        logger.info(f"Starting polling loop ({POLLING_CYCLE_SECONDS:.1f}s cycle, 60φ = 30(1+√5))...")
        try:
            while True:
                logger.debug("Checking for tile updates...")
                self.check_tiles()
                logger.debug("Checking for project file changes...")
                self.check_projects()
                logger.debug(f"Cycle complete, sleeping for {POLLING_CYCLE_SECONDS:.1f} seconds...")
                time.sleep(POLLING_CYCLE_SECONDS)
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")

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
        if proj.rect.tiles:  # Only log for valid projects
            logger.info(f"{path.name}: Forgot project")

    def maybe_load_project(self, path: Path) -> None:
        """Checks a potential project file at the given path to see if it needs loading or reloading."""
        proj = self.projects.get(path)
        if proj and not proj.has_been_modified():
            return  # no change
        self.forget_project(path)
        proj = Project.try_open(path)
        self.projects[path] = proj
        for tile in proj.rect.tiles:
            self.tiles.setdefault(tile, set()).add(proj)
        if proj.rect:  # Only log for valid projects
            logger.info(f"{path.name}: Loaded project")


def main():
    """Main entry point for wwpppp."""
    worker = Main()
    worker.run_forever()
    logger.info("Exiting.")


if __name__ == "__main__":
    main()
