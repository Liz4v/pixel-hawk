"""Application entry point and unified polling loop.

The Main class orchestrates the application lifecycle: it loads existing projects
on startup, indexes their tiles, and runs a ~97 second polling loop (60φ seconds,
chosen to avoid resonance with WPlace's internal timers). Each cycle checks one
tile for changes (via TileChecker) and scans the projects directory for new,
modified, or deleted project files.
"""

import time
from pathlib import Path

from loguru import logger

from .config import get_config
from .ingest import TileChecker
from .projects import Project

# Polling cycle period: 60φ = 30(1 + √5) ≈ 97.08 seconds
# Chosen to be maximally dissonant with 27s and 30s periods
POLLING_CYCLE_SECONDS = 30 * (1 + 5**0.5)


class Main:
    def __init__(self):
        """Initialize the main application, loading existing projects and indexing tiles."""
        self.projects = {p.path: p for p in Project.iter()}
        logger.info(f"Loaded {len(self.projects)} projects.")
        self.tile_checker = TileChecker(self.projects.values())

    def check_projects(self) -> None:
        """Check projects directory for added, modified, or deleted files."""
        current_files = Project.scan_directory()
        known_files = set(self.projects.keys())
        for path in known_files - current_files:
            self.forget_project(path)  # deleted file
        for path in current_files:
            self.maybe_load_project(path)

    def poll_once(self) -> None:
        """Run a cycle of the main polling loop."""
        logger.debug("Checking for project file changes...")
        self.check_projects()
        logger.debug("Checking for tile updates...")
        self.tile_checker.check_next_tile()

    def forget_project(self, path: Path) -> None:
        """Clears cached data about the project at the given path."""
        proj = self.projects.pop(path, None)
        if not proj:
            return
        self.tile_checker.remove_project(proj)
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
        self.tile_checker.add_project(proj)
        if proj.rect:  # Only log for valid projects
            logger.info(f"{path.name}: Loaded project")


def main():
    """Main entry point for pixel-hawk."""
    # Set up logging
    cfg = get_config()
    log_file = cfg.logs_dir / "pixel-hawk.log"
    log_fmt = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    logger.add(log_file, rotation="10 MB", retention="7 days", level="DEBUG", format=log_fmt)
    logger.info("============================================================================================")
    logger.info("pixel-hawk - WPlace paint project change tracker")
    logger.debug(f"pixel-hawk-home: {cfg.home}")
    logger.debug(f"Logging to file: {log_file}")
    logger.info(f"Place project PNG files in: {cfg.projects_dir}")
    # set up main loop
    worker = Main()
    consecutive_errors = 0
    logger.info(f"Starting polling loop ({POLLING_CYCLE_SECONDS:.1f}s cycle, 60φ = 30(1+√5))...")
    while True:
        try:
            worker.poll_once()
            consecutive_errors = 0  # Reset on success
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Error during polling cycle: {e} (consecutive errors: {consecutive_errors})")
            if consecutive_errors >= 3:
                logger.critical("Three consecutive errors encountered. Exiting.")
                raise
        logger.debug(f"Cycle complete, sleeping for {POLLING_CYCLE_SECONDS:.1f} seconds...")
        try:
            time.sleep(POLLING_CYCLE_SECONDS)
        except KeyboardInterrupt:
            logger.info("Exiting due to user interrupt.")
            return


if __name__ == "__main__":
    main()
