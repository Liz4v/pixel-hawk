import io


import pytest
from PIL import Image

from pixel_hawk import projects
from pixel_hawk.config import get_config
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import HistoryChange, Person, ProjectInfo
from pixel_hawk.palette import PALETTE, AsyncImage


@pytest.fixture
async def test_person():
    """Create a test person for use in tests."""
    return await Person.create(name="TestPerson")


def _paletted_image(size=(4, 4), value=1):
    """Helper to create a paletted image for testing."""
    im = PALETTE.new(size)
    im.putdata([value] * (size[0] * size[1]))
    return im


class FakeAsyncImage:
    """Mock AsyncImage for tests that need to patch aopen_file."""

    def __init__(self, image):
        self._image = image

    async def __aenter__(self):
        return self._image

    async def __aexit__(self, *_):
        pass

    async def __call__(self):
        return self._image


async def _make_project(rect, owner_id, *, name="test", touch=False):
    """Helper to create a Project with a DB-backed ProjectInfo.

    Creates the project file at the canonical path. If touch=True, creates an empty file
    instead of a valid paletted image.
    """
    info = await ProjectInfo.get_or_create_from_rect(rect, owner_id, name)
    await info.fetch_related("owner")
    path = get_config().projects_dir / str(info.owner.id) / info.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if touch:
        path.touch()
    else:
        im = _paletted_image(rect.size, value=1)
        im.save(path)
    return projects.Project(info)


# Database-first loading tests


async def test_from_info_valid_project(tmp_path, setup_config, test_person, monkeypatch):
    """Test Project.from_info successfully loads a valid project."""
    # Create project directory for test person
    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)

    # Create a valid project file
    rect = Rectangle.from_point_size(Point(1000, 1000), Size(10, 10))
    info = await ProjectInfo.from_rect(rect, test_person.id, "test_project")

    # Create the actual image file
    path = person_dir / info.filename
    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    async def noop_run_diff(self):
        pass

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    # Fetch owner relationship before loading project
    await info.fetch_related("owner")

    # Load project from info
    proj = await projects.Project.from_info(info)
    assert proj is not None
    assert isinstance(proj, projects.Project)
    assert proj.rect == rect


async def test_from_info_missing_file(tmp_path, setup_config, test_person):
    """Test Project.from_info returns None when file is missing."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    info = await ProjectInfo.from_rect(rect, test_person.id, "missing_project")

    # Fetch owner relationship
    await info.fetch_related("owner")

    # Don't create the file - it should be missing
    proj = await projects.Project.from_info(info)
    assert proj is None


async def test_from_info_invalid_palette(tmp_path, setup_config, test_person):
    """Test Project.from_info returns None when file has invalid palette."""
    # Create project directory for test person
    person_dir = setup_config.projects_dir / str(test_person.id)
    person_dir.mkdir(parents=True, exist_ok=True)

    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    info = await ProjectInfo.from_rect(rect, test_person.id, "invalid_palette")

    # Fetch owner relationship
    await info.fetch_related("owner")

    # Create file with wrong colors
    path = person_dir / info.filename
    im = Image.new("RGBA", (10, 10), (250, 251, 252, 255))
    im.save(path)

    proj = await projects.Project.from_info(info)
    assert proj is None


async def test_projectinfo_filename_property(test_person):
    """Test ProjectInfo.filename property returns coordinate-only format."""
    rect = Rectangle.from_point_size(Point.from4(5, 7, 250, 380), Size(120, 80))
    info = await ProjectInfo.from_rect(rect, test_person.id, "my_project")

    # Filename should be coordinates only, no name prefix
    assert info.filename == "5_7_250_380.png"
    assert "my_project" not in info.filename


# Project.run_diff tests


async def test_run_diff_branches(monkeypatch, test_person):
    """Test run_diff with various scenarios (no change, changes)."""
    rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1, 1))
    proj = await _make_project(rect, test_person.id, touch=True)

    class CM:
        def __init__(self, data):
            self.data = data
            self.size = (1, 1)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_flattened_data(self):
            return self.data

        def save(self, path):
            pass

        def close(self):
            pass

    # Case 1: no change (current == target)
    target = bytes([1, 2, 3])
    monkeypatch.setattr(PALETTE, "aopen_file", lambda path: FakeAsyncImage(CM(target)))

    async def fake_stitch(rect):
        return CM(target)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)
    await proj.run_diff()

    # Case 2: progress branch (different data)
    monkeypatch.setattr(PALETTE, "aopen_file", lambda path: FakeAsyncImage(CM(bytes([0, 1, 2]))))

    async def fake_stitch2(rect):
        return CM(bytes([2, 3, 4]))

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch2)
    await proj.run_diff()


async def test_run_diff_complete_and_remaining(monkeypatch, test_person):
    """Test run_diff complete and progress calculation paths."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    p = await _make_project(rect, test_person.id, touch=True)

    target = _paletted_image((4, 4), value=1)

    # Case: current equals target -> complete branch
    monkeypatch.setattr(PALETTE, "aopen_file", lambda path: FakeAsyncImage(target))

    async def fake_stitch_complete(rect):
        return _paletted_image((4, 4), value=1)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch_complete)
    await p.run_diff()

    # Case: current different -> remaining/progress calculation path
    monkeypatch.setattr(PALETTE, "aopen_file", lambda path: FakeAsyncImage(target))

    async def fake_stitch_partial(rect):
        return _paletted_image((4, 4), value=0)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch_partial)
    await p.run_diff()


