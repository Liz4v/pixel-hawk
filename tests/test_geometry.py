import pytest

from pixel_hawk.geometry import GeoPoint, Point, Rectangle, Size, Tile


def test_point_from4_and_to4():
    p = Point.from4(1, 2, 3, 4)
    assert p.x == 1003 and p.y == 2004
    assert p.to4() == (1, 2, 3, 4)


def test_point_str_and_to_point():
    t = Tile(2, 3)
    assert str(t) == "2_3"
    pt = t.to_point(10, 20)
    assert pt == Point(2010, 3020)
    assert str(Point.from4(2, 3, 10, 20)) == "2_3_10_20"


def test_point_from4_assertions():
    with pytest.raises(AssertionError):
        Point.from4(-1, 0, 0, 0)
    with pytest.raises(AssertionError):
        Point.from4(0, 0, 1000, 0)


def test_point_subtraction():
    a = Point(1500, 2500)
    b = Point(500, 1000)
    c = a - b
    assert c == Point(1000, 1500)


def test_size_and_rectangle_tiles():
    size = Size(1500, 2000)
    assert bool(size)
    rect = Rectangle.from_point_size(Point(500, 500), size)
    # tiles should cover tiles for tx in {0,1} and ty in {0,1,2}
    tiles = rect.tiles
    assert Tile(0, 0) in tiles
    assert Tile(1, 2) in tiles
    assert len(tiles) == 6


def test_rectangle_properties_and_ops():
    p = Point(0, 0)
    s = Size(100, 200)
    r = Rectangle.from_point_size(p, s)
    assert r.point == p
    assert r.size == s
    assert "100x200" in str(r)
    assert r
    # subtraction by point
    r2 = r - Point(10, 20)
    assert r2.left == -10 and r2.top == -20
    # empty rectangle
    r_empty = Rectangle(0, 0, 0, 0)
    assert not r_empty


GEO_EXAMPLES = [
    # Known locations
    (Point(1024000, 1024000), GeoPoint(0.0, 0.0)),
    # Eyeballed points
    (Point(573355, 747984), GeoPoint(43.582364791630496, -79.21485384697264)),
    (Point(733393, 1023987), GeoPoint(0.0021975969721476077, -51.08317415947268)),
    (Point(2006342, 1299716), GeoPoint(-43.54427966409443, 172.67739224677734)),
    (Point(558527, 2047999), GeoPoint(-85.05112116917313, -81.82133822197264)),
]


@pytest.mark.parametrize("pixel, geo", GEO_EXAMPLES)
def test_geopoint_from_pixel(pixel: Point, geo: GeoPoint):
    result = GeoPoint.from_pixel(pixel.x, pixel.y)
    assert abs(result.latitude - geo.latitude) < 0.001
    assert abs(result.longitude - geo.longitude) < 0.001


@pytest.mark.parametrize("pixel, geo", GEO_EXAMPLES)
def test_geopoint_to_pixel(pixel: Point, geo: GeoPoint):
    result = geo.to_pixel()
    assert abs(result.x - pixel.x) <= 1
    assert abs(result.y - pixel.y) <= 1


def test_geo_round_trip():
    """Point -> GeoPoint -> Point should round-trip within 1 pixel."""
    for pt, _ in GEO_EXAMPLES:
        recovered = GeoPoint.from_pixel(pt.x, pt.y).to_pixel()
        assert abs(recovered.x - pt.x) <= 1
        assert abs(recovered.y - pt.y) <= 1


def test_geo_round_trip_reverse():
    """GeoPoint -> Point -> GeoPoint should round-trip within tight tolerance."""
    for _, geo in GEO_EXAMPLES:
        px = geo.to_pixel()
        recovered = GeoPoint.from_pixel(px.x, px.y)
        assert abs(recovered.latitude - geo.latitude) < 0.0001
        assert abs(recovered.longitude - geo.longitude) < 0.0001


def test_geopoint_from_pixel_float():
    """from_pixel should accept float coordinates (for rectangle centers)."""
    geo_int = GeoPoint.from_pixel(1024000, 1024000)
    geo_float = GeoPoint.from_pixel(1024000.5, 1024000.5)
    assert abs(geo_int.latitude - geo_float.latitude) < 0.0001
    assert abs(geo_int.longitude - geo_float.longitude) < 0.0001


@pytest.mark.parametrize(
    "size, expected_zoom",
    [
        (Size(2197, 1), 11.093),
        (Size(398, 1), 13.558),
        (Size(18, 1), 18.025),
        (Size(5, 1), 19.873),
    ],
)
def test_size_to_zoom(size: Size, expected_zoom: float):
    result = size.to_zoom(600)
    assert abs(result - expected_zoom) < 0.01


def test_size_to_zoom_uses_longest_side():
    assert Size(100, 50).to_zoom(600) == Size(100, 1).to_zoom(600)
    assert Size(50, 100).to_zoom(600) == Size(1, 100).to_zoom(600)


def test_size_to_zoom_monotonic():
    """Larger sizes should produce smaller (more zoomed out) zoom levels."""
    zooms = [Size(s, 1).to_zoom(600) for s in [5, 18, 398, 2197]]
    assert zooms == sorted(zooms, reverse=True)


def test_size_to_zoom_viewport_scales():
    """Doubling viewport size should add 1 to zoom level."""
    z1 = Size(100, 100).to_zoom(300)
    z2 = Size(100, 100).to_zoom(600)
    assert abs((z2 - z1) - 1.0) < 0.001


def test_zoom_wont_fail_on_empty_size():
    """There will be no division by zero on an empty size."""
    z = Size(0, 0).to_zoom(600)
    assert 10 <= z <= 22


def test_rectangle_to_link():
    rect = Rectangle.from_point_size(Point(1024000, 1024000), Size(100, 200))
    link = rect.to_link()
    assert link.startswith("https://wplace.live/?")
    assert "lat=" in link
    assert "lng=" in link
    assert "zoom=" in link


def test_rectangle_to_link_center():
    """Link should point to the center of the rectangle."""
    rect = Rectangle(1024000, 1024000, 1024100, 1024200)
    link = rect.to_link()
    # Center is (1024050, 1024100) -> very close to (0, 0) geo
    assert "lat=-0.0" in link or "lat=0.0" in link
    assert "lng=" in link
