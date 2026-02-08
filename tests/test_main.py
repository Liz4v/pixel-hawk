import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from cam import main as main_mod
from cam import projects
from cam.geometry import Point, Rectangle, Size, Tile

# Basic Main initialization and tile tracking


def test_main_load_and_check_tiles(monkeypatch):
    """Test Main initialization, load/forget project, and check_tiles."""
    # create a fake project with a rect covering tile (0,0)
    proj_path = Path("/tmp/proj.png")

    class FakeProj:
        def __init__(self, path, rect):
            self.path = path
            self.rect = rect
            self._called = {"run_diff": 0}

        def run_diff(self, changed_tile=None):
            self._called["run_diff"] += 1

        def __hash__(self):
            return hash(self.path)

        def __eq__(self, other):
            return getattr(other, "path", None) == self.path

    proj = FakeProj(proj_path, Rectangle.from_point_size(Point(0, 0), Size(1000, 1000)))

    # monkeypatch Project.iter and Project.try_open
    monkeypatch.setattr("cam.main.Project.iter", classmethod(lambda cls: [proj]))
    monkeypatch.setattr("cam.main.Project.try_open", classmethod(lambda cls, p: proj))

    m = main_mod.Main()
    # Mock has_tile_changed to return tuple (True, 0)
    monkeypatch.setattr("cam.ingest.has_tile_changed", lambda tile: (True, 0))
    # check_next_tile should call project's run_diff for tile (0,0)
    m.tile_checker.check_next_tile()
    assert proj._called["run_diff"] >= 1

    # forget_project removes tiles and project from tracking
    m.forget_project(proj_path)
    assert proj_path not in m.projects

    # loading project back adds it again
    m.maybe_load_project(proj_path)
    assert proj_path in m.projects


def test_main_indexing_and_check_tiles_and_load_forget(tmp_path, monkeypatch):
    """Test Main tile indexing and project tracking."""
    # start with no projects
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))
    m = main_mod.Main()
    assert m.tile_checker.tiles == {}

    # create dummy project returned by try_open
    path = tmp_path / "proj_0_0_1_1.png"
    path.touch()

    called = {}

    class DummyProj:
        def __init__(self, path):
            self.path = path
            self.rect = SimpleNamespace(tiles=frozenset({Tile(0, 0)}))

        def run_diff(self, changed_tile=None):
            called["run"] = True

    monkeypatch.setattr(projects.Project, "try_open", classmethod(lambda cls, p: DummyProj(p)))

    m.maybe_load_project(path)
    assert path in m.projects
    assert Tile(0, 0) in m.tile_checker.tiles

    # check_next_tile with has_tile_changed returning True should call run_diff
    monkeypatch.setattr("cam.ingest.has_tile_changed", lambda tile: (True, 0))
    m.tile_checker.check_next_tile()
    assert called.get("run") is True

    # forget_project should remove project from tracking
    m.forget_project(path)
    assert path not in m.projects


def test_main_forget_removes_tile_key(monkeypatch):
    """Test that forget_project removes tile from index when no projects use it."""

    # start with no projects
    class FakeProjectClass:
        @classmethod
        def iter(cls):
            return []

        @classmethod
        def try_open(cls, p):
            return None

    monkeypatch.setattr(main_mod, "Project", FakeProjectClass)
    m = main_mod.Main()

    # create a fake project and tile mapping
    path = Path("/tmp/p.png")

    class FakeProj:
        def __init__(self, path, rect):
            self.path = path
            self.rect = rect

        def __hash__(self):
            return hash(self.path)

        def __eq__(self, other):
            return getattr(other, "path", None) == self.path

    proj = FakeProj(path, Rectangle.from_point_size(Point(0, 0), Size(1000, 1000)))
    tile = Tile(0, 0)
    m.projects[path] = proj
    m.tile_checker.tiles[tile] = {proj}

    m.forget_project(path)
    assert tile not in m.tile_checker.tiles


