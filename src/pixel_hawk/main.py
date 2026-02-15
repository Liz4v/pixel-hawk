"""Application entry point and unified polling loop.

The Main class orchestrates the application lifecycle: it loads projects from the
database on startup, indexes their tiles, and runs a ~97 second polling loop (60φ seconds,
chosen to avoid resonance with WPlace's internal timers). Each cycle checks one
tile for changes (via TileChecker).

Projects are loaded from the database (not discovered from filesystem) and are static
after initial load. Future project management will be via API/UI that modifies ProjectInfo
records. Application restart required to pick up new projects.
"""

import asyncio

from loguru import logger

from .config import get_config
from .db import database
from .ingest import TileChecker
from .models import Person, ProjectInfo, ProjectState
from .projects import Project

# Polling cycle period: 60φ = 30(1 + √5) ≈ 97.08 seconds
# Chosen to be maximally dissonant with 27s and 30s periods
POLLING_CYCLE_SECONDS = 30 * (1 + 5**0.5)


class Main:
    def __init__(self):
        """Initialize the main application (sync setup only). Call start() to load projects."""
        self.projects: dict[int, Project] = {}  # Keyed by ProjectInfo.id
        self.tile_checker: TileChecker | None = None

    async def start(self) -> None:
        """Load projects from database and initialize tile checker."""
        # Query all ProjectInfo records (active + passive, exclude inactive)
        infos = await ProjectInfo.filter(state__in=[ProjectState.ACTIVE, ProjectState.PASSIVE]).prefetch_related("owner").all()

        # Load projects from database
        projects_list = []
        for info in infos:
            project = await Project.from_info(info)
            if project is None:
                logger.warning(f"{info.owner.name}/{info.name}: Skipping - file missing or invalid")
                continue
            projects_list.append(project)

        self.projects = {p.info.id: p for p in projects_list}
        logger.info(f"Loaded {len(self.projects)} projects from database.")

        # Update watched tiles counts
        await self._update_all_person_tile_counts()

        # Initialize tile checker with only ACTIVE projects
        active_projects = [p for p in projects_list if p.info.state == ProjectState.ACTIVE]
        self.tile_checker = TileChecker(active_projects)
        logger.info(f"Monitoring {len(active_projects)} active projects ({len(projects_list) - len(active_projects)} passive).")

    async def _update_all_person_tile_counts(self) -> None:
        """Update watched tiles count for all persons."""
        persons = await Person.all()
        for person in persons:
            await person.update_watched_tiles_count()
            active_count = await person.projects.filter(state=ProjectState.ACTIVE).count()
            logger.info(f"{person.name}: Watching {person.watched_tiles_count} tiles across {active_count} active projects")

    async def poll_once(self) -> None:
        """Run a cycle of the main polling loop."""
        assert self.tile_checker is not None, "Must call start() before poll_once()"
        logger.debug("Checking for tile updates...")
        await self.tile_checker.check_next_tile()


async def _async_main():
    """Async entry point for pixel-hawk."""
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
    # Initialize database and run main loop
    async with database():
        # set up main loop
        worker = Main()
        await worker.start()
        consecutive_errors = 0
        logger.info(f"Starting polling loop ({POLLING_CYCLE_SECONDS:.1f}s cycle, 60φ = 30(1+√5))...")
        while True:
            try:
                await worker.poll_once()
                consecutive_errors = 0  # Reset on success
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error during polling cycle: {e} (consecutive errors: {consecutive_errors})")
                if consecutive_errors >= 3:
                    logger.critical("Three consecutive errors encountered. Exiting.")
                    raise
            logger.debug(f"Cycle complete, sleeping for {POLLING_CYCLE_SECONDS:.1f} seconds...")
            try:
                await asyncio.sleep(POLLING_CYCLE_SECONDS)
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.info("Exiting due to user interrupt.")
                return


def main():
    """Main entry point for pixel-hawk."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
