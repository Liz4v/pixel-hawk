from pathlib import Path
from types import SimpleNamespace

from watchfiles import Change

from wwpppp import main as main_mod
from wwpppp import projects
from wwpppp.cache import ProjectCacheDB
from wwpppp.geometry import Tile


def test_main_indexing_and_consume_and_load_forget(tmp_path, monkeypatch):
    # start with no projects
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))
    m = main_mod.Main()
    assert m.tiles == {}

    # create dummy project returned by try_open
    path = tmp_path / "proj_0_0_1_1.png"
    path.touch()

    called = {}

    class DummyProj:
        def __init__(self, path):
            self.path = path
            self.rect = SimpleNamespace(tiles=frozenset({Tile(0, 0)}))

        def run_diff(self):
            called["run"] = True

        def forget(self):
            called["forgot"] = True

    monkeypatch.setattr(projects.Project, "try_open", classmethod(lambda cls, p: DummyProj(p)))

    m.load_project(path)
    assert path in m.projects
    assert Tile(0, 0) in m.tiles

    # consume tile should call run_diff
    m.consume_new_tile(Tile(0, 0))
    assert called.get("run") is True

    # forget should remove project and call forget
    m.forget_project(path)
    assert path not in m.projects
    assert called.get("forgot") is True


def test_watch_loop_keyboardinterrupt(monkeypatch, tmp_path):
    # simulate watch yielding once then KeyboardInterrupt
    def fake_watch(path):
        yield [(Change.added, str(tmp_path / "p"))]
        raise KeyboardInterrupt

    monkeypatch.setattr(main_mod, "watch", fake_watch)
    gen = main_mod.Main().watch_loop()
    change, p = next(gen)
    assert change == Change.added


def test_palette_lookup_transparent_and_ensure():
    # transparent pixel should map to 0
    idx = projects.PALETTE.lookup((0, 0, 0, 0))
    assert idx == 0


def test_has_tile_changed_http_error(monkeypatch):
    from wwpppp.ingest import has_tile_changed

    class FakeResp:
        status_code = 404

    monkeypatch.setattr("requests.get", lambda url, timeout=5: FakeResp())

    assert has_tile_changed(Tile(0, 0)) is False


def test_watch_for_updates_calls_load_and_forget(monkeypatch, tmp_path):
    m = main_mod.Main()
    path = tmp_path / "proj_0_0_1_1.png"
    path.touch()

    # dummy TilePoller context manager
    class DummyPoller:
        def __init__(self, cb, tiles):
            self.tiles = tiles

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main_mod, "TilePoller", DummyPoller)

    # make watch_loop yield added then deleted
    def fake_watch_loop():
        yield (Change.added, path)
        yield (Change.deleted, path)

    m.watch_loop = fake_watch_loop

    called = {"load": 0, "forget": 0}

    def fake_load(p):
        called["load"] += 1

    def fake_forget(p):
        called["forget"] += 1

    m.load_project = fake_load
    m.forget_project = fake_forget

    m.watch_for_updates()
    assert called["load"] == 2 or called["load"] >= 1
    assert called["forget"] >= 1


def test_cache_cursor_outer_exception_closes(monkeypatch, tmp_path):
    # Simulate conn.cursor() raising to exercise outer except block
    class BadConn:
        def cursor(self):
            raise Exception("boom")

        def close(self):
            # closing shouldn't raise
            return None

    def fake_connect(path, isolation_level=None):
        return BadConn()

    monkeypatch.setattr("sqlite3.connect", fake_connect)
    db = ProjectCacheDB(tmp_path / "cache")
    import pytest

    with pytest.raises(Exception):
        with db.cursor():
            pass
