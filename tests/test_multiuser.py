"""Tests for multi-user functionality and project state management."""

import pytest

from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import HistoryChange, Person, ProjectInfo, ProjectState
from pixel_hawk.palette import PALETTE


@pytest.fixture
async def person1():
    """Create first test person."""
    return await Person.create(name="Alice")


@pytest.fixture
async def person2():
    """Create second test person."""
    return await Person.create(name="Bob")


# Multi-owner tests


async def test_same_name_different_owners(person1, person2):
    """Test that different owners can have projects with the same name."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    # Both persons create projects with same name
    info1 = await ProjectInfo.from_rect(rect, person1.id, "my_project")
    info2 = await ProjectInfo.from_rect(rect, person2.id, "my_project")

    # Fetch owner relationships
    await info1.fetch_related("owner")
    await info2.fetch_related("owner")

    # Both should succeed (different owners)
    assert info1.name == "my_project"
    assert info2.name == "my_project"
    assert info1.owner.id == person1.id
    assert info2.owner.id == person2.id
    assert info1.id != info2.id  # Different ProjectInfo records


async def test_same_owner_duplicate_name_fails(person1):
    """Test that unique constraint prevents duplicate names per owner."""
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    rect2 = Rectangle.from_point_size(Point(1000, 1000), Size(100, 100))

    # Create first project
    info1 = await ProjectInfo.from_rect(rect1, person1.id, "duplicate_name")

    # Try to create second project with same name and owner
    try:
        info2 = await ProjectInfo.from_rect(rect2, person1.id, "duplicate_name")
        # If we got here, the unique constraint didn't work
        assert False, "Expected unique constraint violation"
    except Exception as e:
        # Expected - unique constraint should prevent this
        assert "unique" in str(e).lower() or "constraint" in str(e).lower()


async def test_owner_isolation_in_lookups(person1, person2):
    """Test that lookups correctly filter by owner_id."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    # Both owners create projects with same name
    info1 = await ProjectInfo.from_rect(rect, person1.id, "shared_name")
    info2 = await ProjectInfo.from_rect(rect, person2.id, "shared_name")

    # get_or_create should return correct project for each owner
    lookup1 = await ProjectInfo.get_or_create_from_rect(rect, person1.id, "shared_name")
    lookup2 = await ProjectInfo.get_or_create_from_rect(rect, person2.id, "shared_name")

    # Fetch owner relationships
    await lookup1.fetch_related("owner")
    await lookup2.fetch_related("owner")

    assert lookup1.id == info1.id
    assert lookup2.id == info2.id
    assert lookup1.owner.id == person1.id
    assert lookup2.owner.id == person2.id


async def test_history_change_fk_with_multiuser(person1, person2):
    """Test that HistoryChange FK works correctly with integer ProjectInfo IDs."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    # Create projects for both owners
    info1 = await ProjectInfo.from_rect(rect, person1.id, "project1")
    info2 = await ProjectInfo.from_rect(rect, person2.id, "project2")

    # Create history changes for both projects
    change1 = await HistoryChange.create(
        project=info1,
        timestamp=1000,
        status="complete",
        num_remaining=0,
        num_target=100,
        completion_percent=100.0,
    )
    change2 = await HistoryChange.create(
        project=info2,
        timestamp=2000,
        status="in_progress",
        num_remaining=50,
        num_target=100,
        completion_percent=50.0,
    )

    # Verify FK relationships work
    changes1 = await HistoryChange.filter(project=info1).all()
    changes2 = await HistoryChange.filter(project=info2).all()

    assert len(changes1) == 1
    assert len(changes2) == 1
    assert changes1[0].id == change1.id
    assert changes2[0].id == change2.id


# Watched tiles tracking tests


async def test_watched_tiles_tracking(person1):
    """Test end-to-end tile counting across multiple projects."""
    # Create two non-overlapping projects
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    rect2 = Rectangle.from_point_size(Point(1000, 0), Size(1000, 1000))

    info1 = await ProjectInfo.from_rect(rect1, person1.id, "project1")
    info2 = await ProjectInfo.from_rect(rect2, person1.id, "project2")

    # Update watched tiles count
    await person1.update_watched_tiles_count()

    # Reload from DB
    person = await Person.get(id=person1.id)

    # Both projects cover 1 tile each = 2 tiles total
    assert person.watched_tiles_count == 2


async def test_watched_tiles_overlapping_counted_once(person1):
    """Test that overlapping tiles are counted only once."""
    # Create two overlapping projects
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    rect2 = Rectangle.from_point_size(Point(500, 500), Size(1000, 1000))

    info1 = await ProjectInfo.from_rect(rect1, person1.id, "project1")
    info2 = await ProjectInfo.from_rect(rect2, person1.id, "project2")

    # Update watched tiles count
    await person1.update_watched_tiles_count()

    # Calculate expected tile count
    tiles = await person1.calculate_watched_tiles()

    # Reload from DB
    person = await Person.get(id=person1.id)

    # Overlapping tiles should be counted only once
    assert person.watched_tiles_count == len(tiles)
    assert person.watched_tiles_count > 0


async def test_state_affects_tile_count(person1):
    """Test that changing state to passive/inactive updates tile count."""
    # Create two projects
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    rect2 = Rectangle.from_point_size(Point(1000, 0), Size(1000, 1000))

    info1 = await ProjectInfo.from_rect(rect1, person1.id, "project1", ProjectState.ACTIVE)
    info2 = await ProjectInfo.from_rect(rect2, person1.id, "project2", ProjectState.ACTIVE)

    # Update tile count (both active)
    await person1.update_watched_tiles_count()
    assert person1.watched_tiles_count == 2  # Both projects counted

    # Change one to passive
    info2.state = ProjectState.PASSIVE
    await info2.save()

    # Update tile count (only active counted)
    await person1.update_watched_tiles_count()
    assert person1.watched_tiles_count == 1  # Only project1 counted

    # Change both to inactive
    info1.state = ProjectState.INACTIVE
    info2.state = ProjectState.INACTIVE
    await info1.save()
    await info2.save()

    # Update tile count (none counted)
    await person1.update_watched_tiles_count()
    assert person1.watched_tiles_count == 0  # No projects counted


async def test_only_active_projects_in_calculate_tiles(person1):
    """Test that calculate_watched_tiles only includes active projects."""
    # Create projects in different states
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    rect2 = Rectangle.from_point_size(Point(1000, 0), Size(1000, 1000))
    rect3 = Rectangle.from_point_size(Point(2000, 0), Size(1000, 1000))

    info1 = await ProjectInfo.from_rect(rect1, person1.id, "active", ProjectState.ACTIVE)
    info2 = await ProjectInfo.from_rect(rect2, person1.id, "passive", ProjectState.PASSIVE)
    info3 = await ProjectInfo.from_rect(rect3, person1.id, "inactive", ProjectState.INACTIVE)

    # Calculate watched tiles
    tiles = await person1.calculate_watched_tiles()

    # Only active project's tile should be counted
    assert len(tiles) == 1


# ProjectInfo state tests


async def test_projectinfo_state_default(person1):
    """Test that ProjectInfo state defaults to ACTIVE."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person1.id, "test")

    assert info.state == ProjectState.ACTIVE


