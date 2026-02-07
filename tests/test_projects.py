import time
from types import SimpleNamespace

from PIL import Image

from cam import projects
from cam.geometry import Point, Rectangle, Size
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
    proj._image = DummyImage(target)

    class CM:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_flattened_data(self):
            return self.data

    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: CM(target))
    proj.run_diff()  # should early-return without error

    # Case 2: progress branch (different data)
    proj._image = DummyImage(bytes([0, 1, 2]))
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
    p._image = target

    # Case: current equals target -> complete branch
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: _paletted_image((4, 4), value=1))
    p.run_diff()  # should hit the 'Complete.' branch without error

    # Case: current different -> remaining/progress calculation path
    monkeypatch.setattr(projects, "stitch_tiles", lambda rect: _paletted_image((4, 4), value=0))
    p.run_diff()  # should compute remaining and log progress without error


# Project property tests


def test_project_image_property(tmp_path):
    """Test the image property opens/closes correctly."""
    # write an actual paletted file and exercise image property open and close
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=2)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    p = projects.Project(path, rect)
    img = p.image
    assert img.mode == "P"
    del p.image


# ProjectShim tests


def test_invalid_project_file_interface(tmp_path):
    """Test that ProjectShim has the expected interface."""
    path = tmp_path / "invalid.png"
    path.touch()

    invalid = projects.ProjectShim(path)
    assert invalid.path == path
    assert invalid.mtime is not None
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

    # Modify the file
    time.sleep(0.01)
    path.touch()
    assert invalid.has_been_modified()  # should detect change


def test_invalid_project_file_handles_oserror_on_init(tmp_path, monkeypatch):
    """Test that ProjectShim handles OSError during initialization."""
    path = tmp_path / "nonexistent.png"

    # File doesn't exist, so stat will fail
    invalid = projects.ProjectShim(path)
    assert invalid.mtime is None


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
    invalid.mtime = None

    # Should return True when mtime is None
    assert invalid.has_been_modified()


def test_invalid_project_file_nonexistent_stays_nonexistent(tmp_path):
    """Test ProjectShim when file never exists."""
    path = tmp_path / "never_exists.png"

    # Create ProjectShim for non-existent file
    invalid = projects.ProjectShim(path)
    assert invalid.mtime is None

    # File still doesn't exist - should return False (no modification)
    assert not invalid.has_been_modified()


def test_invalid_project_file_created_after_init(tmp_path):
    """Test ProjectShim when file is created after initialization."""
    path = tmp_path / "created_later.png"

    # Create ProjectShim for non-existent file
    invalid = projects.ProjectShim(path)
    assert invalid.mtime is None

    # Create the file
    time.sleep(0.01)
    path.touch()

    # File now exists - should return True (modification detected)
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

    # Modify the file
    time.sleep(0.01)
    path.touch()

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
    proj.mtime = None

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
    """Test Project __del__ method closes image."""
    path = tmp_path / "proj_0_0_0_0.png"
    im = _paletted_image((2, 2), value=1)
    im.save(path)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2, 2))
    proj = projects.Project(path, rect)

    # Access image to open it
    _ = proj.image

    # Delete the project - should not raise
    del proj


