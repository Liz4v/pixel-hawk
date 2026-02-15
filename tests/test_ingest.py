import io

import httpx

from pixel_hawk.geometry import Point, Rectangle, Size, Tile
from pixel_hawk.ingest import has_tile_changed, stitch_tiles
from pixel_hawk.palette import PALETTE


def _paletted_png_bytes(size=(1, 1), data=(0,)):
    im = PALETTE.new(size)
    im.putdata(list(data))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class MockClient:
    """Mock httpx.AsyncClient that returns a preset response."""

    def __init__(self, response=None, handler=None):
        self.response = response
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.handler:
            return await self.handler(url, **kwargs)
        return self.response


async def test_has_tile_changed_http_error(tmp_path):
    client = MockClient(httpx.Response(404))
    changed, last_modified = await has_tile_changed(Tile(0, 0), client)
    assert not changed
    assert last_modified == 0


async def test_has_tile_changed_bad_image(tmp_path):
    client = MockClient(
        httpx.Response(200, content=b"not an image", headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"})
    )
    changed, last_modified = await has_tile_changed(Tile(0, 0), client)
    assert not changed
    assert last_modified == 0


async def test_has_tile_changed_network_exception(tmp_path):
    """Test that network exceptions are caught and return (False, 0)."""

    async def raise_exception(url, **kwargs):
        raise ConnectionError("Network unavailable")

    client = MockClient(handler=raise_exception)
    changed, last_modified = await has_tile_changed(Tile(0, 0), client)
    assert not changed
    assert last_modified == 0


async def test_has_tile_changed_sets_mtime_from_last_modified(setup_config):
    png = _paletted_png_bytes()
    client = MockClient(httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"}))

    cache_path = setup_config.tiles_dir / "tile-0_0.png"
    changed, last_modified = await has_tile_changed(Tile(0, 0), client)
    assert changed
    assert cache_path.exists()
    assert last_modified == 1700052326

    # Verify mtime was set to the Last-Modified timestamp (1700052326)
    import os

    stat = os.stat(cache_path)
    assert int(stat.st_mtime) == 1700052326


async def test_has_tile_changed_handles_missing_last_modified(setup_config):
    """Test that missing Last-Modified header falls back to current time."""
    png = _paletted_png_bytes()
    client = MockClient(httpx.Response(200, content=png, headers={}))

    cache_path = setup_config.tiles_dir / "tile-0_0.png"
    changed, last_modified = await has_tile_changed(Tile(0, 0), client)
    assert changed
    assert last_modified > 0  # Fallback to current time
    assert cache_path.exists()  # Cache file created with fallback timestamp


async def test_has_tile_changed_handles_invalid_last_modified(setup_config):
    """Test that invalid Last-Modified header falls back to current time."""
    png = _paletted_png_bytes()
    client = MockClient(httpx.Response(200, content=png, headers={"Last-Modified": "invalid-date-format"}))

    cache_path = setup_config.tiles_dir / "tile-0_0.png"
    changed, last_modified = await has_tile_changed(Tile(0, 0), client)
    assert changed
    assert last_modified > 0  # Fallback to current time
    assert cache_path.exists()  # Cache file created with fallback timestamp


async def test_has_tile_changed_304_not_modified(setup_config):
    png = _paletted_png_bytes()
    client = MockClient(httpx.Response(304))

    # Create existing cache file
    cache_path = setup_config.tiles_dir / "tile-0_0.png"
    cache_path.write_bytes(png)

    # Should return False (no change) on 304
    changed, last_modified = await has_tile_changed(Tile(0, 0), client)
    assert not changed
    assert last_modified > 0  # Should return cache file mtime

    # Verify If-Modified-Since header was sent
    assert len(client.calls) == 1
    assert "If-Modified-Since" in client.calls[0][1].get("headers", {})


async def test_has_tile_changed_sends_if_modified_since_when_cache_exists(setup_config):
    png = _paletted_png_bytes()
    client = MockClient(httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"}))

    # Create existing cache file
    cache_path = setup_config.tiles_dir / "tile-0_0.png"
    cache_path.write_bytes(png)

    await has_tile_changed(Tile(0, 0), client)

    # Verify If-Modified-Since header was sent
    assert len(client.calls) == 1
    assert "If-Modified-Since" in client.calls[0][1].get("headers", {})


async def test_has_tile_changed_no_if_modified_since_when_no_cache(setup_config):
    png = _paletted_png_bytes()
    client = MockClient(httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"}))

    # No cache file exists
    await has_tile_changed(Tile(0, 0), client)

    # Verify If-Modified-Since header was NOT sent (or headers is empty dict)
    assert len(client.calls) == 1
    headers = client.calls[0][1].get("headers", {})
    assert "If-Modified-Since" not in headers


async def test_stitch_tiles_pastes_cached_tiles(setup_config):
    # create two tile cache files at (0,0) and (1,0)
    png_a = _paletted_png_bytes((1000, 1000), [1] * (1000 * 1000))
    png_b = _paletted_png_bytes((1000, 1000), [2] * (1000 * 1000))
    (setup_config.tiles_dir / "tile-0_0.png").write_bytes(png_a)
    (setup_config.tiles_dir / "tile-1_0.png").write_bytes(png_b)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    stitched = await stitch_tiles(rect)
    assert stitched.size == rect.size
    # check that some pixels are non-zero indicating pasted content
    data = stitched.get_flattened_data()
    assert any(p for p in data)
