"""Geometric primitives for tile math and coordinate conversion.

Provides immutable types for working with WPlace's coordinate system:
- Tile: 2048x2048 grid cells in the tile lattice, each containing 1000x1000 pixels
- Point: individual pixel coordinates in the canvas
- Size: width and height dimensions, with Web Mercator zoom level conversion
- Rectangle: axis-aligned rectangular regions with tile enumeration and wplace.live links
- GeoPoint: latitude/longitude coordinates with Web Mercator projection to/from pixel space

All types support conversion between tile space, pixel space, and geographic coordinates.
"""

from functools import cache
from math import asinh, atan, degrees, log2, pi, radians, sinh, tan
from typing import NamedTuple

CANVAS_SIZE = 2048 * 1000
ZOOM_FACTOR = log2(CANVAS_SIZE / 256)


class Tile(NamedTuple):
    """Represents a tile in 2D lattice space, each containing 1000x1000 pixels."""

    x: int = 0
    y: int = 0

    def __str__(self) -> str:
        return f"{self.x}_{self.y}"

    def to_point(self, px: int = 0, py: int = 0) -> Point:
        """Convert to a Point given pixel coordinates within the tile."""
        return Point(self.x * 1000 + px, self.y * 1000 + py)


class Point(NamedTuple):
    """Represents a pixel point in 2D lattice space.
    Tile information is implicit in the coordinates (every 1000 pixels corresponds to a tile)."""

    x: int = 0
    y: int = 0

    @classmethod
    def from4(cls, tx: int, ty: int, px: int, py: int) -> Point:
        """Create a Point from (tx, ty, px, py) tuple as represented in project file names."""
        assert min(tx, ty, px, py) >= 0, "Tile and pixel coordinates must be non-negative"
        assert max(px, py) < 1000, "Pixel coordinates must be less than 1000"
        assert max(tx, ty) < 2048, "Tile coordinates must be less than 2048"
        return cls(tx * 1000 + px, ty * 1000 + py)

    def to4(self) -> tuple[int, int, int, int]:
        """Convert to (tx, ty, px, py) tuple, as represented in project file names."""
        tx, px = divmod(self.x, 1000)
        ty, py = divmod(self.y, 1000)
        return tx, ty, px, py

    def __str__(self) -> str:
        return "_".join(map(str, self.to4()))

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y)


class Size(NamedTuple):
    """Represents a pixel size in 2D lattice space."""

    w: int = 0
    h: int = 0

    def __str__(self) -> str:
        return f"{self.w}x{self.h}"

    def __bool__(self) -> bool:
        """Non-empty size."""
        return self.w != 0 and self.h != 0

    def to_zoom(self, viewport_size: float) -> float:
        """Convert to a Web Mercator zoom level that will display a piece of this size."""
        # zoom = log2((canvas_size / subject_size) * (viewport_size / 256))
        return ZOOM_FACTOR + log2(viewport_size / max(5, *self))


class Rectangle(NamedTuple):
    """Represents a pixel rectangle in 2D lattice space. Uses PIL-style coordinates."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    @cache
    def point(self) -> Point:
        """Top-left point of the rectangle."""
        return Point(min(self.left, self.right), min(self.top, self.bottom))

    @property
    @cache
    def size(self) -> Size:
        """Size of the rectangle."""
        return Size(abs(self.right - self.left), abs(self.bottom - self.top))

    @classmethod
    def from_point_size(cls, point: Point, size: Size) -> Rectangle:
        """Create a Rectangle from a top-left point and size."""
        return cls(point.x, point.y, point.x + size.w, point.y + size.h)

    def __str__(self):
        return f"{self.size}-{self.point}"

    def __bool__(self) -> bool:
        """Non-empty rectangle."""
        return self.left != self.right and self.top != self.bottom

    def __sub__(self, other: Point) -> Rectangle:
        """Offset rectangle by a point."""
        return Rectangle(self.left - other.x, self.top - other.y, self.right - other.x, self.bottom - other.y)

    @property
    @cache
    def tiles(self) -> frozenset[Tile]:
        """Set of tile coordinates (tx, ty) covered by this rectangle."""
        left = self.left // 1000
        top = self.top // 1000
        right = (self.right + 999) // 1000
        bottom = (self.bottom + 999) // 1000
        return frozenset(Tile(tx, ty) for tx in range(left, right) for ty in range(top, bottom))

    def to_link(self, viewport_size: float = 300) -> str:
        """Converts to a wplace.live link to display the live contents of this rectangle."""
        geo = GeoPoint.from_pixel((self.left + self.right) / 2, (self.top + self.bottom) / 2)
        lat = round(geo.latitude, 6)
        lon = round(geo.longitude, 6)
        zoom = round(self.size.to_zoom(viewport_size), 3)
        return f"https://wplace.live/?lat={lat}&lng={lon}&zoom={zoom}"


class GeoPoint(NamedTuple):
    """Latitude/longitude coordinates with Web Mercator projection conversion."""

    latitude: float
    longitude: float

    @classmethod
    def from_pixel(cls, x: float, y: float) -> GeoPoint:
        """Inverse Web Mercator projection on a square pixel canvas."""
        longitude = x / CANVAS_SIZE * 360 - 180
        latitude = degrees(atan(sinh(pi * (1 - 2 * y / CANVAS_SIZE))))
        return cls(latitude, longitude)

    def to_pixel(self) -> Point:
        """Forward Web Mercator projection: geo coordinates to pixel coordinates."""
        x = (self.longitude + 180) / 360 * CANVAS_SIZE
        lat_rad = radians(self.latitude)
        y = (1 - asinh(tan(lat_rad)) / pi) / 2 * CANVAS_SIZE
        return Point(round(x), round(y))
