"""Tests for TileInfo.adjust_project_heat, tile linking, and query helpers."""

from pixel_hawk.models.geometry import Point, Rectangle, Size
from pixel_hawk.models.person import Person
from pixel_hawk.models.project import ProjectInfo, ProjectState
from pixel_hawk.models.tile import TileInfo, TileProject


async def _create_tile(x: int, y: int, *, heat: int = 999) -> TileInfo:
    return await TileInfo.create(id=TileInfo.tile_id(x, y), x=x, y=y, heat=heat, last_checked=0, last_update=0)


async def _link_project(tile: TileInfo) -> ProjectInfo:
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")
    await TileProject.create(tile=tile, project=info)
    return info


# --- No linked projects ---


async def test_no_projects_sets_heat_to_zero():
    """Tile with no linked projects should have heat set to 0."""
    tile = await _create_tile(0, 0, heat=999)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 0


async def test_no_projects_already_zero_is_noop():
    """Tile already at heat 0 with no projects should stay unchanged."""
    tile = await _create_tile(0, 0, heat=0)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 0


async def test_no_projects_from_temperature_sets_zero():
    """Tile at a temperature heat with no projects should be set to 0."""
    tile = await _create_tile(0, 0, heat=5)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 0


# --- Has linked ACTIVE projects ---


async def test_has_active_projects_heat_zero_promotes_to_burning():
    """Tile at heat 0 with an ACTIVE project should be promoted to 999."""
    tile = await _create_tile(0, 0, heat=0)
    await _link_project(tile)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 999


async def test_has_active_projects_heat_nonzero_unchanged():
    """Tile at a non-zero heat with an ACTIVE project should stay unchanged."""
    tile = await _create_tile(0, 0, heat=5)
    await _link_project(tile)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 5


async def test_has_active_projects_heat_burning_unchanged():
    """Tile already burning with an ACTIVE project should stay at 999."""
    tile = await _create_tile(0, 0, heat=999)
    await _link_project(tile)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 999


# --- Has linked projects, but none ACTIVE ---


async def test_only_passive_project_sets_heat_zero():
    """Tile linked only to a PASSIVE project should have heat set to 0."""
    tile = await _create_tile(0, 0, heat=999)
    info = await _link_project(tile)
    info.state = ProjectState.PASSIVE
    await info.save()
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 0


async def test_only_inactive_project_sets_heat_zero():
    """Tile linked only to an INACTIVE project should have heat set to 0."""
    tile = await _create_tile(0, 0, heat=5)
    info = await _link_project(tile)
    info.state = ProjectState.INACTIVE
    await info.save()
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 0


async def test_mixed_active_and_passive_keeps_heat():
    """Tile with one ACTIVE and one PASSIVE project should stay hot."""
    tile = await _create_tile(0, 0, heat=5)
    await _link_project(tile)  # ACTIVE
    person2 = await Person.create(name="other")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    passive = await ProjectInfo.from_rect(rect, person2.id, "passive")
    passive.state = ProjectState.PASSIVE
    await passive.save()
    await TileProject.create(tile=tile, project=passive)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 5


# --- Person query helpers ---


async def test_person_filter_empty():
    """Person with no projects found via filter returns empty list for projects."""
    person = await Person.create(name="lonely")
    projects = await ProjectInfo.filter_by_owner(person.id)
    assert projects == []


async def test_person_projects_returns_owned():
    """filter_by_owner returns only that person's projects."""
    alice = await Person.create(name="Alice")
    bob = await Person.create(name="Bob")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    alice_proj = await ProjectInfo.from_rect(rect, alice.id, "alice_proj")
    bob_proj = await ProjectInfo.from_rect(rect, bob.id, "bob_proj")

    alice_projects = await ProjectInfo.filter_by_owner(alice.id)
    assert len(alice_projects) == 1
    assert alice_projects[0].id == alice_proj.id

    bob_projects = await ProjectInfo.filter_by_owner(bob.id)
    assert len(bob_projects) == 1
    assert bob_projects[0].id == bob_proj.id


async def test_person_projects_filter_by_state():
    """filter_by_owner supports filtering by state."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    active = await ProjectInfo.from_rect(rect, person.id, "active")
    passive = await ProjectInfo.from_rect(rect, person.id, "passive")
    passive.state = ProjectState.PASSIVE
    await passive.save()

    active_only = await ProjectInfo.filter_by_owner(person.id, state=ProjectState.ACTIVE)
    assert len(active_only) == 1
    assert active_only[0].id == active.id


# --- TileProject queries ---


async def test_tile_projects_empty():
    """Tile with no linked projects has empty query results."""
    tile = await _create_tile(5, 5)
    links = await TileProject.filter_by_tile(tile.id)
    assert links == []


async def test_tile_projects_returns_links():
    """TileProject.filter_by_tile returns junction records for that tile."""
    tile = await _create_tile(1, 1)
    info = await _link_project(tile)

    links = await TileProject.filter_by_tile(tile.id)
    assert len(links) == 1
    assert links[0].project_id == info.id
    assert links[0].tile_id == tile.id


async def test_tile_projects_multiple_projects():
    """Multiple projects linked to the same tile are all returned."""
    tile = await _create_tile(2, 2)
    person1 = await Person.create(name="p1")
    person2 = await Person.create(name="p2")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    info1 = await ProjectInfo.from_rect(rect, person1.id, "proj1")
    info2 = await ProjectInfo.from_rect(rect, person2.id, "proj2")
    await TileProject.create(tile=tile, project=info1)
    await TileProject.create(tile=tile, project=info2)

    links = await TileProject.filter_by_tile(tile.id)
    assert len(links) == 2
    linked_ids = {link.project_id for link in links}
    assert linked_ids == {info1.id, info2.id}


# --- ProjectInfo.project_tiles ---


async def test_project_tiles_empty():
    """Project with no linked tiles has empty query results."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")

    links = await TileProject.filter_by_project(info.id)
    assert links == []


