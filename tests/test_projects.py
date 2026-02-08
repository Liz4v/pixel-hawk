import time
from types import SimpleNamespace

from PIL import Image

from cam import projects
from cam.geometry import Point, Rectangle, Size
from cam.metadata import ProjectMetadata
from cam.palette import PALETTE


def _paletted_image(size=(4, 4), value=1):
    """Helper to create a paletted image for testing."""
    im = PALETTE.new(size)
    im.putdata([value] * (size[0] * size[1]))
    return im


# Basic utility tests


def test_pixel_compare():
    from cam.projects import pixel_compare

    assert pixel_compare(1, 1) == 0
    assert pixel_compare(1, 2) == 2


# Project.try_open tests


def test_try_open_no_coords(tmp_path):
    """Test that try_open returns ProjectShim when filename has no coordinates."""
    p = tmp_path / "no_coords.png"
    p.write_bytes(b"x")
    result = projects.Project.try_open(p)
    assert type(result) is projects.ProjectShim
    assert result.path == p


def test_try_open_invalid_color(tmp_path, monkeypatch):
    """Test that try_open renames files with invalid palette colors."""
    # write an image with a color not in the palette
    path = tmp_path / "proj_0_0_0_0.png"
    im = Image.new("RGBA", (2, 2), (250, 251, 252, 255))
    im.save(path)

    # try_open should detect color not in palette and rename the file
    res = projects.Project.try_open(path)
    assert type(res) is projects.ProjectShim
    assert path.with_suffix(".invalid.png").exists()


def test_try_open_valid_project_and_run_diff(tmp_path, monkeypatch):
    """Test that try_open successfully opens a valid project file."""
    # create a correct paletted image
    path = tmp_path / "proj_1_1_0_0.png"
    im = PALETTE.new((10, 10))
    im.putdata([1] * 100)
    im.save(path)

    # monkeypatch stitch_tiles to return identical image -> run_diff should be quick
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: PALETTE.new((10, 10)))
    # avoid heavy logging or side effects
    monkeypatch.setattr(projects.Project, "run_diff", lambda self: None)

    res = projects.Project.try_open(path)
    assert isinstance(res, projects.Project)
    assert isinstance(res.rect, Rectangle)


# Project.run_diff tests


def test_run_diff_branches(monkeypatch, tmp_path):
    """Test run_diff with various scenarios (no change, changes)."""
    # create a dummy project and exercise run_diff branches
    p = tmp_path / "proj_0_0_1_1.png"
    p.touch()
    rect = Rectangle.from_point_size(Point.from4(0, 0, 0, 0), Size(1, 1))
    proj = projects.Project(p, rect)

    class DummyImage:
        def __init__(self, data):
            self._data = data

        def get_flattened_data(self):
            return self._data

        def close(self):
            pass

    # Case 1: no change (current == target)
    target = bytes([1, 2, 3])

    class CM:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_flattened_data(self):
            return self.data

    monkeypatch.setattr(PALETTE, "open_image", lambda path: CM(target))
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: CM(target))
    proj.run_diff()  # should early-return without error

    # Case 2: progress branch (different data)
    monkeypatch.setattr(PALETTE, "open_image", lambda path: CM(bytes([0, 1, 2])))
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: CM(bytes([2, 3, 4])))
    proj.run_diff()  # should run through progress logging


def test_run_diff_complete_and_remaining(monkeypatch, tmp_path):
    """Test run_diff complete and progress calculation paths."""
    # Create the file first so Project.__init__ doesn't fail
    proj_path = tmp_path / "proj_0_0_0_0.png"
    proj_path.touch()

    # Prepare a project with a target image in memory
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    p = projects.Project(proj_path, rect)

    target = _paletted_image((4, 4), value=1)

    # Case: current equals target -> complete branch
    monkeypatch.setattr(PALETTE, "open_image", lambda path: target)
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: _paletted_image((4, 4), value=1))
    p.run_diff()  # should hit the 'Complete.' branch without error

    # Case: current different -> remaining/progress calculation path
    monkeypatch.setattr(PALETTE, "open_image", lambda path: target)
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: _paletted_image((4, 4), value=0))
    p.run_diff()  # should compute remaining and log progress without error


# ProjectShim tests


def test_invalid_project_file_interface(tmp_path):
    """Test that ProjectShim has the expected interface."""
    path = tmp_path / "invalid.png"
    path.touch()

    invalid = projects.ProjectShim(path)
    assert invalid.path == path
    assert invalid.mtime != 0
    assert hasattr(invalid, "rect")
    assert len(list(invalid.rect.tiles)) == 0  # empty rect
    assert callable(invalid.has_been_modified)
    assert callable(invalid.run_diff)

    # run_diff should be a no-op
    invalid.run_diff()  # should not raise