# Project.has_been_modified tests


async def test_project_has_been_modified(test_person):
    """Test Project.has_been_modified detects file changes."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(rect, test_person.id)

    assert not proj.has_been_modified()

    real_mtime = round(proj.path.stat().st_mtime)
    proj.mtime = real_mtime - 1

    assert proj.has_been_modified()


async def test_project_has_been_modified_with_oserror(test_person):
    """Test Project.has_been_modified handles OSError."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(rect, test_person.id)

    proj.path.unlink()
    assert proj.has_been_modified()


async def test_project_has_been_modified_with_none_mtime(test_person):
    """Test Project.has_been_modified when mtime is 0."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(rect, test_person.id)
    proj.mtime = 0

    assert proj.has_been_modified()


async def test_project_equality_and_hash(test_person):
    """Test Project __eq__ and __hash__ methods."""
    rect1 = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    rect2 = Rectangle.from_point_size(Point(2000, 0), Size(2, 2))

    proj1 = await _make_project(rect1, test_person.id, name="a")
    proj2 = await _make_project(rect1, test_person.id, name="a")
    proj3 = await _make_project(rect2, test_person.id, name="b")

    assert proj1 == proj2
    assert hash(proj1) == hash(proj2)
    assert proj1 != proj3
    assert hash(proj1) != hash(proj3)
    assert proj1 != "not a project"


async def test_project_deletion(test_person):
    """Test Project deletion does not raise."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(rect, test_person.id)
    del proj


# ProjectInfo DB persistence tests


async def test_project_info_save_and_load(test_person):
    """Test ProjectInfo persistence via DB."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(rect, test_person.id)

    proj.info.max_completion_pixels = 42
    proj.info.total_progress = 100
    await proj.info.save()

    # Load fresh from DB
    loaded = await ProjectInfo.get(owner=test_person, name=proj.info.name)
    assert loaded.max_completion_pixels == 42
    assert loaded.total_progress == 100


async def test_project_snapshot_save_and_load(test_person):
    """Test snapshot persistence."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(rect, test_person.id)

    snapshot = _paletted_image((4, 4), value=2)
    await proj.save_snapshot(snapshot)
    assert proj.snapshot_path.exists()
    assert proj.info.last_snapshot > 0

    async with proj.load_snapshot_if_exists() as loaded:
        assert loaded is not None
        data = loaded.get_flattened_data()
        assert all(v == 2 for v in data)


async def test_project_snapshot_load_nonexistent(test_person):
    """Test loading snapshot when it doesn't exist."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(rect, test_person.id)

    async with proj.load_snapshot_if_exists() as snapshot:
        assert snapshot is None


async def test_run_diff_with_info_tracking(monkeypatch, test_person):
    """Test that run_diff updates info correctly."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(rect, test_person.id, touch=True)

    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)
    target.putpixel((1, 1), 2)
    target.putpixel((2, 2), 3)

    current = _paletted_image((4, 4), value=0)
    current.putpixel((0, 0), 1)

    monkeypatch.setattr(PALETTE, "aopen_file", lambda path_arg: FakeAsyncImage(target))

    async def fake_stitch(rect_arg):
        return current

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()

    assert proj.info.last_check > 0
    assert proj.info.max_completion_pixels > 0
    assert proj.info.max_completion_percent > 0
    assert proj.snapshot_path.exists()


