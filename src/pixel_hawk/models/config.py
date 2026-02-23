"""Configuration management for pixel-hawk.

Provides Config dataclass with unified directory structure and load_config()
function to parse CLI arguments, environment variables, and defaults.

Default nest is ./nest (relative to current working directory).
Can be overridden with --nest CLI flag or HAWK_NEST environment variable.
Precedence: CLI flag > env var > default
"""

import functools
import os
import sys
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class DiscordSettings:
    """Typed settings from the [discord] section of config.toml."""

    bot_token: str = ""
    command_prefix: str = "hawk"


@dataclass
class Config:
    """Configuration with unified directory structure.

    All pixel-hawk data lives under a single home directory with organized subdirectories.
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
    def tiles_dir(self) -> Path:
        """Directory for downloaded tile cache."""
        return self.home / "tiles"

    @property
    def logs_dir(self) -> Path:
        """Directory for application logs."""
        return self.home / "logs"

    @property
    def rejected_dir(self) -> Path:
        """Directory for project files that failed to import."""
        return self.home / "rejected"

    @property
    def data_dir(self) -> Path:
        """Directory for SQLite database and bot data."""
        return self.home / "data"

    @functools.cached_property
    def config_toml(self) -> dict:
        """Read config.toml from nest root, defaulting to empty dict."""
        path = self.home / "config.toml"
        try:
            with path.open("rb") as f:
                return tomllib.load(f)
        except (IOError, ValueError):
            return {}

    @functools.cached_property
    def discord(self) -> DiscordSettings:
        """Typed access to [discord] settings from config.toml."""
        raw = self.config_toml.get("discord", {})
        return DiscordSettings(**{f.name: raw[f.name] for f in fields(DiscordSettings) if f.name in raw})


def load_config(args: list[str] | None = None) -> Config:
    """Load configuration from CLI args, environment, or defaults.

    Precedence: CLI flag > env var > default (./nest)

    Args:
        args: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Config instance with absolute path for home
    """
    if args is None:
        args = sys.argv[1:]

    # Check CLI argument: --nest /path/to/nest
    home_path: Path | None = None
    for i, arg in enumerate(args):
        if arg == "--nest" and i + 1 < len(args):
            home_path = Path(args[i + 1])
            break

    # Check environment variable: HAWK_NEST
    if home_path is None:
        env_home = os.environ.get("HAWK_NEST")
        if env_home:
            home_path = Path(env_home)

    # Fall back to default: ./nest
    if home_path is None:
        home_path = Path("./nest")

    # Convert to absolute path
    home_path = home_path.resolve()

    cfg = Config(home=home_path)

    # Initialize all subdirectories
    cfg.projects_dir.mkdir(parents=True, exist_ok=True)
    cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)
    cfg.tiles_dir.mkdir(parents=True, exist_ok=True)
    cfg.rejected_dir.mkdir(parents=True, exist_ok=True)
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
