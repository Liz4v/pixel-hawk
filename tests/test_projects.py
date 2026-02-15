import os

from PIL import Image

from pixel_hawk import projects
from pixel_hawk.geometry import Point, Rectangle, Size, Tile
from pixel_hawk.models import HistoryChange, ProjectInfo
from pixel_hawk.palette import PALETTE, AsyncImage


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


async def _make_project(path, rect):
    """Helper to create a Project with a DB-backed ProjectInfo."""
    info = await ProjectInfo.get_or_create_from_rect(rect, path.with_suffix("").name)
    return projects.Project(path, rect, info)


# Project.try_open tests


async def test_try_open_no_coords(tmp_path, setup_config):
    """Test that try_open returns None and moves file to rejected/ when filename has no coordinates."""
    p = setup_config.projects_dir / "no_coords.png"
    p.write_bytes(b"x")
    result = await projects.Project.try_open(p)
    assert result is None
    assert not p.exists()
    assert (setup_config.rejected_dir / "no_coords.png").exists()


async def test_try_open_invalid_color(tmp_path, setup_config):
    """Test that try_open moves files with invalid palette colors to rejected/."""
    path = setup_config.projects_dir / "proj_0_0_0_0.png"
    im = Image.new("RGBA", (2, 2), (250, 251, 252, 255))
    im.save(path)

    res = await projects.Project.try_open(path)
    assert res is None
    assert not path.exists()
    assert (setup_config.rejected_dir / "proj_0_0_0_0.png").exists()


async def test_try_open_valid_project_and_run_diff(tmp_path, monkeypatch):
    """Test that try_open successfully opens a valid project file."""
    path = tmp_path / "proj_1_1_0_0.png"
    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    res = await projects.Project.try_open(path)
    assert isinstance(res, projects.Project)
    assert isinstance(res.rect, Rectangle)


# Project.run_diff tests


async def test_run_diff_branches(monkeypatch, tmp_path):
    """Test run_diff with various scenarios (no change, changes)."""
    p = tmp_path / "proj_0_0_1_1.png"
    p.touch()
    rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1, 1))
    proj = await _make_project(p, rect)

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


async def test_run_diff_complete_and_remaining(monkeypatch, tmp_path):
    """Test run_diff complete and progress calculation paths."""
    proj_path = tmp_path / "proj_0_0_0_0.png"
    proj_path.touch()

    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    p = await _make_project(proj_path, rect)

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


async def test_try_open_non_file(tmp_path, setup_config):
    """Test that try_open returns None for directories and non-files."""
    d = tmp_path / "not_a_file_0_0_0_0.png"
    d.mkdir()
    result = await projects.Project.try_open(d)
    assert result is None


async def test_project_first_seen(tmp_path, setup_config, monkeypatch):
    """Test that Project.info.first_seen is set on creation."""
    path = setup_config.projects_dir / "proj_0_0_0_0.png"
    img = PALETTE.new((4, 4))
    img.save(path)

    async def noop_run_diff(self, changed_tile=None):
        pass

    monkeypatch.setattr(projects.Project, "run_diff", noop_run_diff)

    proj = await projects.Project.try_open(path)
    assert isinstance(proj, projects.Project)
    assert proj.info.first_seen > 0


# Project.scan_directory tests


async def test_scan_directory(tmp_path, setup_config):
    """Test Project.scan_directory returns PNG files."""
    projects_dir = setup_config.projects_dir

    png1 = projects_dir / "file1.png"
    png2 = projects_dir / "file2.png"
    txt = projects_dir / "file.txt"
    png1.touch()
    png2.touch()
    txt.touch()

    result = await projects.Project.scan_directory()
    assert png1 in result
    assert png2 in result
    assert txt not in result
    assert len(result) == 2


# Project.has_been_modified tests


async def test_project_has_been_modified(tmp_path):
    """Test Project.has_been_modified detects file changes."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect)

    assert not proj.has_been_modified()

    real_mtime = round(path.stat().st_mtime)
    proj.mtime = real_mtime - 1

    assert proj.has_been_modified()


async def test_project_has_been_modified_with_oserror(tmp_path):
    """Test Project.has_been_modified handles OSError."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect)

    path.unlink()
    assert proj.has_been_modified()


