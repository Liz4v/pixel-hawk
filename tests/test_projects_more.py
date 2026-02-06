from pathlib import Path

from wwpppp import projects
from wwpppp.cache import ProjectCacheDB
from wwpppp.geometry import Point, Rectangle, Size
from wwpppp.palette import PALETTE


def test_try_open_with_bad_cached_type(tmp_path, monkeypatch):
    # create a valid paletted image file
    p = tmp_path / "proj_0_0_1_1.png"
    img = PALETTE.new((2, 2))
    img.putdata([1, 0, 0, 0])
    img.save(p)

    class BadCached:
        def __init__(self, path):
            pass

        def __bool__(self):
            return True

        def __iter__(self):
            return iter([1, 2, 3])

        def __call__(self, rect):
            return [rect]

    monkeypatch.setattr(projects, "CachedProjectMetadata", BadCached)

    res = projects.Project.try_open(p)
    assert res is not None
    assert isinstance(res, projects.Project)


def test_try_open_no_coords(tmp_path):
    p = tmp_path / "nocoords.png"
    p.touch()
    assert projects.Project.try_open(p) is None


def test_run_diff_branches(monkeypatch, tmp_path):
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


def test_pixel_compare():
    assert projects.pixel_compare(1, 1) == 0
    assert projects.pixel_compare(1, 2) == 2