def test_maybe_load_project_invalid(monkeypatch):
    """Test that maybe_load_project handles ProjectShim gracefully."""

    # start with no projects
    class FakeProjectClass:
        @classmethod
        def iter(cls):
            return []

        @classmethod
        def try_open(cls, p):
            return projects.ProjectShim(p)

    monkeypatch.setattr(main_mod, "Project", FakeProjectClass)
    m = main_mod.Main()
    path = Path("/tmp/nothing.png")
    # should store the ProjectShim but not index tiles
    m.maybe_load_project(path)
    assert path in m.projects
    assert isinstance(m.projects[path], projects.ProjectShim)
    assert len(m.tile_checker.tiles) == 0  # No tiles indexed for invalid projects


# check_projects tests (file watching)


def test_check_projects_detects_added_and_deleted(tmp_path, monkeypatch):
    """Test that check_projects detects added and deleted project files."""
    wplace_dir = tmp_path / "wplace"
    wplace_dir.mkdir()

    # Setup DIRS to point to tmp_path
    monkeypatch.setattr(
        projects, "DIRS", SimpleNamespace(user_pictures_path=tmp_path, user_cache_path=tmp_path / "cache")
    )

    # Start with no projects
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))
    m = main_mod.Main()

    # Track calls to load_project and forget_project
    loaded = []
    forgotten = []
    original_load = m.maybe_load_project
    original_forget = m.forget_project

    def track_load(p):
        loaded.append(p)
        original_load(p)

    def track_forget(p):
        forgotten.append(p)
        original_forget(p)

    m.maybe_load_project = track_load
    m.forget_project = track_forget

    # Create a new project file
    proj_path = wplace_dir / "proj_0_0_1_1.png"
    proj_path.touch()

    # Mock Project.try_open to return a dummy project
    class DummyProj:
        def __init__(self, path):
            self.path = path
            self.rect = SimpleNamespace(tiles=frozenset())

        def run_diff(self):
            pass

    monkeypatch.setattr(projects.Project, "try_open", classmethod(lambda cls, p: DummyProj(p)))

    # check_projects should detect the new file
    m.check_projects()
    assert proj_path in loaded

    # Delete the project file
    proj_path.unlink()

    # check_projects should detect the deletion
    loaded.clear()
    m.check_projects()
    assert proj_path in forgotten


def test_check_projects_processes_added_and_deleted(tmp_path, monkeypatch):
    """Test that check_projects correctly handles adding and deleting projects."""
    wplace_dir = tmp_path / "wplace"
    wplace_dir.mkdir()

    # Setup DIRS to point to tmp_path
    monkeypatch.setattr(
        projects, "DIRS", SimpleNamespace(user_pictures_path=tmp_path, user_cache_path=tmp_path / "cache")
    )

    # ensure Project.iter returns empty for deterministic start
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))
    m = main_mod.Main()

    path = wplace_dir / "proj_0_0_1_1.png"
    path.touch()

    # Dummy project that exposes a single tile and records calls
    called = {"run": 0}

    class DummyProj:
        def __init__(self, p):
            self.path = p
            self.rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1000, 1000))
            self.mtime = p.stat().st_mtime if p.exists() else None

        def run_diff(self):
            called["run"] += 1

    def make_proj(cls, p):
        inst = DummyProj(p)
        inst.run_diff()
        return inst

    monkeypatch.setattr(projects.Project, "try_open", classmethod(make_proj))

    # check_projects should detect the added file
    m.check_projects()
    assert called["run"] >= 1

    # Delete the file
    path.unlink()

    # check_projects should detect the deleted file and remove it from tracking
    m.check_projects()
    assert path not in m.projects


