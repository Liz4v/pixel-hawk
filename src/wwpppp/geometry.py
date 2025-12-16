from functools import cache
from typing import NamedTuple


class Point(NamedTuple):
    """Represents a point in 2D lattice space."""

    x: int = 0
    y: int = 0

    @classmethod
    def from4(cls, tx: int, ty: int, px: int, py: int) -> "Point":
        """Create a Point from (tx, ty, px, py) tuple as represented in project file names."""
        assert min(tx, ty, px, py) >= 0, "Tile and pixel coordinates must be non-negative"
        assert max(px, py) < 1000, "Pixel coordinates must be less than 1000"
        return cls(tx * 1000 + px, ty * 1000 + py)

    def to4(self) -> tuple[int, int, int, int]:
        """Convert to (tx, ty, px, py) tuple, as represented in project file names."""
        tx, px = divmod(self.x, 1000)
        ty, py = divmod(self.y, 1000)
        return tx, ty, px, py

    def __str__(self) -> str:
        return "_".join(map(str, self.to4()))

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: int) -> "Point":
        return Point(self.x * scalar, self.y * scalar)


class Size(NamedTuple):
    """Represents a size in 2D lattice space."""

    w: int = 0
    h: int = 0

    def __str__(self) -> str:
        return f"{self.w}x{self.h}"

    def __bool__(self) -> bool:
        """Non-empty size."""
        return self.w > 0 and self.h > 0


class Rectangle(NamedTuple):
    """Represents a rectangle in 2D lattice space."""

    point: Point = Point()
    size: Size = Size()

    def __str__(self):
        return f"{self.size}-{self.point}"

    def __bool__(self) -> bool:
        """Non-empty rectangle."""
        return bool(self.size)

    def __contains__(self, other: "Rectangle") -> bool:
        """Check if this rectangle fully contains another rectangle."""
        return (
            self.point.x <= other.point.x
            and other.point.x + other.size.w <= self.point.x + self.size.w
            and self.point.y <= other.point.y
            and other.point.y + other.size.h <= self.point.y + self.size.h
        )

    def __sub__(self, other: Point) -> "Rectangle":
        """Offset rectangle by a point."""
        return Rectangle(self.point - other, self.size)

    @property
    @cache
    def pilbox(self) -> tuple[int, int, int, int]:
        """PIL box tuple for cropping: (left, upper, right, lower)."""
        return (self.point.x, self.point.y, self.point.x + self.size.w, self.point.y + self.size.h)

    @property
    @cache
    def tiles(self) -> frozenset[tuple[int, int]]:
        """Set of tile coordinates (tx, ty) covered by this rectangle."""
        x_start = self.point.x // 1000
        x_end = (self.point.x + self.size.w - 1) // 1000
        y_start = self.point.y // 1000
        y_end = (self.point.y + self.size.h - 1) // 1000
        return frozenset((tx, ty) for tx in range(x_start, x_end + 1) for ty in range(y_start, y_end + 1))