async def test_run_diff_creates_history_change(monkeypatch, test_person):
    """Test that run_diff creates a HistoryChange record when progress is detected."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(rect, test_person.id, touch=True)

    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)
    target.putpixel((1, 1), 2)

    # First run: partial match, establishes snapshot (no progress yet since no prev)
    current1 = _paletted_image((4, 4), value=0)
    current1.putpixel((0, 0), 1)

    original_open_file = PALETTE.open_file

    def aopen_file_mock(path_arg):
        if ".snapshot." in str(path_arg):
            return AsyncImage(original_open_file, path_arg)
        return FakeAsyncImage(target)

    monkeypatch.setattr(PALETTE, "aopen_file", aopen_file_mock)

    stitch_results = iter([current1])

    async def fake_stitch(rect_arg):
        return next(stitch_results)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()

    # Second run: progress detected (pixel (1,1) now matches target)
    current2 = _paletted_image((4, 4), value=0)
    current2.putpixel((0, 0), 1)
    current2.putpixel((1, 1), 2)

    stitch_results = iter([current2])
    await proj.run_diff()

    # Should have created a HistoryChange record (progress detected)
    changes = await HistoryChange.filter(project=proj.info).all()
    assert len(changes) >= 1
    assert changes[0].num_target > 0


async def test_run_diff_skips_history_change_without_progress_or_regress(monkeypatch, test_person):
    """Test that HistoryChange is NOT saved when there are no progress or regress pixels."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(rect, test_person.id, touch=True)

    # Target has some non-transparent pixels; current partially matches (in-progress)
    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)
    target.putpixel((1, 1), 2)

    current = _paletted_image((4, 4), value=0)
    current.putpixel((0, 0), 1)  # one pixel correct

    original_open_file = PALETTE.open_file

    def aopen_file_mock(path_arg):
        if ".snapshot." in str(path_arg):
            return AsyncImage(original_open_file, path_arg)
        return FakeAsyncImage(target)

    monkeypatch.setattr(PALETTE, "aopen_file", aopen_file_mock)

    async def fake_stitch(rect_arg):
        return current

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    # First diff: no previous snapshot, so progress=0, regress=0 → no HistoryChange saved
    await proj.run_diff()
    changes = await HistoryChange.filter(project=proj.info).all()
    assert len(changes) == 0

    # Second diff: snapshot matches current exactly (no change) → still no HistoryChange
    await proj.run_diff()
    changes = await HistoryChange.filter(project=proj.info).all()
    assert len(changes) == 0


async def test_run_diff_saves_history_change_with_progress(monkeypatch, test_person):
    """Test that HistoryChange IS saved when there are progress pixels."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(rect, test_person.id, touch=True)

    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)
    target.putpixel((1, 1), 2)

    # First run: partial match, no prev snapshot
    current1 = _paletted_image((4, 4), value=0)
    current1.putpixel((0, 0), 1)

    original_open_file = PALETTE.open_file

    def aopen_file_mock(path_arg):
        if ".snapshot." in str(path_arg):
            return AsyncImage(original_open_file, path_arg)
        return FakeAsyncImage(target)

    monkeypatch.setattr(PALETTE, "aopen_file", aopen_file_mock)

    stitch_results = iter([current1])

    async def fake_stitch(rect_arg):
        return next(stitch_results)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()
    assert len(await HistoryChange.filter(project=proj.info).all()) == 0

    # Second run: progress detected (pixel (1,1) now matches target)
    current2 = _paletted_image((4, 4), value=0)
    current2.putpixel((0, 0), 1)
    current2.putpixel((1, 1), 2)

    stitch_results = iter([current2])
    await proj.run_diff()

    changes = await HistoryChange.filter(project=proj.info).all()
    assert len(changes) == 1
    assert changes[0].progress_pixels == 1
    assert changes[0].regress_pixels == 0


async def test_run_diff_progress_and_regress_tracking(monkeypatch, test_person):
    """Test progress/regress detection between checks."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(rect, test_person.id, touch=True)

    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)
    target.putpixel((1, 1), 2)

    current1 = _paletted_image((4, 4), value=0)
    current1.putpixel((0, 0), 1)

    original_open_file = PALETTE.open_file

    def aopen_file_mock(path_arg):
        if ".snapshot." in str(path_arg):
            return AsyncImage(original_open_file, path_arg)
        return FakeAsyncImage(target)

    monkeypatch.setattr(PALETTE, "aopen_file", aopen_file_mock)

    async def fake_stitch1(rect_arg):
        return current1

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch1)

    await proj.run_diff()
    initial_progress = proj.info.total_progress

    current2 = _paletted_image((4, 4), value=0)
    current2.putpixel((0, 0), 1)
    current2.putpixel((1, 1), 2)

    async def fake_stitch2(rect_arg):
        return current2

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch2)

    await proj.run_diff()

    assert proj.info.total_progress == initial_progress + 1


