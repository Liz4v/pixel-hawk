import asyncio

import pytest

from pixel_hawk import main as main_mod
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import Person, ProjectInfo


@pytest.fixture
async def test_person():
    """Create a test person for use in tests."""
    return await Person.create(name="TestPerson")


# Database-first loading tests


async def test_watched_tiles_count_updated(setup_config, test_person):
    """Test that Main.start() updates watched tiles count for persons."""
    # Create two overlapping active projects (only DB records needed)
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    await ProjectInfo.from_rect(rect1, test_person.id, "project1")

    rect2 = Rectangle.from_point_size(Point(500, 500), Size(1000, 1000))
    await ProjectInfo.from_rect(rect2, test_person.id, "project2")

    # Start Main (no project files needed - start() only updates person totals)
    m = main_mod.Main()
    await m.start()

    # Reload person from DB
    person = await Person.get(id=test_person.id)
    # watched_tiles_count should be updated (overlapping tiles counted once)
    assert person.watched_tiles_count > 0


# Poll cycle tests


async def test_poll_once_checks_tiles(setup_config):
    """Test that poll_once() checks tiles via TileChecker."""
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


async def test_main_handles_consecutive_errors(setup_config, monkeypatch):
    """Test that Main.main() exits after three consecutive errors."""
    error_count = {"count": 0}

    class FakeMain(main_mod.Main):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            error_count["count"] += 1
            raise RuntimeError("Test error")

    # Don't actually sleep
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: _real_sleep(0))

    # Main.main() should raise after 3 consecutive errors
    try:
        await FakeMain().main()
        assert False, "Expected Main.main() to raise after 3 consecutive errors"
    except RuntimeError:
        # Expected - should have failed after 3 errors
        assert error_count["count"] == 3


async def test_main_resets_error_count_on_success(setup_config, monkeypatch):
    """Test that Main.main() resets consecutive error count after a successful cycle."""
    cycle_count = {"count": 0}

    class FakeMain(main_mod.Main):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            cycle_count["count"] += 1
            # Fail twice, succeed once, then fail twice again, then succeed
            if cycle_count["count"] in [1, 2, 4, 5]:
                raise RuntimeError("Test error")
            # On cycles 3 and 6, succeed

    # Mock sleep to exit after 6 cycles
    async def mock_sleep(s):
        if cycle_count["count"] >= 6:
            raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    # Main.main() should not crash since errors are interspersed with successes
    await FakeMain().main()  # Should exit gracefully via KeyboardInterrupt
    assert cycle_count["count"] == 6


async def test_main_handles_keyboard_interrupt_during_sleep(setup_config, monkeypatch):
    """Test that Main.main() handles KeyboardInterrupt during sleep gracefully."""
    cycle_count = {"count": 0}

    async def mock_sleep(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    class FakeMain(main_mod.Main):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            cycle_count["count"] += 1

    # Main.main() should catch KeyboardInterrupt and exit gracefully
    await FakeMain().main()  # Should not raise

    # Should have completed one cycle before interrupt
    assert cycle_count["count"] >= 1


async def test_main_sleeps_and_loops(setup_config, monkeypatch):
    """Test that Main.main() sleeps between cycles and can be interrupted."""
    sleep_calls = []
    cycle_count = {"count": 0}

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    class FakeMain(main_mod.Main):
        async def start(self):
            pass  # Skip actual startup

        async def poll_once(self):
            cycle_count["count"] += 1

    # Main.main() should loop, call poll_once, sleep, then be interrupted
    await FakeMain().main()

    # Should have called poll_once once and tried to sleep
    assert cycle_count["count"] >= 1
    assert len(sleep_calls) == 1
    # 60φ = 30(1 + √5) ≈ 97.08 seconds
    assert sleep_calls[0] == 30 * (1 + 5**0.5)
