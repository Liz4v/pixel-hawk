"""Configuration management for cam.

Provides Config dataclass with unified directory structure and load_config()
function to parse CLI arguments, environment variables, and defaults.

Default cam-home is ./cam-data (relative to current working directory).
Can be overridden with --cam-home CLI flag or CAM_HOME environment variable.
Precedence: CLI flag > env var > default
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Configuration with unified directory structure.

    All cam data lives under a single home directory with organized subdirectories.
    """

    home: Path

    @property
    def projects_dir(self) -> Path:
        """Directory for project PNG files."""
        return self.home / "projects"

    @property
    def snapshots_dir(self) -> Path:
        """Directory for canvas state snapshots."""
        return self.home / "snapshots"

    @property
    def metadata_dir(self) -> Path:
        """Directory for project metadata YAML files."""
        return self.home / "metadata"

    @property
    def tiles_dir(self) -> Path:
        """Directory for downloaded tile cache."""
        return self.home / "tiles"

    @property
    def logs_dir(self) -> Path:
        """Directory for application logs."""
        return self.home / "logs"

    @property
    def data_dir(self) -> Path:
        """Directory for future bot data and state."""
        return self.home / "data"


def load_config(args: list[str] | None = None) -> Config:
    """Load configuration from CLI args, environment, or defaults.

    Precedence: CLI flag > env var > default (./cam-data)

    Args:
        args: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Config instance with absolute path for home
    """
    if args is None:
        args = sys.argv[1:]

    # Check CLI argument: --cam-home /path/to/cam-data
    home_path: Path | None = None
    for i, arg in enumerate(args):
        if arg == "--cam-home" and i + 1 < len(args):
            home_path = Path(args[i + 1])
            break

    # Check environment variable: CAM_HOME
    if home_path is None:
        env_home = os.environ.get("CAM_HOME")
        if env_home:
            home_path = Path(env_home)

    # Fall back to default: ./cam-data
    if home_path is None:
        home_path = Path("./cam-data")

    # Convert to absolute path
    home_path = home_path.resolve()

    cfg = Config(home=home_path)

    # Initialize all subdirectories
    cfg.projects_dir.mkdir(parents=True, exist_ok=True)
    cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)
    cfg.metadata_dir.mkdir(parents=True, exist_ok=True)
    cfg.tiles_dir.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    return cfg


CONFIG: Config | None = None


def get_config() -> Config:
    """Returns the global CONFIG instance.

    Raises:
        RuntimeError: If CONFIG has not been initialized by main()
    """
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config()
    return CONFIG