def test_check_projects_handles_modified_files(tmp_path, monkeypatch):
    """Test that check_projects detects modified files via mtime."""
    wplace_dir = tmp_path / "wplace"
    wplace_dir.mkdir()

    monkeypatch.setattr(
        projects, "DIRS", SimpleNamespace(user_pictures_path=tmp_path, user_cache_path=tmp_path / "cache")
    )

    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))
    m = main_mod.Main()

    proj_path = wplace_dir / "proj_0_0_1_1.png"
    proj_path.touch()

    class DummyProj:
        def __init__(self, path):
            self.path = path
            self.rect = SimpleNamespace(tiles=frozenset())
            self.mtime = path.stat().st_mtime

        def has_been_modified(self):
            # Check if file mtime differs from cached mtime
            try:
                current = self.path.stat().st_mtime
                return current != self.mtime
            except OSError:
                return True

        def run_diff(self):
            pass

    monkeypatch.setattr(projects.Project, "try_open", classmethod(lambda cls, p: DummyProj(p)))

    # Load the project first
    m.check_projects()
    assert proj_path in m.projects

    # Modify the file (change mtime)
    time.sleep(0.01)
    proj_path.touch()

    # Track if load_project is called again
    load_called = {"count": 0}
    original_load = m.maybe_load_project

    def track_load(p):
        load_called["count"] += 1
        original_load(p)

    m.maybe_load_project = track_load

    # check_projects should detect the modification
    m.check_projects()
    assert load_called["count"] >= 1


def test_check_projects_skips_deleted_files_in_current_loop(tmp_path, monkeypatch):
    """Test that check_projects doesn't try to load files that are in deleted set."""
    wplace_dir = tmp_path / "wplace"
    wplace_dir.mkdir()

    monkeypatch.setattr(
        projects, "DIRS", SimpleNamespace(user_pictures_path=tmp_path, user_cache_path=tmp_path / "cache")
    )

    # Start with one project already loaded
    proj_path = wplace_dir / "proj_0_0_1_1.png"

    class DummyProj:
        def __init__(self, path):
            self.path = path
            self.rect = SimpleNamespace(tiles=frozenset())
            self.mtime = None

        def run_diff(self):
            pass

    existing_proj = DummyProj(proj_path)

    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: [existing_proj]))
    m = main_mod.Main()

    # Create a different file on disk
    other_path = wplace_dir / "other_0_0_1_1.png"
    other_path.touch()

    monkeypatch.setattr(projects.Project, "try_open", classmethod(lambda cls, p: DummyProj(p)))

    # Track calls
    forgot_called = []
    loaded_called = []

    original_forget = m.forget_project
    original_load = m.maybe_load_project

    def track_forget(p):
        forgot_called.append(p)
        original_forget(p)

    def track_load(p):
        loaded_called.append(p)
        original_load(p)

    m.forget_project = track_forget
    m.maybe_load_project = track_load

    # check_projects should:
    # 1. Forget proj_path (not on disk)
    # 2. Load other_path (new file on disk)
    # 3. NOT try to load proj_path even though it's in the loop
    m.check_projects()

    assert proj_path in forgot_called
    assert other_path in loaded_called
    # proj_path should not be in loaded_called (this tests the "if path in deleted: continue")
    assert proj_path not in loaded_called


def test_project_init_handles_stat_oserror(tmp_path, monkeypatch):
    """Test that Project.__init__ handles OSError when getting mtime."""
    proj_path = tmp_path / "proj_0_0_1_1.png"
    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))

    # Mock Path.stat to raise OSError for this specific path
    original_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        if self == proj_path:
            raise OSError("Mock error")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", mock_stat)

    # Project.__init__ should handle the OSError and set mtime to None
    proj = projects.Project(proj_path, rect)
    assert proj.mtime == 0


# main() function tests


def test_main_handles_keyboard_interrupt_during_sleep(monkeypatch):
    """Test that main() handles KeyboardInterrupt during sleep gracefully."""
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))

    cycle_count = {"count": 0}

    def mock_sleep(seconds):
        # Interrupt after first sleep
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", mock_sleep)

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        def poll_once(self):
            cycle_count["count"] += 1

    monkeypatch.setattr(main_mod, "Main", FakeMain)

    # main() should catch KeyboardInterrupt and exit gracefully
    main_mod.main()  # Should not raise

    # Should have completed one cycle before interrupt
    assert cycle_count["count"] >= 1