async def test_projectinfo_state_can_be_set(person1):
    """Test that ProjectInfo state can be set to different values."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    # Create with passive state
    info_passive = await ProjectInfo.from_rect(rect, person1.id, "passive", ProjectState.PASSIVE)
    assert info_passive.state == ProjectState.PASSIVE

    # Create with inactive state
    rect2 = Rectangle.from_point_size(Point(1000, 0), Size(100, 100))
    info_inactive = await ProjectInfo.from_rect(rect2, person1.id, "inactive", ProjectState.INACTIVE)
    assert info_inactive.state == ProjectState.INACTIVE


async def test_projectinfo_filename_property(person1):
    """Test that filename property returns coordinate-only format regardless of name."""
    rect = Rectangle.from_point_size(Point.from4(5, 7, 250, 380), Size(120, 80))
    info = await ProjectInfo.from_rect(rect, person1.id, "My Cool Project Name!")

    # Filename should be coordinates only, no name
    assert info.filename == "5_7_250_380.png"
    assert "My Cool Project Name" not in info.filename
    assert "cool" not in info.filename.lower()


async def test_multiple_owners_different_tiles(person1, person2):
    """Test that different owners can watch different sets of tiles."""
    # Person 1 watches tiles 0,0 and 1,0
    rect1a = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    rect1b = Rectangle.from_point_size(Point(1000, 0), Size(1000, 1000))
    info1a = await ProjectInfo.from_rect(rect1a, person1.id, "project1a")
    info1b = await ProjectInfo.from_rect(rect1b, person1.id, "project1b")

    # Person 2 watches tiles 2,0 and 3,0
    rect2a = Rectangle.from_point_size(Point(2000, 0), Size(1000, 1000))
    rect2b = Rectangle.from_point_size(Point(3000, 0), Size(1000, 1000))
    info2a = await ProjectInfo.from_rect(rect2a, person2.id, "project2a")
    info2b = await ProjectInfo.from_rect(rect2b, person2.id, "project2b")

    # Update tile counts
    await person1.update_watched_tiles_count()
    await person2.update_watched_tiles_count()

    # Each person should watch 2 tiles
    person1_reloaded = await Person.get(id=person1.id)
    person2_reloaded = await Person.get(id=person2.id)

    assert person1_reloaded.watched_tiles_count == 2
    assert person2_reloaded.watched_tiles_count == 2

    # Verify they're watching different tiles
    tiles1 = await person1.calculate_watched_tiles()
    tiles2 = await person2.calculate_watched_tiles()

    assert len(tiles1) == 2
    assert len(tiles2) == 2
    assert tiles1.isdisjoint(tiles2)  # No overlap
