# __init__.py is required to exist to mark this as a package. It must not
# be merged into anything else. It may be an acceptable candidate to have
# other small files merged into it, but ask the user before doing so.
"""Unified directory configuration for cam (Canvas Activity Monitor).

Provides CONFIG module variable set by main() at startup, and get_config()
helper function that returns CONFIG or raises RuntimeError if not initialized.

All cam data lives under CONFIG.home with organized subdirectories:
- projects/ - project PNG files
- snapshots/ - canvas state snapshots
- metadata/ - project statistics YAML files
- tiles/ - downloaded tile cache
- logs/ - application logs
- data/ - future bot data and state
"""
