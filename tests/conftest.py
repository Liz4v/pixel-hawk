import pytest
from loguru import logger

import pixel_hawk.config
from pixel_hawk.config import Config
from pixel_hawk.db import close_db, init_db


@pytest.fixture(autouse=True)
def setup_config(tmp_path, monkeypatch):
    """Set up test CONFIG before each test using tmp_path."""
    # Create Config with test-specific tmp_path
    config = Config(home=tmp_path / "pixel-hawk-data")

    # Set module-level CONFIG
    pixel_hawk.config.CONFIG = config

    # Create all subdirectories for tests
    config.projects_dir.mkdir(parents=True, exist_ok=True)
    config.snapshots_dir.mkdir(parents=True, exist_ok=True)
    config.metadata_dir.mkdir(parents=True, exist_ok=True)
    config.tiles_dir.mkdir(parents=True, exist_ok=True)
    config.rejected_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)

    yield config

    # Cleanup: Reset CONFIG after test
    pixel_hawk.config.CONFIG = None


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize in-memory SQLite for each test and tear down after."""
    await init_db(db_path=":memory:")
    yield
    await close_db()


@pytest.fixture(autouse=True)
def disable_file_logging(monkeypatch):
    """Prevent logger.add() from creating file handlers during tests."""
    original_add = logger.add

    def mock_add(sink, **kwargs):
        # Only allow non-file sinks (like sys.stderr which is the default)
        # Block file path sinks to prevent log files during tests
        if hasattr(sink, "__fspath__") or isinstance(sink, (str, bytes)):
            return None  # Return dummy handler ID
        return original_add(sink, **kwargs)

    monkeypatch.setattr(logger, "add", mock_add)
    yield
