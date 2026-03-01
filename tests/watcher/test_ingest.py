"""Tests for tile fetching, caching, and conditional requests."""

import io
from unittest.mock import AsyncMock, patch

import httpx

from pixel_hawk.models.painters import Painter
from pixel_hawk.watcher.ingest import TileChecker
from pixel_hawk.models.entities import Person, ProjectInfo, ProjectState, TileInfo, TileProject
from pixel_hawk.models.geometry import Point, Rectangle, Size
from pixel_hawk.models.palette import PALETTE


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
    """Create a TileChecker and inject a mock client."""
    checker = TileChecker()
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


# --- TileChecker ---


async def _create_project_with_tile(x: int, y: int, *, state: ProjectState = ProjectState.ACTIVE) -> ProjectInfo:
    """Create a Person, ProjectInfo, and TileProject linking to a tile at (x, y).

    Also creates the TileInfo if it doesn't exist yet.
    """
    person = await Person.create(name=f"tester-{x}-{y}")
    info = ProjectInfo(
        owner=person,
        name=f"project-{x}-{y}",
        state=state,
        x=x * 1000,
        y=y * 1000,
        width=1000,
        height=1000,
        first_seen=1000,
    )
    await info.save_as_new()
    tile_id = TileInfo.tile_id(x, y)
    tile_info, _ = await TileInfo.get_or_create(
        id=tile_id,
        defaults={"x": x, "y": y, "heat": 999, "last_checked": 0, "last_update": 0},
    )
    await TileProject.create(tile=tile_info, project=info)
    return info


async def test_check_next_tile_no_tiles():
    """check_next_tile returns immediately when no tiles in queue."""
    checker = TileChecker()
    await checker.check_next_tile()  # Should not raise
    await checker.close()


async def test_check_next_tile_no_tile_selected():
    """check_next_tile logs warning when QueueSystem has no tiles to select."""
    checker = TileChecker()
    # No TileInfo in DB, so select_next_tile returns None
    await checker.check_next_tile()
    await checker.close()


async def test_check_next_tile_changed_calls_run_diff(setup_config):
    """When tile has changed, run_diff is called on affected projects."""
    await _create_project_with_tile(0, 0)

    checker = TileChecker()
    png = _paletted_png_bytes()
    checker.client = MockClient(
        httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"})
    )

    mock_run_diff = AsyncMock()
    with patch("pixel_hawk.watcher.projects.Project.run_diff", mock_run_diff):
        await checker.check_next_tile()

    mock_run_diff.assert_called_once_with()
    await checker.close()


async def test_check_next_tile_unchanged_calls_run_nochange(setup_config):
    """When tile is unchanged (304), run_nochange is called on affected projects."""
    await _create_project_with_tile(0, 0)
    # Move tile out of burning queue so it's selectable as a temp tile
    tile_info = await TileInfo.get(id=TileInfo.tile_id(0, 0))
    tile_info.last_update = 1700052326
    tile_info.last_checked = 100
    tile_info.heat = 1
    await tile_info.save()

    checker = TileChecker()
    await checker.start()
    checker.client = MockClient(httpx.Response(304))

    mock_run_nochange = AsyncMock()
    with patch("pixel_hawk.watcher.projects.Project.run_nochange", mock_run_nochange):
        await checker.check_next_tile()

    mock_run_nochange.assert_called_once()
    await checker.close()


async def test_check_next_tile_unchanged_returns_projects(setup_config):
    """When tile is unchanged, check_next_tile still returns affected Projects for watch updates."""
    info = await _create_project_with_tile(0, 0)
    tile_info = await TileInfo.get(id=TileInfo.tile_id(0, 0))
    tile_info.last_update = 1700052326
    tile_info.last_checked = 100
    tile_info.heat = 1
    await tile_info.save()

    checker = TileChecker()
    await checker.start()
    checker.client = MockClient(httpx.Response(304))

    with patch("pixel_hawk.watcher.projects.Project.run_nochange", AsyncMock()):
        result = await checker.check_next_tile()

    assert len(result) == 1
    assert result[0].info.id == info.id
    await checker.close()


async def test_check_next_tile_skips_inactive_projects(setup_config):
    """Inactive projects are not diffed even if linked to a changed tile."""
    await _create_project_with_tile(0, 0, state=ProjectState.INACTIVE)

    checker = TileChecker()
    png = _paletted_png_bytes()
    checker.client = MockClient(
        httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"})
    )

    mock_run_diff = AsyncMock()
    with patch("pixel_hawk.watcher.projects.Project.run_diff", mock_run_diff):
        await checker.check_next_tile()

    mock_run_diff.assert_not_called()
    await checker.close()


async def test_check_next_tile_includes_passive_projects(setup_config):
    """Passive projects are diffed when their tile changes."""
    await _create_project_with_tile(0, 0, state=ProjectState.PASSIVE)

    checker = TileChecker()
    png = _paletted_png_bytes()
    checker.client = MockClient(
        httpx.Response(200, content=png, headers={"Last-Modified": "Wed, 15 Nov 2023 12:45:26 GMT"})
    )

    mock_run_diff = AsyncMock()
    with patch("pixel_hawk.watcher.projects.Project.run_diff", mock_run_diff):
        await checker.check_next_tile()

    mock_run_diff.assert_called_once_with()
    await checker.close()


