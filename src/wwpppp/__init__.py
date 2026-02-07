"""Platform directory management for wwpppp.

Provides DIRS, a PlatformDirs instance configured for the application.
User project images should be placed in DIRS.user_pictures_path / 'wplace'.
Downloaded tiles are cached in DIRS.user_cache_path.
"""

from platformdirs import PlatformDirs

DIRS = PlatformDirs("wwpppp", ensure_exists=True)
