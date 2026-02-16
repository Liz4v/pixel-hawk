"""Tests for tile fetching, caching, and conditional requests."""

import io
from unittest.mock import AsyncMock

import httpx

from pixel_hawk.geometry import Point, Rectangle, Size, Tile
from pixel_hawk.ingest import TileChecker, stitch_tiles
from pixel_hawk.models import TileInfo
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

    async def aclose(self):
        self.is_closed = True


async def _create_tile_info(x: int, y: int, *, last_update: int = 0, etag: str = "", last_checked: int = 0) -> TileInfo:
    """Create a TileInfo record in the database."""
    return await TileInfo.create(
        id=TileInfo.tile_id(x, y),
        x=x,
        y=y,
        heat=999 if last_checked == 0 else 1,
        last_checked=last_checked,
        last_update=last_update,
        etag=etag,
    )


# --- has_tile_changed ---


def _checker_with_client(client: MockClient) -> TileChecker:
    """Create a TileChecker with no projects and inject a mock client."""
    checker = TileChecker([])
    checker.client = client
    return checker


async def test_has_tile_changed_http_error():
    tile_info = await _create_tile_info(0, 0)
    checker = _checker_with_client(MockClient(httpx.Response(404)))
    assert not await checker.has_tile_changed(tile_info)
    assert tile_info.last_update == 0  # Unchanged on error
    assert tile_info.etag == ""
    assert tile_info.last_checked > 0  # Always updated
    await checker.close()


