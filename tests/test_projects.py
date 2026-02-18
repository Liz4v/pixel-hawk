import io


import pytest
from PIL import Image

from pixel_hawk import projects
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


async def _make_project(path, rect, owner_id):
    """Helper to create a Project with a DB-backed ProjectInfo."""
    info = await ProjectInfo.get_or_create_from_rect(rect, owner_id, path.with_suffix("").name)
    # Fetch owner relationship for metadata log messages
    await info.fetch_related("owner")
    return projects.Project(path, rect, info)


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


async def test_run_diff_branches(monkeypatch, tmp_path, test_person):
    """Test run_diff with various scenarios (no change, changes)."""
    p = tmp_path / "proj_0_0_1_1.png"
    p.touch()
    rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1, 1))
    proj = await _make_project(p, rect, test_person.id)

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


async def test_run_diff_complete_and_remaining(monkeypatch, tmp_path, test_person):
    """Test run_diff complete and progress calculation paths."""
    proj_path = tmp_path / "proj_0_0_0_0.png"
    proj_path.touch()

    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    p = await _make_project(proj_path, rect, test_person.id)

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


async def test_project_has_been_modified(tmp_path, test_person):
    """Test Project.has_been_modified detects file changes."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect, test_person.id)

    assert not proj.has_been_modified()

    real_mtime = round(path.stat().st_mtime)
    proj.mtime = real_mtime - 1

    assert proj.has_been_modified()


async def test_project_has_been_modified_with_oserror(tmp_path, test_person):
    """Test Project.has_been_modified handles OSError."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect, test_person.id)

    path.unlink()
    assert proj.has_been_modified()


async def test_project_has_been_modified_with_none_mtime(tmp_path, test_person):
    """Test Project.has_been_modified when mtime is 0."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect, test_person.id)
    proj.mtime = 0

    assert proj.has_been_modified()


async def test_project_equality_and_hash(tmp_path, test_person):
    """Test Project __eq__ and __hash__ methods."""
    path1 = tmp_path / "proj_0_0_0_0.png"
    path2 = tmp_path / "proj_1_1_1_1.png"

    im = _paletted_image((2, 2), value=1)
    im.save(path1)
    im.save(path2)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj1 = await _make_project(path1, rect, test_person.id)
    proj2 = await _make_project(path1, rect, test_person.id)
    proj3 = await _make_project(path2, rect, test_person.id)

    assert proj1 == proj2
    assert hash(proj1) == hash(proj2)
    assert proj1 != proj3
    assert hash(proj1) != hash(proj3)
    assert proj1 != "not a project"


async def test_project_deletion(tmp_path, test_person):
    """Test Project deletion does not raise."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect, test_person.id)
    del proj


# ProjectInfo DB persistence tests


async def test_project_info_save_and_load(tmp_path, test_person):
    """Test ProjectInfo persistence via DB."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect, test_person.id)

    proj.info.max_completion_pixels = 42
    proj.info.total_progress = 100
    await proj.info.save()

    # Load fresh from DB
    loaded = await ProjectInfo.get(owner=test_person, name=proj.info.name)
    assert loaded.max_completion_pixels == 42
    assert loaded.total_progress == 100


async def test_project_snapshot_save_and_load(tmp_path, monkeypatch, test_person):
    """Test snapshot persistence."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((4, 4), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect, test_person.id)

    snapshot = _paletted_image((4, 4), value=2)
    await proj.save_snapshot(snapshot)
    assert proj.snapshot_path.exists()
    assert proj.info.last_snapshot > 0

    async with proj.load_snapshot_if_exists() as loaded:
        assert loaded is not None
        data = loaded.get_flattened_data()
        assert all(v == 2 for v in data)


async def test_project_snapshot_load_nonexistent(tmp_path, test_person):
    """Test loading snapshot when it doesn't exist."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect, test_person.id)

    async with proj.load_snapshot_if_exists() as snapshot:
        assert snapshot is None


async def test_run_diff_with_info_tracking(tmp_path, monkeypatch, test_person):
    """Test that run_diff updates info correctly."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect, test_person.id)

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


async def test_run_diff_creates_history_change(tmp_path, monkeypatch, test_person):
    """Test that run_diff creates a HistoryChange record when progress is detected."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect, test_person.id)

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


async def test_run_diff_skips_history_change_without_progress_or_regress(tmp_path, monkeypatch, test_person):
    """Test that HistoryChange is NOT saved when there are no progress or regress pixels."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect, test_person.id)

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


async def test_run_diff_saves_history_change_with_progress(tmp_path, monkeypatch, test_person):
    """Test that HistoryChange IS saved when there are progress pixels."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect, test_person.id)

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


async def test_run_diff_progress_and_regress_tracking(tmp_path, monkeypatch, test_person):
    """Test progress/regress detection between checks."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect, test_person.id)

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


async def test_run_diff_regress_detection(tmp_path, monkeypatch, test_person):
    """Test regress (griefing) detection."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect, test_person.id)

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


async def test_run_diff_complete_status(tmp_path, monkeypatch, test_person):
    """Test complete project detection."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect, test_person.id)

    target = _paletted_image((2, 2), value=1)
    current = _paletted_image((2, 2), value=1)

    monkeypatch.setattr(PALETTE, "aopen_file", lambda path_arg: FakeAsyncImage(target))

    async def fake_stitch(rect_arg):
        return current

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()

    assert "Complete" in proj.info.last_log_message


async def test_has_missing_tiles_all_present(tmp_path, monkeypatch, setup_config, test_person):
    """Test _has_missing_tiles returns False when all tiles exist."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(path, rect, test_person.id)

    for tile in rect.tiles:
        tile_file = setup_config.tiles_dir / f"tile-{tile}.png"
        tile_file.touch()

    assert proj._has_missing_tiles() is False


async def test_has_missing_tiles_some_missing(tmp_path, monkeypatch, setup_config, test_person):
    """Test _has_missing_tiles returns True when some tiles are missing."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 2000))
    proj = await _make_project(path, rect, test_person.id)

    tile_file = setup_config.tiles_dir / "tile-0_0.png"
    tile_file.touch()

    assert proj._has_missing_tiles() is True


async def test_has_missing_tiles_all_missing(tmp_path, monkeypatch, setup_config, test_person):
    """Test _has_missing_tiles returns True when all tiles are missing."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(path, rect, test_person.id)

    assert proj._has_missing_tiles() is True


async def test_run_diff_sets_has_missing_tiles(tmp_path, monkeypatch, setup_config, test_person):
    """Test run_diff properly sets has_missing_tiles flag."""
    path = tmp_path / "proj_0_0_0_0.png"

    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    proj = await _make_project(path, rect, test_person.id)

    async def fake_stitch(rect):
        return _paletted_image((10, 10), 0)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()
    assert proj.info.has_missing_tiles is True

    tile_file = setup_config.tiles_dir / "tile-0_0.png"
    tile_file.touch()

    await proj.run_diff()
    assert proj.info.has_missing_tiles is False


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
