import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixel_hawk import main as main_mod
from pixel_hawk.geometry import Point, Rectangle, Size, Tile
from pixel_hawk.models import Person, ProjectInfo, ProjectState
from pixel_hawk.palette import PALETTE


@pytest.fixture
async def test_person():
    """Create a test person for use in tests."""
    return await Person.create(name="TestPerson")


# Database-first loading tests


async def test_start_loads_from_database(setup_config, test_person, monkeypatch):
    """Test that Main.start() loads projects from database, not filesystem."""
    # Create a ProjectInfo record in database
    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    info = await ProjectInfo.from_rect(rect, test_person.id, "test_project", ProjectState.ACTIVE)

    # Create the actual project file
    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)
    path = person_dir / info.filename
    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    from pixel_hawk import projects

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    # Start Main - should load from database
    m = main_mod.Main()
    await m.start()

    # Verify project was loaded from database
    assert len(m.projects) == 1
    assert info.id in m.projects
    assert m.projects[info.id].info.name == "test_project"


async def test_active_passive_loaded(setup_config, test_person, monkeypatch):
    """Test that active and passive projects are both loaded."""
    # Create one active and one passive project
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    info1 = await ProjectInfo.from_rect(rect1, test_person.id, "active_project", ProjectState.ACTIVE)

    rect2 = Rectangle.from_point_size(Point(1000, 1000), Size(10, 10))
    info2 = await ProjectInfo.from_rect(rect2, test_person.id, "passive_project", ProjectState.PASSIVE)

    # Create the actual project files
    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)

    for info in [info1, info2]:
        path = person_dir / info.filename
        im = PALETTE.new((10, 10))
        im.putdata([1] * 100)
        im.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    from pixel_hawk import projects

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    # Start Main
    m = main_mod.Main()
    await m.start()

    # Both projects should be loaded
    assert len(m.projects) == 2
    assert info1.id in m.projects
    assert info2.id in m.projects

    # Only active project should be in TileChecker
    active_projects = [p for p in m.projects.values() if p.info.state == ProjectState.ACTIVE]
    assert len(active_projects) == 1


async def test_inactive_not_loaded(setup_config, test_person, monkeypatch):
    """Test that inactive projects are not loaded."""
    # Create one active and one inactive project
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    info1 = await ProjectInfo.from_rect(rect1, test_person.id, "active_project", ProjectState.ACTIVE)

    rect2 = Rectangle.from_point_size(Point(1000, 1000), Size(10, 10))
    info2 = await ProjectInfo.from_rect(rect2, test_person.id, "inactive_project", ProjectState.INACTIVE)

    # Create the actual project files
    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)

    for info in [info1, info2]:
        path = person_dir / info.filename
        im = PALETTE.new((10, 10))
        im.putdata([1] * 100)
        im.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    from pixel_hawk import projects

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    # Start Main
    m = main_mod.Main()
    await m.start()

    # Only active project should be loaded (inactive excluded)
    assert len(m.projects) == 1
    assert info1.id in m.projects
    assert info2.id not in m.projects


