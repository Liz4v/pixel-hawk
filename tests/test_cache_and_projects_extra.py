import types
from pathlib import Path

import pytest

from wwpppp import projects
from wwpppp.cache import ProjectCacheDB
from wwpppp.geometry import Point, Rectangle, Size
from wwpppp.palette import PALETTE, ColorNotInPalette


def test_cache_db_commit_and_close_exceptions(monkeypatch, tmp_path):
    """Ensure exceptions during commit/close are swallowed and cursor yields."""

    class FakeCursor:
        def execute(self, sql, params=None):
            return None

        def fetchone(self):
            return None

    class FakeConn:
        def __init__(self):
            self._cursor = FakeCursor()

        def cursor(self):
            return self._cursor

        def commit(self):
            raise Exception("commit-fail")

        def close(self):
            raise Exception("close-fail")

    def fake_connect(path, isolation_level=None):
        return FakeConn()

    monkeypatch.setattr("sqlite3.connect", fake_connect)

    db = ProjectCacheDB(tmp_path / "cache")
    with db.cursor() as cur:
        cur.execute("SELECT 1")


def test_cachedprojectmetadata_save_load(tmp_path, monkeypatch):
    # Use a fresh cache DB per test
    cache_dir = tmp_path / "cache"
    cache_db = ProjectCacheDB(cache_dir)
    projects._CACHE_DB = cache_db

    # create a dummy project file
    p = tmp_path / "proj_0_0_1_1.png"
    # create a paletted image using PALETTE.new so it's valid
    img = PALETTE.new((2, 2))
    img.putdata([1, 0, 0, 0])
    img.save(p)

    meta = projects.CachedProjectMetadata(p)
    assert list(meta) == []

    rect = Rectangle.from_point_size(Point.from4(0, 0, 1, 1), Size(2, 2))
    meta(rect)

    # Reload should return the rect
    meta2 = projects.CachedProjectMetadata(p)
    assert len(meta2) == 1
    assert isinstance(meta2[0], Rectangle)


def test_cachedprojectmetadata_value_error_resets(monkeypatch, tmp_path):
    # Monkeypatch cursor to return a row with wrong size to trigger ValueError
    called = {}

    class FakeCursor:
        def execute(self, sql, params=None):
            return None

        def fetchone(self):
            return ("only_one",)

    class FakeCM:
        def __enter__(self):
            return FakeCursor()

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_db = types.SimpleNamespace()

    def fake_cursor():
        return FakeCM()

    def fake_reset():
        called["reset"] = True

    fake_db.cursor = fake_cursor
    fake_db.reset_table = fake_reset

    monkeypatch.setattr(projects, "_CACHE_DB", fake_db)

    p = tmp_path / "proj_0_0_1_1.png"
    p.touch()

    # instantiation should call _reset_table via ValueError path
    projects.CachedProjectMetadata(p)
    assert called.get("reset") is True


def test_project_try_open_invalid_palette(tmp_path):
    # create image with color not in palette
    p = tmp_path / "project_0_0_1_1.png"
    from PIL import Image

    img = Image.new("RGBA", (1, 1), (1, 2, 3, 255))
    img.save(p)

    res = projects.Project.try_open(p)
    assert res is None
    # file should be renamed to .invalid.png
    assert any(str(p.with_suffix(".invalid.png")) in str(x) for x in [p.with_suffix(".invalid.png")])