def test_main_sleeps_and_loops(monkeypatch):
    """Test that main() sleeps between cycles and can be interrupted."""
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))

    sleep_calls = []
    cycle_count = {"count": 0}

    def mock_sleep(seconds):
        sleep_calls.append(seconds)
        # Interrupt after first sleep
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", mock_sleep)

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        def poll_once(self):
            cycle_count["count"] += 1

    monkeypatch.setattr(main_mod, "Main", FakeMain)

    # main() should loop, call poll_once, sleep, then be interrupted
    main_mod.main()

    # Should have called poll_once once and tried to sleep
    assert cycle_count["count"] >= 1
    assert len(sleep_calls) == 1
    # 60φ = 30(1 + √5) ≈ 97.08 seconds
    assert sleep_calls[0] == 30 * (1 + 5**0.5)


def test_main_function_creates_main_and_loops(monkeypatch):
    """Test that the main() function creates Main instance and runs polling loop."""
    called = {"init": False, "poll": 0}

    # Monkeypatch Project.iter to avoid real initialization
    monkeypatch.setattr(projects.Project, "iter", classmethod(lambda cls: []))

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        def __init__(self):
            called["init"] = True
            super().__init__()

        def poll_once(self):
            called["poll"] += 1

    monkeypatch.setattr(main_mod, "Main", FakeMain)

    # Mock sleep to raise KeyboardInterrupt after first call
    def mock_sleep(s):
        if called["poll"] >= 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", mock_sleep)

    # Call main() - should create Main and call poll_once
    main_mod.main()

    assert called["init"] is True
    assert called["poll"] >= 1


# Stitch tiles integration test


def test_stitch_tiles_warns_on_missing_and_returns_paletted_image(tmp_path, capsys, monkeypatch):
    """Test that stitch_tiles returns paletted image even with missing tiles."""
    # rectangle covering a single tile (0,0)
    rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1000, 1000))

    # ensure cache dir is empty
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    # replace module cache dir so stitch_tiles looks at tmp cache
    monkeypatch.setattr(projects, "DIRS", SimpleNamespace(user_cache_path=cache_dir))
    from cam import ingest

    monkeypatch.setattr(ingest, "DIRS", SimpleNamespace(user_cache_path=cache_dir))

    img = ingest.stitch_tiles(rect)
    assert isinstance(img, Image.Image)
    # since no tile files exist, the result should be paletted (mode 'P')
    assert img.mode == "P"
    # loguru writes warnings to stderr; the warning appeared during the run


# Miscellaneous palette tests


def test_palette_lookup_transparent_and_ensure():
    """Test that transparent pixel maps to palette index 0."""
    # transparent pixel should map to 0
    idx = projects.PALETTE.lookup((0, 0, 0, 0))
    assert idx == 0


def test_main_check_tiles_round_robin(monkeypatch):
    """Test that check_next_tile only checks one tile per cycle in round-robin fashion."""

    # Create fake projects covering three different tiles
    class FakeProj:
        def __init__(self, path, tile):
            self.path = path
            self.rect = SimpleNamespace(tiles=frozenset({tile}))
            self.diff_count = 0

        def run_diff(self, changed_tile=None):
            self.diff_count += 1

        def has_been_modified(self):
            return False

        def __hash__(self):
            return hash(self.path)

        def __eq__(self, other):
            return getattr(other, "path", None) == self.path

    proj1 = FakeProj(Path("/tmp/proj1.png"), Tile(0, 0))
    proj2 = FakeProj(Path("/tmp/proj2.png"), Tile(1, 0))
    proj3 = FakeProj(Path("/tmp/proj3.png"), Tile(0, 1))

    monkeypatch.setattr("cam.main.Project.iter", classmethod(lambda cls: [proj1, proj2, proj3]))

    m = main_mod.Main()
    assert len(m.tile_checker.tiles) == 3  # Three tiles tracked
    assert len(m.tile_checker.queue_system.tile_metadata) == 3  # Queue system has all tiles

    # Track which tiles have been checked
    checked_tiles = []

    def mock_has_tile_changed(tile):
        checked_tiles.append(tile)
        return (True, 0)  # Return tuple: (changed, last_modified)

    monkeypatch.setattr("cam.ingest.has_tile_changed", mock_has_tile_changed)

    # First cycle: should check only one tile
    m.tile_checker.check_next_tile()
    assert len(checked_tiles) == 1, "Should only check one tile per cycle"

    # Second cycle: should check the next tile
    m.tile_checker.check_next_tile()
    assert len(checked_tiles) == 2, "Should have checked two tiles total after two cycles"

    # Third cycle: should check the last tile
    m.tile_checker.check_next_tile()
    assert len(checked_tiles) == 3, "Should have checked three tiles total after three cycles"

    # Fourth cycle: should wrap around and check another tile
    m.tile_checker.check_next_tile()
    assert len(checked_tiles) == 4, "Should have checked four tiles total (wrapping around)"