async def test_projects_dict_keyed_by_id(setup_config, test_person, monkeypatch):
    """Test that self.projects uses ProjectInfo.id as key."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    info = await ProjectInfo.from_rect(rect, test_person.id, "test_project")

    # Create the actual project file
    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)
    path = person_dir / info.filename
    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    from pixel_hawk import projects

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    # Start Main
    m = main_mod.Main()
    await m.start()

    # Verify projects dict is keyed by integer ID, not path
    assert isinstance(list(m.projects.keys())[0], int)
    assert info.id in m.projects


async def test_load_skips_missing_file(setup_config, test_person, monkeypatch):
    """Test that Main.start() skips projects with missing files."""
    # Create ProjectInfo without creating the file
    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    info = await ProjectInfo.from_rect(rect, test_person.id, "missing_project")

    # Don't create the file - it should be missing

    # Start Main
    m = main_mod.Main()
    await m.start()

    # Project should not be loaded
    assert len(m.projects) == 0


async def test_watched_tiles_count_updated(setup_config, test_person, monkeypatch):
    """Test that Main.start() updates watched tiles count for persons."""
    # Create two overlapping projects
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    info1 = await ProjectInfo.from_rect(rect1, test_person.id, "project1")

    rect2 = Rectangle.from_point_size(Point(500, 500), Size(1000, 1000))
    info2 = await ProjectInfo.from_rect(rect2, test_person.id, "project2")

    # Create the actual project files
    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)

    for info in [info1, info2]:
        path = person_dir / info.filename
        im = PALETTE.new((10, 10))
        im.putdata([1] * 100)
        im.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    from pixel_hawk import projects

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    # Start Main
    m = main_mod.Main()
    await m.start()

    # Reload person from DB
    person = await Person.get(id=test_person.id)
    # watched_tiles_count should be updated (overlapping tiles counted once)
    assert person.watched_tiles_count > 0


# Poll cycle tests


async def test_poll_once_checks_tiles(setup_config, test_person, monkeypatch):
    """Test that poll_once() checks tiles via TileChecker."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    info = await ProjectInfo.from_rect(rect, test_person.id, "test_project")

    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)
    path = person_dir / info.filename
    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    from pixel_hawk import projects

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    m = main_mod.Main()
    await m.start()

    # Track if check_next_tile was called
    called = {"count": 0}
    original_check = m.tile_checker.check_next_tile

    async def track_check():
        called["count"] += 1
        await original_check()

    m.tile_checker.check_next_tile = track_check

    # Call poll_once
    await m.poll_once()

    # check_next_tile should have been called
    assert called["count"] == 1


# Main loop error handling tests


async def test_main_handles_consecutive_errors(monkeypatch):
    """Test that _async_main() exits after three consecutive errors."""
    error_count = {"count": 0}

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            error_count["count"] += 1
            raise RuntimeError("Test error")

    monkeypatch.setattr(main_mod, "Main", FakeMain)
    # Don't actually sleep
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: _real_sleep(0))

    # _async_main() should raise after 3 consecutive errors
    try:
        await main_mod._async_main()
        assert False, "Expected _async_main() to raise after 3 consecutive errors"
    except RuntimeError:
        # Expected - should have failed after 3 errors
        assert error_count["count"] == 3


async def test_main_resets_error_count_on_success(monkeypatch):
    """Test that _async_main() resets consecutive error count after a successful cycle."""
    cycle_count = {"count": 0}

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            cycle_count["count"] += 1
            # Fail twice, succeed once, then fail twice again, then succeed
            if cycle_count["count"] in [1, 2, 4, 5]:
                raise RuntimeError("Test error")
            # On cycles 3 and 6, succeed

    monkeypatch.setattr(main_mod, "Main", FakeMain)

    # Mock sleep to exit after 6 cycles
    async def mock_sleep(s):
        if cycle_count["count"] >= 6:
            raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    # _async_main() should not crash since errors are interspersed with successes
    await main_mod._async_main()  # Should exit gracefully via KeyboardInterrupt
    assert cycle_count["count"] == 6


async def test_main_handles_keyboard_interrupt_during_sleep(monkeypatch):
    """Test that _async_main() handles KeyboardInterrupt during sleep gracefully."""
    cycle_count = {"count": 0}

    async def mock_sleep(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            cycle_count["count"] += 1

    monkeypatch.setattr(main_mod, "Main", FakeMain)

    # _async_main() should catch KeyboardInterrupt and exit gracefully
    await main_mod._async_main()  # Should not raise

    # Should have completed one cycle before interrupt
    assert cycle_count["count"] >= 1


async def test_main_sleeps_and_loops(monkeypatch):
    """Test that _async_main() sleeps between cycles and can be interrupted."""
    sleep_calls = []
    cycle_count = {"count": 0}

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    original_main_class = main_mod.Main

    class FakeMain(original_main_class):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            cycle_count["count"] += 1

    monkeypatch.setattr(main_mod, "Main", FakeMain)

    # _async_main() should loop, call poll_once, sleep, then be interrupted
    await main_mod._async_main()

    # Should have called poll_once once and tried to sleep
    assert cycle_count["count"] >= 1
    assert len(sleep_calls) == 1
    # 60φ = 30(1 + √5) ≈ 97.08 seconds
    assert sleep_calls[0] == 30 * (1 + 5**0.5)
