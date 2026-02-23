"""Tests for TileInfo.adjust_project_heat and reverse relation annotations."""

from pixel_hawk.models.entities import Person, ProjectInfo, ProjectState, TileInfo, TileProject
from pixel_hawk.models.geometry import Point, Rectangle, Size


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


# --- Has linked projects ---


async def test_has_projects_heat_zero_promotes_to_burning():
    """Tile at heat 0 with linked projects should be promoted to 999."""
    tile = await _create_tile(0, 0, heat=0)
    await _link_project(tile)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 999


async def test_has_projects_heat_nonzero_unchanged():
    """Tile at a non-zero heat with linked projects should stay unchanged."""
    tile = await _create_tile(0, 0, heat=5)
    await _link_project(tile)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 5


async def test_has_projects_heat_burning_unchanged():
    """Tile already burning with linked projects should stay at 999."""
    tile = await _create_tile(0, 0, heat=999)
    await _link_project(tile)
    await tile.adjust_project_heat()
    await tile.refresh_from_db()
    assert tile.heat == 999


# --- Reverse relation: Person.projects ---


async def test_person_projects_empty():
    """Person with no projects has empty reverse relation."""
    person = await Person.create(name="lonely")
    projects = await person.projects.all()
    assert projects == []


async def test_person_projects_returns_owned():
    """Person.projects returns only that person's projects."""
    alice = await Person.create(name="Alice")
    bob = await Person.create(name="Bob")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    alice_proj = await ProjectInfo.from_rect(rect, alice.id, "alice_proj")
    bob_proj = await ProjectInfo.from_rect(rect, bob.id, "bob_proj")

    alice_projects = await alice.projects.all()
    assert len(alice_projects) == 1
    assert alice_projects[0].id == alice_proj.id

    bob_projects = await bob.projects.all()
    assert len(bob_projects) == 1
    assert bob_projects[0].id == bob_proj.id


async def test_person_projects_filter_by_state():
    """Person.projects supports filtering by state."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))

    active = await ProjectInfo.from_rect(rect, person.id, "active")
    passive = await ProjectInfo.from_rect(rect, person.id, "passive")
    passive.state = ProjectState.PASSIVE
    await passive.save()

    active_only = await person.projects.filter(state=ProjectState.ACTIVE).all()
    assert len(active_only) == 1
    assert active_only[0].id == active.id


# --- Reverse relation: TileInfo.tile_projects ---


async def test_tile_projects_empty():
    """Tile with no linked projects has empty reverse relation."""
    tile = await _create_tile(5, 5)
    links = await tile.tile_projects.all()
    assert links == []


async def test_tile_projects_returns_links():
    """TileInfo.tile_projects returns junction records for that tile."""
    tile = await _create_tile(1, 1)
    info = await _link_project(tile)

    links = await tile.tile_projects.all()
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

    links = await tile.tile_projects.all()
    assert len(links) == 2
    linked_ids = {link.project_id for link in links}
    assert linked_ids == {info1.id, info2.id}


# --- Reverse relation: ProjectInfo.project_tiles ---


async def test_project_tiles_empty():
    """Project with no linked tiles has empty reverse relation."""
    person = await Person.create(name="tester")
    rect = Rectangle.from_point_size(Point(0, 0), Size(100, 100))
    info = await ProjectInfo.from_rect(rect, person.id, "proj")

    links = await info.project_tiles.all()
    assert links == []


async def test_project_tiles_returns_links():
    """ProjectInfo.project_tiles returns junction records for that project."""
    tile = await _create_tile(3, 3)
    info = await _link_project(tile)

    links = await info.project_tiles.all()
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

    links = await info.project_tiles.all()
    assert len(links) == 2
    linked_tile_ids = {link.tile_id for link in links}
    assert linked_tile_ids == {tile_a.id, tile_b.id}