async def test_project_has_been_modified_with_none_mtime(tmp_path):
    """Test Project.has_been_modified when mtime is 0."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect)
    proj.mtime = 0

    assert proj.has_been_modified()


async def test_project_equality_and_hash(tmp_path):
    """Test Project __eq__ and __hash__ methods."""
    path1 = tmp_path / "proj_0_0_0_0.png"
    path2 = tmp_path / "proj_1_1_1_1.png"

    im = _paletted_image((2, 2), value=1)
    im.save(path1)
    im.save(path2)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj1 = await _make_project(path1, rect)
    proj2 = await _make_project(path1, rect)
    proj3 = await _make_project(path2, rect)

    assert proj1 == proj2
    assert hash(proj1) == hash(proj2)
    assert proj1 != proj3
    assert hash(proj1) != hash(proj3)
    assert proj1 != "not a project"


async def test_project_deletion(tmp_path):
    """Test Project deletion does not raise."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect)
    del proj


# ProjectInfo DB persistence tests


async def test_project_info_save_and_load(tmp_path):
    """Test ProjectInfo persistence via DB."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect)

    proj.info.max_completion_pixels = 42
    proj.info.total_progress = 100
    await proj.info.save()

    # Load fresh from DB
    loaded = await ProjectInfo.get(name=proj.info.name)
    assert loaded.max_completion_pixels == 42
    assert loaded.total_progress == 100


async def test_project_snapshot_save_and_load(tmp_path, monkeypatch):
    """Test snapshot persistence."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((4, 4), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect)

    snapshot = _paletted_image((4, 4), value=2)
    await proj.save_snapshot(snapshot)
    assert proj.snapshot_path.exists()
    assert proj.info.last_snapshot > 0

    async with proj.load_snapshot_if_exists() as loaded:
        assert loaded is not None
        data = loaded.get_flattened_data()
        assert all(v == 2 for v in data)


async def test_project_snapshot_load_nonexistent(tmp_path):
    """Test loading snapshot when it doesn't exist."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect)

    async with proj.load_snapshot_if_exists() as snapshot:
        assert snapshot is None


async def test_run_diff_with_info_tracking(tmp_path, monkeypatch):
    """Test that run_diff updates info correctly."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect)

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


async def test_run_diff_creates_history_change(tmp_path, monkeypatch):
    """Test that run_diff creates a HistoryChange record."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect)

    target = _paletted_image((4, 4), value=1)
    current = _paletted_image((4, 4), value=1)

    monkeypatch.setattr(PALETTE, "aopen_file", lambda path_arg: FakeAsyncImage(target))

    async def fake_stitch(rect_arg):
        return current

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()

    # Should have created at least one HistoryChange record
    changes = await HistoryChange.filter(project=proj.info).all()
    assert len(changes) >= 1
    assert changes[0].num_target > 0


async def test_run_diff_progress_and_regress_tracking(tmp_path, monkeypatch):
    """Test progress/regress detection between checks."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect)

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


async def test_run_diff_regress_detection(tmp_path, monkeypatch):
    """Test regress (griefing) detection."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = await _make_project(path, rect)

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


async def test_run_diff_complete_status(tmp_path, monkeypatch):
    """Test complete project detection."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = await _make_project(path, rect)

    target = _paletted_image((2, 2), value=1)
    current = _paletted_image((2, 2), value=1)

    monkeypatch.setattr(PALETTE, "aopen_file", lambda path_arg: FakeAsyncImage(target))

    async def fake_stitch(rect_arg):
        return current

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()

    assert "Complete" in proj.info.last_log_message


async def test_update_single_tile_metadata_updates_when_newer(tmp_path, monkeypatch, setup_config):
    """Test _update_single_tile_metadata updates when tile file is newer."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(path, rect)

    tile = Tile(0, 0)
    tile_path = setup_config.tiles_dir / f"tile-{tile}.png"
    tile_path.write_bytes(b"dummy")

    tile_mtime = 10000
    os.utime(tile_path, (tile_mtime, tile_mtime))

    proj.info.tile_last_update["0_0"] = 5000

    proj._update_single_tile_metadata(tile)

    assert proj.info.tile_last_update["0_0"] == tile_mtime
    assert ["0_0", tile_mtime] in proj.info.tile_updates_24h


async def test_update_single_tile_metadata_skips_when_not_newer(tmp_path, monkeypatch, setup_config):
    """Test _update_single_tile_metadata skips update when tile not newer."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(path, rect)

    tile = Tile(0, 0)
    tile_path = setup_config.tiles_dir / f"tile-{tile}.png"
    tile_path.write_bytes(b"dummy")

    tile_mtime = 10000
    os.utime(tile_path, (tile_mtime, tile_mtime))

    proj.info.tile_last_update["0_0"] = 15000
    proj.info.tile_updates_24h = [["0_0", 15000]]

    proj._update_single_tile_metadata(tile)

    assert proj.info.tile_last_update["0_0"] == 15000
    assert len(proj.info.tile_updates_24h) == 1
    assert ["0_0", 15000] in proj.info.tile_updates_24h


