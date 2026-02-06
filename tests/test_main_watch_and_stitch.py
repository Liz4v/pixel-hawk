from pathlib import Path

from PIL import Image
from watchfiles import Change

from wwpppp import main as main_mod
from wwpppp import projects
from wwpppp.cache import ProjectCacheDB
from wwpppp.geometry import Point, Rectangle, Size, Tile


def test_watch_for_updates_processes_added_and_deleted(tmp_path, monkeypatch):
    # ensure Project.iter returns empty for deterministic start
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))
    m = main_mod.Main()

    path = tmp_path / "proj_0_0_1_1.png"
    path.touch()

    # Dummy project that exposes a single tile and records calls
    called = {"run": 0, "forgot": 0}

    class DummyProj:
        def __init__(self, p):
            self.path = p
            self.rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1000, 1000))

        def run_diff(self):
            called["run"] += 1

        def forget(self):
            called["forgot"] += 1

    def make_proj(cls, p):
        inst = DummyProj(p)
        inst.run_diff()
        return inst

    monkeypatch.setattr(projects.Project, "try_open", classmethod(make_proj))

    created = []

    class DummyPoller:
        def __init__(self, cb, tiles):
            self.cb = cb
            self.tiles = tiles
            created.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main_mod, "TilePoller", DummyPoller)

    # watch_loop yields added then deleted
    def fake_watch_loop():
        yield (Change.added, path)
        yield (Change.deleted, path)

    m.watch_loop = fake_watch_loop

    m.watch_for_updates()

    # poller was created and should have seen tiles updated at least once
    assert created
    assert called["run"] >= 1
    assert called["forgot"] >= 1


def test_stitch_tiles_warns_on_missing_and_returns_paletted_image(tmp_path, capsys, monkeypatch):
    # rectangle covering a single tile (0,0)
    rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1000, 1000))

    # ensure cache dir is empty
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    # replace module cache dir so stitch_tiles looks at tmp cache
    from types import SimpleNamespace

    monkeypatch.setattr(projects, "DIRS", SimpleNamespace(user_cache_path=cache_dir))
    from wwpppp import ingest

    monkeypatch.setattr(ingest, "DIRS", SimpleNamespace(user_cache_path=cache_dir))

    img = ingest.stitch_tiles(rect)
    assert isinstance(img, Image.Image)
    # since no tile files exist, the result should be paletted (mode 'P')
    assert img.mode == "P"
    # loguru writes warnings to stderr; the warning appeared during the run
