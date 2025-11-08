import functools
import typing


class Point(typing.NamedTuple):
    x: int = 0
    y: int = 0

    @classmethod
    def from4(cls, tx: int, ty: int, px: int, py: int) -> "Point":
        return cls(tx * 1000 + px, ty * 1000 + py)

    def to4(self) -> tuple[int, int, int, int]:
        tx, px = divmod(self.x, 1000)
        ty, py = divmod(self.y, 1000)
        return tx, ty, px, py

    def __str__(self) -> str:
        return "_".join(map(str, self.to4()))

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)


class Size(typing.NamedTuple):
    w: int = 0
    h: int = 0

    def __str__(self) -> str:
        return f"{self.w}x{self.h}"

    def __bool__(self) -> bool:
        return self.w > 0 and self.h > 0


class Rectangle(typing.NamedTuple):
    point: Point = Point()
    size: Size = Size()

    def __str__(self):
        return f"{self.point}-{self.size}"

    def __bool__(self) -> bool:
        return bool(self.size)

    def __contains__(self, other: "Rectangle") -> bool:
        return (
            self.point.x <= other.point.x
            and other.point.x + other.size.w <= self.point.x + self.size.w
            and self.point.y <= other.point.y
            and other.point.y + other.size.h <= self.point.y + self.size.h
        )

    def __sub__(self, other: Point) -> "Rectangle":
        return Rectangle(self.point - other, self.size)

    def __mul__(self, other: "Rectangle") -> "Rectangle":
        """Intersection of two rectangles"""
        x1 = max(self.point.x, other.point.x)
        y1 = max(self.point.y, other.point.y)
        x2 = min(self.point.x + self.size.w, other.point.x + other.size.w)
        y2 = min(self.point.y + self.size.h, other.point.y + other.size.h)
        if x2 <= x1 or y2 <= y1:
            return Rectangle(Point(0, 0), Size(0, 0))
        return Rectangle(Point(x1, y1), Size(x2 - x1, y2 - y1))

    @property
    @functools.cache
    def box(self) -> tuple[int, int, int, int]:
        return (self.point.x, self.point.y, self.point.x + self.size.w, self.point.y + self.size.h)

    @property
    @functools.cache
    def tiles(self) -> frozenset[tuple[int, int]]:
        x_start = self.point.x // 1000
        x_end = (self.point.x + self.size.w - 1) // 1000
        y_start = self.point.y // 1000
        y_end = (self.point.y + self.size.h - 1) // 1000
        return frozenset((tx, ty) for tx in range(x_start, x_end + 1) for ty in range(y_start, y_end + 1))