async def test_has_tile_changed_bad_image():
    tile_info = await _create_tile_info(0, 0)
    checker = _checker_with_client(
        MockClient(
            httpx.Response(200, content=b"not an image", headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"})
        )
    )
    assert not await checker.has_tile_changed(tile_info)
    # last_update/etag are mutated before decode, so they reflect the 200 response
    assert tile_info.last_update == 1700052326
    assert tile_info.etag == ""
    await checker.close()


async def test_has_tile_changed_network_exception():
    """Network exceptions are caught and preserve existing values."""
    tile_info = await _create_tile_info(0, 0, last_update=500, etag="old-etag")

    async def raise_exception(url, **kwargs):
        raise ConnectionError("Network unavailable")

    checker = _checker_with_client(MockClient(handler=raise_exception))
    assert not await checker.has_tile_changed(tile_info)
    assert tile_info.last_update == 500  # Preserved
    assert tile_info.etag == "old-etag"  # Preserved
    await checker.close()


async def test_has_tile_changed_success_with_last_modified(setup_config):
    png = _paletted_png_bytes()
    tile_info = await _create_tile_info(0, 0)
    checker = _checker_with_client(
        MockClient(httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"}))
    )

    assert await checker.has_tile_changed(tile_info)
    assert tile_info.last_update == 1700052326
    assert tile_info.last_checked > 0
    assert setup_config.tiles_dir.joinpath("tile-0_0.png").exists()
    await checker.close()


async def test_has_tile_changed_missing_last_modified(setup_config):
    """Missing Last-Modified header falls back to current time."""
    png = _paletted_png_bytes()
    tile_info = await _create_tile_info(0, 0)
    checker = _checker_with_client(MockClient(httpx.Response(200, content=png, headers={})))

    assert await checker.has_tile_changed(tile_info)
    assert tile_info.last_update > 0  # Fallback to current time
    await checker.close()


async def test_has_tile_changed_invalid_last_modified(setup_config):
    """Invalid Last-Modified header falls back to current time."""
    png = _paletted_png_bytes()
    tile_info = await _create_tile_info(0, 0)
    checker = _checker_with_client(
        MockClient(httpx.Response(200, content=png, headers={"Last-Modified": "invalid-date-format"}))
    )

    assert await checker.has_tile_changed(tile_info)
    assert tile_info.last_update > 0  # Fallback to current time
    await checker.close()


async def test_has_tile_changed_returns_etag(setup_config):
    """ETag from response is stored on tile_info."""
    png = _paletted_png_bytes()
    tile_info = await _create_tile_info(0, 0)
    checker = _checker_with_client(
        MockClient(
            httpx.Response(
                200,
                content=png,
                headers={
                    "Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT",
                    "ETag": '"abc123"',
                },
            )
        )
    )

    assert await checker.has_tile_changed(tile_info)
    assert tile_info.etag == '"abc123"'
    await checker.close()


async def test_has_tile_changed_304_not_modified():
    """304 preserves existing tile_info values."""
    tile_info = await _create_tile_info(0, 0, last_update=1700052326, etag='"old"')
    checker = _checker_with_client(MockClient(httpx.Response(304)))

    assert not await checker.has_tile_changed(tile_info)
    assert tile_info.last_update == 1700052326  # Preserved
    assert tile_info.etag == '"old"'  # Preserved
    await checker.close()


async def test_has_tile_changed_sends_if_modified_since():
    """If-Modified-Since header is sent when tile_info has last_update."""
    tile_info = await _create_tile_info(0, 0, last_update=1700052326)
    client = MockClient(httpx.Response(304))
    checker = _checker_with_client(client)

    await checker.has_tile_changed(tile_info)

    assert len(client.calls) == 1
    headers = client.calls[0][1].get("headers", {})
    assert "If-Modified-Since" in headers
    await checker.close()


async def test_has_tile_changed_sends_if_none_match():
    """If-None-Match header is sent when tile_info has etag."""
    tile_info = await _create_tile_info(0, 0, etag='"abc"')
    client = MockClient(httpx.Response(304))
    checker = _checker_with_client(client)

    await checker.has_tile_changed(tile_info)

    assert len(client.calls) == 1
    headers = client.calls[0][1].get("headers", {})
    assert headers.get("If-None-Match") == '"abc"'
    await checker.close()


async def test_has_tile_changed_no_conditional_headers_when_fresh():
    """No conditional headers sent when tile_info has no cached state."""
    png = _paletted_png_bytes()
    tile_info = await _create_tile_info(0, 0)  # last_update=0, etag=""
    client = MockClient(httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"}))
    checker = _checker_with_client(client)

    await checker.has_tile_changed(tile_info)

    headers = client.calls[0][1].get("headers", {})
    assert "If-Modified-Since" not in headers
    assert "If-None-Match" not in headers
    await checker.close()


# --- stitch_tiles ---


async def test_stitch_tiles_missing_tile_logs_and_skips(setup_config):
    """Missing cache tiles are skipped with transparent pixels."""
    # Only create one of two needed tiles
    png_a = _paletted_png_bytes((1000, 1000), [1] * (1000 * 1000))
    (setup_config.tiles_dir / "tile-0_0.png").write_bytes(png_a)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    stitched = await stitch_tiles(rect)
    assert stitched.size == rect.size


async def test_stitch_tiles_pastes_cached_tiles(setup_config):
    png_a = _paletted_png_bytes((1000, 1000), [1] * (1000 * 1000))
    png_b = _paletted_png_bytes((1000, 1000), [2] * (1000 * 1000))
    (setup_config.tiles_dir / "tile-0_0.png").write_bytes(png_a)
    (setup_config.tiles_dir / "tile-1_0.png").write_bytes(png_b)

    rect = Rectangle.from_point_size(Point(0, 0), Size(2000, 1000))
    stitched = await stitch_tiles(rect)
    assert stitched.size == rect.size
    data = stitched.get_flattened_data()
    assert any(p for p in data)


# --- TileChecker ---


class MockProject:
    """Hashable mock Project for TileChecker tests."""

    def __init__(self, rect: Rectangle):
        self.rect = rect
        self.run_diff = AsyncMock()
        self.run_nochange = AsyncMock()


async def test_tile_checker_init_indexes_tiles():
    """TileChecker builds tile→projects index from projects."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))  # 1 tile: (0,0)
    proj = MockProject(rect)

    checker = TileChecker([proj])
    assert Tile(0, 0) in checker.tiles
    assert proj in checker.tiles[Tile(0, 0)]
    await checker.close()


async def test_tile_checker_init_multiple_projects_same_tile():
    """Multiple projects sharing a tile are both indexed."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj1 = MockProject(rect)
    proj2 = MockProject(rect)

    checker = TileChecker([proj1, proj2])
    assert len(checker.tiles[Tile(0, 0)]) == 2
    await checker.close()


async def test_check_next_tile_no_tiles():
    """check_next_tile returns immediately when no tiles are indexed."""
    checker = TileChecker([])
    await checker.check_next_tile()  # Should not raise
    await checker.close()


async def test_check_next_tile_no_tile_selected():
    """check_next_tile returns when QueueSystem has no tiles to select."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = MockProject(rect)

    checker = TileChecker([proj])
    # No TileInfo in DB, so select_next_tile returns None
    await checker.check_next_tile()
    proj.run_diff.assert_not_called()
    proj.run_nochange.assert_not_called()
    await checker.close()


async def test_check_next_tile_changed_calls_run_diff(setup_config):
    """When tile has changed, run_diff is called on affected projects."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = MockProject(rect)

    checker = TileChecker([proj])
    await _create_tile_info(0, 0)

    # Mock client to return a changed tile
    png = _paletted_png_bytes()
    checker.client = MockClient(
        httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"})
    )

    await checker.check_next_tile()
    proj.run_diff.assert_called_once()
    proj.run_nochange.assert_not_called()
    await checker.close()


async def test_check_next_tile_unchanged_calls_run_nochange(setup_config):
    """When tile is unchanged (304), run_nochange is called on affected projects."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = MockProject(rect)

    checker = TileChecker([proj])
    await _create_tile_info(0, 0, last_update=1700052326, last_checked=100)

    # Initialize queue system from DB so it discovers the temp queue
    await checker.start()

    # Mock client to return 304
    checker.client = MockClient(httpx.Response(304))

    # Burning queue is empty; select_next_tile skips it and finds temp queue 1
    await checker.check_next_tile()
    proj.run_nochange.assert_called_once()
    proj.run_diff.assert_not_called()
    await checker.close()


async def test_check_next_tile_updates_database(setup_config):
    """check_next_tile updates TileInfo in database after checking."""
    rect = Rectangle.from_point_size(Point(0, 0), Size(1000, 1000))
    proj = MockProject(rect)

    checker = TileChecker([proj])
    await _create_tile_info(0, 0)

    png = _paletted_png_bytes()
    checker.client = MockClient(
        httpx.Response(
            200,
            content=png,
            headers={
                "Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT",
                "ETag": '"new-etag"',
            },
        )
    )

    await checker.check_next_tile()

    # Verify TileInfo was updated
    tile_info = await TileInfo.get(id=TileInfo.tile_id(0, 0))
    assert tile_info.last_checked > 0
    assert tile_info.last_update == 1700052326
    assert tile_info.etag == '"new-etag"'
    await checker.close()


async def test_tile_checker_close():
    """close() shuts down the httpx client."""
    checker = TileChecker([])
    await checker.close()
    assert checker.client.is_closed