def test_invalid_project_file_has_been_modified(tmp_path):
    """Test that ProjectShim.has_been_modified detects changes."""
    path = tmp_path / "test.png"
    path.touch()

    invalid = projects.ProjectShim(path)
    assert not invalid.has_been_modified()  # just created

    # Manually set mtime to a different value to simulate passage of time
    # When has_been_modified() checks against the real file's mtime, it will detect the difference
    real_mtime = round(path.stat().st_mtime)
    invalid.mtime = real_mtime - 1  # Set to 1 second earlier

    assert invalid.has_been_modified()  # should detect change


def test_invalid_project_file_handles_oserror_on_init(tmp_path, monkeypatch):
    """Test that ProjectShim handles OSError during initialization."""
    path = tmp_path / "nonexistent.png"

    # File doesn't exist, so stat will fail
    invalid = projects.ProjectShim(path)
    assert invalid.mtime == 0


def test_invalid_project_file_has_been_modified_with_oserror(tmp_path, monkeypatch):
    """Test that ProjectShim.has_been_modified handles OSError."""
    path = tmp_path / "test.png"
    path.touch()

    invalid = projects.ProjectShim(path)

    # Delete the file after creating the instance
    path.unlink()

    # has_been_modified should return True when stat fails
    assert invalid.has_been_modified()


def test_invalid_project_file_has_been_modified_with_none_mtime(tmp_path):
    """Test ProjectShim.has_been_modified when mtime is None."""
    path = tmp_path / "test.png"
    path.touch()

    invalid = projects.ProjectShim(path)
    invalid.mtime = 0

    # Should return True when mtime is None
    assert invalid.has_been_modified()


def test_invalid_project_file_nonexistent_stays_nonexistent(tmp_path):
    """Test ProjectShim when file never exists."""
    path = tmp_path / "never_exists.png"

    # Create ProjectShim for non-existent file
    invalid = projects.ProjectShim(path)
    assert invalid.mtime == 0

    # File still doesn't exist - should return False (no modification)
    assert not invalid.has_been_modified()


def test_invalid_project_file_created_after_init(tmp_path):
    """Test ProjectShim when file is created after initialization."""
    path = tmp_path / "created_later.png"

    # Create ProjectShim for non-existent file
    invalid = projects.ProjectShim(path)
    assert invalid.mtime == 0

    # Create the file
    path.touch()

    # File now exists with mtime > 0, and stored mtime is 0
    # has_been_modified() should detect this difference
    assert invalid.has_been_modified()


# Project.scan_directory tests


def test_scan_directory(tmp_path, monkeypatch):
    """Test Project.scan_directory returns PNG files."""
    wplace_dir = tmp_path / "wplace"
    wplace_dir.mkdir()

    monkeypatch.setattr("cam.projects.DIRS", SimpleNamespace(user_pictures_path=tmp_path))

    # Create some files
    png1 = wplace_dir / "file1.png"
    png2 = wplace_dir / "file2.png"
    txt = wplace_dir / "file.txt"
    png1.touch()
    png2.touch()
    txt.touch()

    result = projects.Project.scan_directory()
    assert png1 in result
    assert png2 in result
    assert txt not in result
    assert len(result) == 2


# Project.has_been_modified tests


def test_project_has_been_modified(tmp_path):
    """Test Project.has_been_modified detects file changes."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    # Should not be modified right after creation
    assert not proj.has_been_modified()

    # Manually set mtime to a different value to simulate passage of time
    # When has_been_modified() checks against the real file's mtime, it will detect the difference
    real_mtime = round(path.stat().st_mtime)
    proj.mtime = real_mtime - 1  # Set to 1 second earlier

    # Should detect modification
    assert proj.has_been_modified()


def test_project_has_been_modified_with_oserror(tmp_path):
    """Test Project.has_been_modified handles OSError."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    # Delete the file
    path.unlink()

    # Should return True when stat fails
    assert proj.has_been_modified()


def test_project_has_been_modified_with_none_mtime(tmp_path):
    """Test Project.has_been_modified when mtime is None."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)
    proj.mtime = 0

    # Should return True when mtime is None
    assert proj.has_been_modified()


def test_project_equality_and_hash(tmp_path):
    """Test Project __eq__ and __hash__ methods."""
    path1 = tmp_path / "proj_0_0_0_0.png"
    path2 = tmp_path / "proj_1_1_1_1.png"

    im = _paletted_image((2, 2), value=1)
    im.save(path1)
    im.save(path2)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj1 = projects.Project(path1, rect)
    proj2 = projects.Project(path1, rect)  # same path
    proj3 = projects.Project(path2, rect)  # different path

    # Same path should be equal
    assert proj1 == proj2
    assert hash(proj1) == hash(proj2)

    # Different paths should not be equal
    assert proj1 != proj3
    assert hash(proj1) != hash(proj3)

    # Equality with non-Project object
    assert proj1 != "not a project"


def test_project_deletion(tmp_path):
    """Test Project deletion does not raise."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    # Delete the project - should not raise
    del proj