async def test_project_tiles_returns_links():
    """TileProject.filter_by_project returns junction records for that project."""
    tile = await _create_tile(3, 3)
    info = await _link_project(tile)

    links = await TileProject.filter_by_project(info.id)
    assert len(links) == 1
    assert links[0].tile_id == tile.id
    assert links[0].project_id == info.id


async def test_project_tiles_multiple_tiles():
    """Multiple tiles linked to the same project are all returned."""
    tile_a = await _create_tile(4, 0)
    tile_b = await _create_tile(4, 1)
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")

    await TileProject.create(tile=tile_a, project=info)
    await TileProject.create(tile=tile_b, project=info)

    links = await TileProject.filter_by_project(info.id)
    assert len(links) == 2
    linked_tile_ids = {link.tile_id for link in links}
    assert linked_tile_ids == {tile_a.id, tile_b.id}


# --- reset_tracking ---


async def test_reset_tracking_clears_percentages():
    """reset_tracking clears percentage-based fields but preserves lifetime totals."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")

    info.last_check = 1000
    info.last_snapshot = 1000
    info.max_completion_pixels = 500
    info.max_completion_percent = 50.0
    info.max_completion_time = 1000
    info.largest_regress_pixels = 20
    info.largest_regress_time = 900
    info.recent_rate_pixels_per_hour = 10.0
    info.recent_rate_window_start = 800
    info.total_progress = 300
    info.total_regress = 30
    info.has_missing_tiles = False
    info.last_log_message = "some message"

    info.reset_tracking()

    assert info.last_check == 0
    assert info.last_snapshot == 0
    assert info.max_completion_pixels == 0
    assert info.max_completion_percent == 0.0
    assert info.max_completion_time == 0
    assert info.largest_regress_pixels == 0
    assert info.largest_regress_time == 0
    assert info.recent_rate_pixels_per_hour == 0.0
    assert info.recent_rate_window_start == 0
    assert info.has_missing_tiles is True
    assert info.last_log_message == ""
    # Lifetime totals preserved
    assert info.total_progress == 300
    assert info.total_regress == 30


async def test_unlink_tiles_adjusts_heat():
    """unlink_tiles should set heat to 0 on tiles with no remaining projects."""
    tile = await _create_tile(6, 6, heat=999)
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(6000, 6000), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")
    await TileProject.create(tile=tile, project=info)

    await info.unlink_tiles()
    await tile.refresh_from_db()
    assert tile.heat == 0


async def test_link_tiles_promotes_existing_heat_zero_tile():
    """link_tiles for an ACTIVE project should promote an existing tile from heat 0 to 999."""
    tile = await _create_tile(7, 7, heat=0)
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(7000, 7000), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")

    await info.link_tiles()
    await tile.refresh_from_db()
    assert tile.heat == 999


async def test_link_tiles_preserves_nonzero_heat():
    """link_tiles should not change the heat of an existing tile that already has a non-zero heat."""
    tile = await _create_tile(8, 8, heat=5)
    person = await Person.create(name="tester")
    # Link a first project manually so heat=5 is valid
    rect = Rectangle.from_point_size(Point(8000, 8000), Size(100, 100))
    first = await ProjectInfo.from_rect(rect, person.id, "first")
    await TileProject.create(tile=tile, project=first)

    # Link a second project via link_tiles
    second = await ProjectInfo.from_rect(rect, person.id, "second")
    await second.link_tiles()
    await tile.refresh_from_db()
    assert tile.heat == 5


async def test_link_tiles_passive_does_not_promote():
    """link_tiles for a PASSIVE project should not promote tiles from heat 0."""
    tile = await _create_tile(9, 9, heat=0)
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(9000, 9000), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")
    info.state = ProjectState.PASSIVE
    await info.save()

    await info.link_tiles()
    await tile.refresh_from_db()
    assert tile.heat == 0


async def test_link_tiles_creates_new_tile_at_zero_for_passive():
    """link_tiles for a PASSIVE project should create new tiles at heat 0."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(10000, 10000), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")
    info.state = ProjectState.PASSIVE
    await info.save()

    await info.link_tiles()
    tile_id = TileInfo.tile_id(10, 10)
    tile = await TileInfo.get_by_id(tile_id)
    assert tile is not None
    assert tile.heat == 0


# --- adjust_linked_tiles_heat (state transitions) ---


async def test_adjust_linked_tiles_heat_active_to_inactive():
    """Deactivating the sole ACTIVE project on a tile should set heat to 0."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(11000, 11000), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")
    await info.link_tiles()

    tile_id = TileInfo.tile_id(11, 11)
    tile = await TileInfo.get_by_id(tile_id)
    assert tile is not None
    assert tile.heat == 999

    info.state = ProjectState.INACTIVE
    await info.save()
    await info.adjust_linked_tiles_heat()

    await tile.refresh_from_db()
    assert tile.heat == 0


async def test_adjust_linked_tiles_heat_inactive_to_active():
    """Reactivating a project should promote its tiles from heat 0."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(12000, 12000), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")
    await info.link_tiles()

    # Deactivate
    info.state = ProjectState.INACTIVE
    await info.save()
    await info.adjust_linked_tiles_heat()

    tile_id = TileInfo.tile_id(12, 12)
    tile = await TileInfo.get_by_id(tile_id)
    assert tile is not None
    assert tile.heat == 0

    # Reactivate
    info.state = ProjectState.ACTIVE
    await info.save()
    await info.adjust_linked_tiles_heat()

    await tile.refresh_from_db()
    assert tile.heat == 999