async def test_update_single_tile_metadata_handles_missing_file(tmp_path, monkeypatch, setup_config):
    """Test _update_single_tile_metadata handles nonexistent tile file."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(path, rect)

    tile = Tile(0, 0)

    proj.info.tile_last_update = {}
    proj.info.tile_updates_24h = []

    proj._update_single_tile_metadata(tile)

    assert "0_0" not in proj.info.tile_last_update
    assert len(proj.info.tile_updates_24h) == 0


async def test_has_missing_tiles_all_present(tmp_path, monkeypatch, setup_config):
    """Test _has_missing_tiles returns False when all tiles exist."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(path, rect)

    for tile in rect.tiles:
        tile_file = setup_config.tiles_dir / f"tile-{tile}.png"
        tile_file.touch()

    assert proj._has_missing_tiles() is False


async def test_has_missing_tiles_some_missing(tmp_path, monkeypatch, setup_config):
    """Test _has_missing_tiles returns True when some tiles are missing."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 2000))
    proj = await _make_project(path, rect)

    tile_file = setup_config.tiles_dir / "tile-0_0.png"
    tile_file.touch()

    assert proj._has_missing_tiles() is True


async def test_has_missing_tiles_all_missing(tmp_path, monkeypatch, setup_config):
    """Test _has_missing_tiles returns True when all tiles are missing."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = await _make_project(path, rect)

    assert proj._has_missing_tiles() is True


async def test_run_diff_sets_has_missing_tiles(tmp_path, monkeypatch, setup_config):
    """Test run_diff properly sets has_missing_tiles flag."""
    path = tmp_path / "proj_0_0_0_0.png"

    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(10, 10))
    proj = await _make_project(path, rect)

    async def fake_stitch(rect):
        return _paletted_image((10, 10), 0)

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)

    await proj.run_diff()
    assert proj.info.has_missing_tiles is True

    tile_file = setup_config.tiles_dir / "tile-0_0.png"
    tile_file.touch()

    await proj.run_diff()
    assert proj.info.has_missing_tiles is False


# YAML migration tests


async def test_yaml_migration(tmp_path, setup_config):
    """Test migration of legacy YAML metadata to SQLite."""
    from ruamel.yaml import YAML as YAMLWriter

    yaml_writer = YAMLWriter(typ="safe")
    yaml_writer.default_flow_style = False

    # Create a legacy YAML metadata file
    yaml_path = setup_config.metadata_dir / "test_proj.metadata.yaml"
    data = {
        "name": "test_proj",
        "bounds": {"x": 100, "y": 200, "width": 50, "height": 60},
        "timestamps": {"first_seen": 1000, "last_check": 2000, "last_snapshot": 1500},
        "max_completion": {"pixels_remaining": 42, "percent_complete": 75.5, "achieved_at": 1800},
        "totals": {"progress_pixels": 100, "regress_pixels": 10},
        "largest_regress": {"pixels": 5, "timestamp": 1200},
        "recent_rate": {"pixels_per_hour": 12.5, "window_start": 900},
        "tile_updates": {
            "last_update_by_tile": {"1_2": 1500},
            "recent_24h": [{"tile": "1_2", "timestamp": 1500}],
        },
        "cache_state": {"has_missing_tiles": False},
        "last_log_message": "test message",
    }
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml_writer.dump(data, f)

    # Trigger migration
    rect = Rectangle.from_point_size(Point(100, 200), Size(50, 60))
    info = await projects._load_or_migrate_info(rect, "test_proj")

    # Verify migration worked
    assert info.name == "test_proj"
    assert info.x == 100
    assert info.y == 200
    assert info.first_seen == 1000
    assert info.max_completion_pixels == 42
    assert info.total_progress == 100
    assert info.tile_last_update == {"1_2": 1500}
    assert info.tile_updates_24h == [["1_2", 1500]]
    assert info.has_missing_tiles is False

    # YAML file should be renamed
    assert not yaml_path.exists()
    assert yaml_path.with_suffix(".yaml.migrated").exists()

    # Re-running should use DB, not YAML
    info2 = await projects._load_or_migrate_info(rect, "test_proj")
    assert info2.name == "test_proj"
    assert info2.total_progress == 100