# ProjectMetadata tests


def test_metadata_from_rect():
    """Test ProjectMetadata.from_rect creates correct initial state."""
    rect = Rectangle.from_point_size(Point(100, 200), Size(50, 60))
    meta = ProjectMetadata.from_rect(rect)

    assert meta.x == 100
    assert meta.y == 200
    assert meta.width == 50
    assert meta.height == 60
    assert meta.first_seen > 0
    assert meta.last_check > 0
    assert meta.max_completion_pixels == 0
    assert meta.total_progress == 0
    assert meta.total_regress == 0


def test_metadata_to_dict_and_from_dict():
    """Test metadata serialization round-trip."""
    rect = Rectangle.from_point_size(Point(10, 20), Size(30, 40))
    meta = ProjectMetadata.from_rect(rect)
    meta.max_completion_pixels = 100
    meta.max_completion_percent = 75.5
    meta.total_progress = 50
    meta.total_regress = 5
    meta.streak_type = "progress"
    meta.streak_count = 3
    meta.tile_last_update = {"1_2": 12345, "3_4": 67890}
    meta.tile_updates_24h = [("1_2", 12345), ("3_4", 67890)]

    data = meta.to_dict()
    meta2 = ProjectMetadata.from_dict(data)

    assert meta2.x == meta.x
    assert meta2.y == meta.y
    assert meta2.width == meta.width
    assert meta2.height == meta.height
    assert meta2.max_completion_pixels == meta.max_completion_pixels
    assert meta2.max_completion_percent == meta.max_completion_percent
    assert meta2.total_progress == meta.total_progress
    assert meta2.total_regress == meta.total_regress
    assert meta2.change_streak_type == meta.change_streak_type
    assert meta2.change_streak_count == meta.change_streak_count
    assert meta2.nochange_streak_count == meta.nochange_streak_count
    assert meta2.tile_last_update == meta.tile_last_update
    assert meta2.tile_updates_24h == meta.tile_updates_24h


def test_metadata_prune_old_tile_updates():
    """Test pruning of old tile updates from 24h list."""
    meta = ProjectMetadata()
    now = round(time.time())
    old_time = now - 100000  # more than 24h ago
    recent_time = now - 1000  # within 24h

    meta.tile_updates_24h = [
        ("1_2", old_time),
        ("3_4", recent_time),
        ("5_6", old_time),
        ("7_8", recent_time),
    ]

    meta.last_check = now
    meta.prune_old_tile_updates()

    assert len(meta.tile_updates_24h) == 2
    assert ("3_4", recent_time) in meta.tile_updates_24h
    assert ("7_8", recent_time) in meta.tile_updates_24h
    assert ("1_2", old_time) not in meta.tile_updates_24h


def test_metadata_update_tile():
    """Test tile update recording."""
    from cam.geometry import Tile

    meta = ProjectMetadata()
    tile = Tile(1, 2)
    timestamp = 12345

    meta.update_tile(tile, timestamp)

    assert meta.tile_last_update["1_2"] == timestamp
    assert ("1_2", timestamp) in meta.tile_updates_24h

    # Update same tile with new timestamp
    new_timestamp = 67890
    meta.update_tile(tile, new_timestamp)

    assert meta.tile_last_update["1_2"] == new_timestamp
    assert ("1_2", new_timestamp) in meta.tile_updates_24h


def test_project_metadata_paths(tmp_path):
    """Test snapshot_path and metadata_path properties."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    assert proj.snapshot_path == tmp_path / "proj_0_0_0_0.snapshot.png"
    assert proj.metadata_path == tmp_path / "proj_0_0_0_0.metadata.yaml"


def test_project_metadata_save_and_load(tmp_path):
    """Test metadata persistence to YAML file."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    # Modify metadata
    proj.metadata.max_completion_pixels = 42
    proj.metadata.total_progress = 100
    proj.metadata.change_streak_type = "progress"
    proj.metadata.change_streak_count = 5

    # Save metadata
    proj.save_metadata()
    assert proj.metadata_path.exists()

    # Create new project instance and verify metadata loaded
    proj2 = projects.Project(path, rect)
    assert proj2.metadata.max_completion_pixels == 42
    assert proj2.metadata.total_progress == 100
    assert proj2.metadata.change_streak_type == "progress"
    assert proj2.metadata.change_streak_count == 5