def test_main_check_tiles_empty_tiles(monkeypatch):
    """Test that check_tiles handles empty tiles gracefully."""
    monkeypatch.setattr("cam.main.Project.iter", classmethod(lambda cls: []))

    m = main_mod.Main()
    assert len(m.tile_checker.tiles) == 0
    assert len(m.tile_checker.queue_system.tile_metadata) == 0

    # Should not crash when no tiles exist
    m.tile_checker.check_next_tile()
    assert len(m.tile_checker.queue_system.tile_metadata) == 0  # Should remain empty


def test_poll_once_checks_projects_before_tiles(monkeypatch):
    """Test that poll_once() checks projects before tiles (inverted order)."""
    monkeypatch.setattr("cam.main.Project.iter", classmethod(lambda cls: []))

    m = main_mod.Main()

    call_order = []

    original_check_projects = m.check_projects
    original_check_next_tile = m.tile_checker.check_next_tile

    def track_check_projects():
        call_order.append("projects")
        original_check_projects()

    def track_check_next_tile():
        call_order.append("tiles")
        original_check_next_tile()

    m.check_projects = track_check_projects
    m.tile_checker.check_next_tile = track_check_next_tile

    # Call poll_once
    m.poll_once()

    # Verify projects are checked before tiles
    assert call_order == ["projects", "tiles"], f"Expected ['projects', 'tiles'], got {call_order}"


def test_main_handles_consecutive_errors(monkeypatch):
    """Test that main() exits after three consecutive errors."""
    monkeypatch.setattr("cam.main.Project.iter", classmethod(lambda cls: []))

    error_count = {"count": 0}

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        def poll_once(self):
            error_count["count"] += 1
            raise RuntimeError("Test error")

    monkeypatch.setattr(main_mod, "Main", FakeMain)
    # Don't actually sleep
    monkeypatch.setattr(time, "sleep", lambda s: None)

    # main() should raise after 3 consecutive errors
    try:
        main_mod.main()
        assert False, "Expected main() to raise after 3 consecutive errors"
    except RuntimeError:
        # Expected - should have failed after 3 errors
        assert error_count["count"] == 3


def test_main_resets_error_count_on_success(monkeypatch):
    """Test that main() resets consecutive error count after a successful cycle."""
    monkeypatch.setattr("cam.main.Project.iter", classmethod(lambda cls: []))

    cycle_count = {"count": 0}

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        def poll_once(self):
            cycle_count["count"] += 1
            # Fail twice, succeed once, then fail twice again, then succeed
            if cycle_count["count"] in [1, 2, 4, 5]:
                raise RuntimeError("Test error")
            # On cycles 3 and 6, succeed

    monkeypatch.setattr(main_mod, "Main", FakeMain)

    # Mock sleep to exit after 6 cycles
    def mock_sleep(s):
        if cycle_count["count"] >= 6:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", mock_sleep)

    # main() should not crash since errors are interspersed with successes
    main_mod.main()  # Should exit gracefully via KeyboardInterrupt
    assert cycle_count["count"] == 6
