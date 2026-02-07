"""Platform directory management for cam (Canvas Activity Monitor).

Provides DIRS, a PlatformDirs instance configured for the application.
User project images should be placed in DIRS.user_pictures_path / 'wplace'.
Downloaded tiles are cached in DIRS.user_cache_path.
"""

from platformdirs import PlatformDirs

DIRS = PlatformDirs("cam", ensure_exists=True)
