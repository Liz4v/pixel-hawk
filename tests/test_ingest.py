import io
from types import SimpleNamespace

from wwpppp.geometry import Point, Rectangle, Size, Tile
from wwpppp.ingest import has_tile_changed, stitch_tiles
from wwpppp.palette import PALETTE


def _paletted_png_bytes(size=(1, 1), data=(0,)):
    im = PALETTE.new(size)
    im.putdata(list(data))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_has_tile_changed_http_error(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))

    class Resp:
        status_code = 404
        content = b""
        headers = {}

    monkeypatch.setattr("wwpppp.ingest.requests.get", lambda *a, **k: Resp())
    assert not has_tile_changed(Tile(0, 0))


def test_has_tile_changed_bad_image(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))

    class Resp:
        status_code = 200
        content = b"not an image"
        headers = {}

    monkeypatch.setattr("wwpppp.ingest.requests.get", lambda *a, **k: Resp())
    assert not has_tile_changed(Tile(0, 0))


def test_has_tile_changed_no_change_and_change(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    png = _paletted_png_bytes()

    class Resp:
        status_code = 200
        content = png
        headers = {}

    monkeypatch.setattr("wwpppp.ingest.requests.get", lambda *a, **k: Resp())

    # create existing identical cache -> no change
    cache_path = tmp_path / "tile-0_0.png"
    cache_path.write_bytes(png)
    assert not has_tile_changed(Tile(0, 0))

    # remove cache -> change detected and file created
    cache_path.unlink()
    assert has_tile_changed(Tile(0, 0))
    assert cache_path.exists()


def test_has_tile_changed_sets_mtime_from_last_modified(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    png = _paletted_png_bytes()

    class Resp:
        status_code = 200
        content = png
        headers = {"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"}

    monkeypatch.setattr("wwpppp.ingest.requests.get", lambda *a, **k: Resp())

    cache_path = tmp_path / "tile-0_0.png"
    assert has_tile_changed(Tile(0, 0))
    assert cache_path.exists()

    # Verify mtime was set to the Last-Modified timestamp (1700052326)
    import os

    stat = os.stat(cache_path)
    assert int(stat.st_mtime) == 1700052326


def test_has_tile_changed_handles_missing_last_modified(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    png = _paletted_png_bytes()

    class Resp:
        status_code = 200
        content = png
        headers = {}  # No Last-Modified header

    monkeypatch.setattr("wwpppp.ingest.requests.get", lambda *a, **k: Resp())

    cache_path = tmp_path / "tile-0_0.png"
    assert has_tile_changed(Tile(0, 0))
    assert cache_path.exists()
    # Should still create file successfully even without Last-Modified


def test_has_tile_changed_handles_invalid_last_modified(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    png = _paletted_png_bytes()

    class Resp:
        status_code = 200
        content = png
        headers = {"Last-Modified": "invalid-date-format"}

    monkeypatch.setattr("wwpppp.ingest.requests.get", lambda *a, **k: Resp())

    cache_path = tmp_path / "tile-0_0.png"
    assert has_tile_changed(Tile(0, 0))
    assert cache_path.exists()
    # Should still create file successfully even with invalid Last-Modified


def test_has_tile_changed_304_not_modified(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    png = _paletted_png_bytes()

    class Resp:
        status_code = 304
        content = b""
        headers = {}

    call_args = []

    def mock_get(*a, **k):
        call_args.append((a, k))
        return Resp()

    monkeypatch.setattr("wwpppp.ingest.requests.get", mock_get)

    # Create existing cache file
    cache_path = tmp_path / "tile-0_0.png"
    cache_path.write_bytes(png)

    # Should return False (no change) on 304
    assert not has_tile_changed(Tile(0, 0))

    # Verify If-Modified-Since header was sent
    assert len(call_args) == 1
    assert "headers" in call_args[0][1]
    assert "If-Modified-Since" in call_args[0][1]["headers"]


def test_has_tile_changed_sends_if_modified_since_when_cache_exists(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    png = _paletted_png_bytes()

    class Resp:
        status_code = 200
        content = png
        headers = {}

    call_args = []

    def mock_get(*a, **k):
        call_args.append((a, k))
        return Resp()

    monkeypatch.setattr("wwpppp.ingest.requests.get", mock_get)

    # Create existing cache file
    cache_path = tmp_path / "tile-0_0.png"
    cache_path.write_bytes(png)

    has_tile_changed(Tile(0, 0))

    # Verify If-Modified-Since header was sent
    assert len(call_args) == 1
    assert "headers" in call_args[0][1]
    assert "If-Modified-Since" in call_args[0][1]["headers"]


def test_has_tile_changed_no_if_modified_since_when_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    png = _paletted_png_bytes()

    class Resp:
        status_code = 200
        content = png
        headers = {}

    call_args = []

    def mock_get(*a, **k):
        call_args.append((a, k))
        return Resp()

    monkeypatch.setattr("wwpppp.ingest.requests.get", mock_get)

    # No cache file exists
    has_tile_changed(Tile(0, 0))

    # Verify If-Modified-Since header was NOT sent (or headers is empty dict)
    assert len(call_args) == 1
    headers = call_args[0][1].get("headers", {})
    assert "If-Modified-Since" not in headers


def test_stitch_tiles_pastes_cached_tiles(monkeypatch, tmp_path):
    monkeypatch.setattr("wwpppp.ingest.DIRS", SimpleNamespace(user_cache_path=tmp_path, user_pictures_path=tmp_path))
    # create two tile cache files at (0,0) and (1,0)
    png_a = _paletted_png_bytes((1000, 1000), [1] * (1000 * 1000))
    png_b = _paletted_png_bytes((1000, 1000), [2] * (1000 * 1000))
    (tmp_path / "tile-0_0.png").write_bytes(png_a)
    (tmp_path / "tile-1_0.png").write_bytes(png_b)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    stitched = stitch_tiles(rect)
    assert stitched.size == rect.size
    # check that some pixels are non-zero indicating pasted content
    data = stitched.get_flattened_data()
    assert any(p for p in data)