async def test_check_next_tile_updates_database(setup_config):
    """check_next_tile updates TileInfo in database after checking."""
    await _create_project_with_tile(0, 0)

    checker = TileChecker()
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

    with patch("pixel_hawk.watcher.projects.Project.run_diff", AsyncMock()):
        await checker.check_next_tile()

    # Verify TileInfo was updated
    tile_info = await TileInfo.get(id=TileInfo.tile_id(0, 0))
    assert tile_info.last_checked > 0
    assert tile_info.last_update == 1700052326
    assert tile_info.etag == '"new-etag"'
    await checker.close()


async def test_tile_checker_close():
    """close() shuts down the httpx client."""
    checker = TileChecker()
    await checker.close()
    assert checker.client.is_closed


# --- investigate_regression ---


def _make_project_with_regressed_indices(indices: list[int], *, rect: Rectangle | None = None) -> "Project":
    """Create a minimal Project mock with regressed_indices and rect set."""
    from pixel_hawk.watcher.projects import Project

    if rect is None:
        rect = Rectangle.from_point_size(Point(5000, 7000), Size(100, 100))

    proj = object.__new__(Project)
    proj.regressed_indices = indices
    proj.grief_report = None
    proj.rect = rect

    # Minimal info stub for logging
    class _Info:
        class owner:
            name = "tester"

        name = "test-project"

    proj.info = _Info()
    return proj


async def test_investigate_regression_stops_after_4_hits():
    """Stops sampling once any single author reaches 4 occurrences."""
    painter_a = Painter(user_id=42, user_name="Griefer", alliance_name="Bad", discord_id="", discord_name="")

    call_count = 0

    async def mock_investigate(self, point):
        nonlocal call_count
        call_count += 1
        return painter_a

    checker = TileChecker()
    proj = _make_project_with_regressed_indices(list(range(200)))

    with patch.object(TileChecker, "investigate_pixel", mock_investigate):
        await checker.investigate_regression(proj)

    assert call_count == 4  # Stopped after 4 hits of same author
    assert proj.grief_report is not None
    assert proj.grief_report.regress_count == 200
    assert len(proj.grief_report.painters) == 1
    await checker.close()


async def test_investigate_regression_deduplicates_authors():
    """Each unique author is logged once, not per-pixel. GriefReport has deduplicated list."""
    alice = Painter(user_id=1, user_name="Alice", alliance_name="A", discord_id="", discord_name="")
    bob = Painter(user_id=2, user_name="Bob", alliance_name="B", discord_id="", discord_name="")
    # Alice hits 4 on the 7th call: A B A B A B A
    painters = [alice, bob, alice, bob, alice, bob, alice]
    call_idx = 0

    async def mock_investigate(self, point):
        nonlocal call_idx
        p = painters[call_idx]
        call_idx += 1
        return p

    checker = TileChecker()
    proj = _make_project_with_regressed_indices(list(range(200)))

    with patch.object(TileChecker, "investigate_pixel", mock_investigate):
        await checker.investigate_regression(proj)

    assert call_idx == 7  # Stopped when Alice hit 4
    assert proj.grief_report is not None
    assert len(proj.grief_report.painters) == 2  # Deduplicated: Alice and Bob
    await checker.close()


async def test_investigate_regression_maps_indices_to_canvas_points():
    """Flat indices are correctly converted to canvas Points using rect."""
    rect = Rectangle.from_point_size(Point(5000, 7000), Size(100, 100))
    # Index 0 -> (5000, 7000), Index 105 -> (5005, 7001)
    captured_points = []

    async def mock_investigate(self, point):
        captured_points.append(point)
        return Painter(user_id=1, user_name="X", alliance_name="", discord_id="", discord_name="")

    checker = TileChecker()
    proj = _make_project_with_regressed_indices([0, 105, 250])

    with patch.object(TileChecker, "investigate_pixel", mock_investigate):
        await checker.investigate_regression(proj)

    # Only 3 indices, single author doesn't reach threshold of 4 — all investigated
    assert Point(5000, 7000) in captured_points
    assert Point(5005, 7001) in captured_points
    assert Point(5050, 7002) in captured_points
    await checker.close()


async def test_investigate_regression_exhausts_all_indices():
    """If no author reaches 4 hits, all indices are investigated."""
    call_count = 0

    async def mock_investigate(self, point):
        nonlocal call_count
        call_count += 1
        # Each pixel has a unique author
        return Painter(user_id=call_count, user_name=f"User{call_count}", alliance_name="", discord_id="", discord_name="")

    checker = TileChecker()
    proj = _make_project_with_regressed_indices(list(range(5)))

    with patch.object(TileChecker, "investigate_pixel", mock_investigate):
        await checker.investigate_regression(proj)

    assert call_count == 5  # All indices checked, none hit 4
    await checker.close()