async def test_run_diff_regress_detection(monkeypatch, test_person):
    """Test regress (griefing) detection."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(rect, test_person.id, touch=True)

    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)

    current1 = _paletted_image((4, 4), value=0)
    current1.putpixel((0, 0), 1)

    monkeypatch.setattr(PALETTE, "aopen_file", lambda path_arg: FakeAsyncImage(target))

    async def fake_stitch1(rect_arg):
        return current1

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch1)

    await proj.run_diff()

    current2 = _paletted_image((4, 4), value=0)
    current2.putpixel((0, 0), 7)

    async def fake_stitch2(rect_arg):
        return current2

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch2)

    await proj.run_diff()

    assert proj.info.total_regress == 1
    assert proj.info.largest_regress_pixels == 1


async def test_run_diff_complete_status(monkeypatch, test_person):
    """Test complete project detection."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(rect, test_person.id, touch=True)

    target = _paletted_image((2, 2), value=1)
    current = _paletted_image((2, 2), value=1)

    monkeypatch.setattr(PALETTE, "aopen_file", lambda path_arg: FakeAsyncImage(target))

    async def fake_stitch(rect_arg):
        return current

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()

    assert "Complete" in proj.info.last_log_message


async def test_has_missing_tiles_all_present(setup_config, test_person):
    """Test _has_missing_tiles returns False when all tiles exist."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(rect, test_person.id, touch=True)

    for tile in rect.tiles:
        tile_file = setup_config.tiles_dir / f"tile-{tile}.png"
        tile_file.touch()

    assert proj._has_missing_tiles() is False


async def test_has_missing_tiles_some_missing(setup_config, test_person):
    """Test _has_missing_tiles returns True when some tiles are missing."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 2000))
    proj = await _make_project(rect, test_person.id, touch=True)

    tile_file = setup_config.tiles_dir / "tile-0_0.png"
    tile_file.touch()

    assert proj._has_missing_tiles() is True


async def test_has_missing_tiles_all_missing(setup_config, test_person):
    """Test _has_missing_tiles returns True when all tiles are missing."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(rect, test_person.id, touch=True)

    assert proj._has_missing_tiles() is True


async def test_run_diff_sets_has_missing_tiles(monkeypatch, setup_config, test_person):
    """Test run_diff properly sets has_missing_tiles flag."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    proj = await _make_project(rect, test_person.id)

    async def fake_stitch(rect):
        return _paletted_image((10, 10), 0)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()
    assert proj.info.has_missing_tiles is True

    tile_file = setup_config.tiles_dir / "tile-0_0.png"
    tile_file.touch()

    await proj.run_diff()
    assert proj.info.has_missing_tiles is False


# --- count_cached_tiles ---


async def test_count_cached_tiles_all_present(setup_config):
    """All tiles exist in cache."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    for tile in rect.tiles:
        (setup_config.tiles_dir / f"tile-{tile}.png").touch()

    cached, total = await projects.count_cached_tiles(rect)
    assert cached == total == 2


async def test_count_cached_tiles_some_present(setup_config):
    """Only one of two tiles exists."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    (setup_config.tiles_dir / "tile-0_0.png").touch()

    cached, total = await projects.count_cached_tiles(rect)
    assert cached == 1
    assert total == 2


async def test_count_cached_tiles_none_present(setup_config):
    """No tiles exist in cache."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))

    cached, total = await projects.count_cached_tiles(rect)
    assert cached == 0
    assert total == 2


# --- stitch_tiles ---
def _paletted_png_bytes(size=(1, 1), data=(0,)):
    im = PALETTE.new(size)
    im.putdata(list(data))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


async def test_stitch_tiles_missing_tile_logs_and_skips(setup_config):
    """Missing cache tiles are skipped with transparent pixels."""
    # Only create one of two needed tiles
    png_a = _paletted_png_bytes((1000, 1000), [1] * (1000 * 1000))
    (setup_config.tiles_dir / "tile-0_0.png").write_bytes(png_a)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    stitched = await projects.stitch_tiles(rect)
    assert stitched.size == rect.size


async def test_stitch_tiles_pastes_cached_tiles(setup_config):
    png_a = _paletted_png_bytes((1000, 1000), [1] * (1000 * 1000))
    png_b = _paletted_png_bytes((1000, 1000), [2] * (1000 * 1000))
    (setup_config.tiles_dir / "tile-0_0.png").write_bytes(png_a)
    (setup_config.tiles_dir / "tile-1_0.png").write_bytes(png_b)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    stitched = await projects.stitch_tiles(rect)
    assert stitched.size == rect.size
    data = stitched.get_flattened_data()
    assert any(p for p in data)