def test_project_snapshot_save_and_load(tmp_path, monkeypatch):
    """Test snapshot persistence."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((4, 4), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = projects.Project(path, rect)

    # Create and save a snapshot
    snapshot = _paletted_image((4, 4), value=2)
    proj.save_snapshot(snapshot)
    assert proj.snapshot_path.exists()
    assert proj.metadata.last_snapshot > 0

    # Load snapshot and verify
    loaded = proj.load_snapshot()
    assert loaded is not None
    with loaded:
        data = loaded.get_flattened_data()
        assert all(v == 2 for v in data)


def test_project_snapshot_load_nonexistent(tmp_path):
    """Test loading snapshot when it doesn't exist."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    snapshot = proj.load_snapshot()
    assert snapshot is None


def test_run_diff_with_metadata_tracking(tmp_path, monkeypatch):
    """Test that run_diff updates metadata correctly."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = projects.Project(path, rect)

    # Setup: target has some pixels set
    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)
    target.putpixel((1, 1), 2)
    target.putpixel((2, 2), 3)

    # Current state: partial progress (1 pixel correct, 2 wrong)
    current = _paletted_image((4, 4), value=0)
    current.putpixel((0, 0), 1)  # correct

    monkeypatch.setattr(PALETTE, "open_image", lambda path_arg: target)
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect_arg: current)

    proj.run_diff()

    # Check metadata was updated
    assert proj.metadata.last_check > 0
    assert proj.metadata.max_completion_pixels > 0
    assert proj.metadata.max_completion_percent > 0
    assert proj.snapshot_path.exists()


def test_run_diff_progress_and_regress_tracking(tmp_path, monkeypatch):
    """Test progress/regress detection between checks."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = projects.Project(path, rect)

    # Target: pixels (0,0)=1, (1,1)=2
    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)
    target.putpixel((1, 1), 2)

    # First check: (0,0) correct
    current1 = _paletted_image((4, 4), value=0)
    current1.putpixel((0, 0), 1)

    # Monkeypatch to return target for project path, let snapshots work normally
    original_open_image = PALETTE.open_image

    def open_image_mock(path_arg):
        if ".snapshot." in str(path_arg):
            return original_open_image(path_arg)  # Use real implementation for snapshots
        return target

    monkeypatch.setattr(PALETTE, "open_image", open_image_mock)
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect_arg: current1)

    proj.run_diff()
    initial_progress = proj.metadata.total_progress

    # Second check: (0,0) still correct, (1,1) now correct too (progress)
    current2 = _paletted_image((4, 4), value=0)
    current2.putpixel((0, 0), 1)
    current2.putpixel((1, 1), 2)

    monkeypatch.setattr(projects, "stitch_tiles", lambda rect_arg: current2)

    proj.run_diff()

    # Should have detected 1 pixel of progress
    assert proj.metadata.total_progress == initial_progress + 1
    assert proj.metadata.change_streak_type == "progress"
    assert proj.metadata.change_streak_count >= 1


def test_run_diff_regress_detection(tmp_path, monkeypatch):
    """Test regress (griefing) detection."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(4, 4))
    proj = projects.Project(path, rect)

    # Target: pixel (0,0)=1
    target = _paletted_image((4, 4), value=0)
    target.putpixel((0, 0), 1)

    # First check: (0,0) correct
    current1 = _paletted_image((4, 4), value=0)
    current1.putpixel((0, 0), 1)

    monkeypatch.setattr(PALETTE, "open_image", lambda path_arg: target)
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect_arg: current1)

    proj.run_diff()

    # Second check: (0,0) now wrong (regress)
    current2 = _paletted_image((4, 4), value=0)
    current2.putpixel((0, 0), 7)  # wrong color

    monkeypatch.setattr(projects, "stitch_tiles", lambda rect_arg: current2)

    proj.run_diff()

    # Should have detected regress
    assert proj.metadata.total_regress == 1
    assert proj.metadata.change_streak_type == "regress"
    assert proj.metadata.largest_regress_pixels == 1


def test_run_diff_complete_status(tmp_path, monkeypatch):
    """Test complete project detection."""
    path = tmp_path / "proj_0_0_0_0.png"
    path.touch()
    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    # Target and current match perfectly
    target = _paletted_image((2, 2), value=1)
    current = _paletted_image((2, 2), value=1)

    monkeypatch.setattr(PALETTE, "open_image", lambda path_arg: target)
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect_arg: current)

    proj.run_diff()

    # Should detect as complete - no pixels to fill
    assert proj.metadata.nochange_streak_count >= 1
