"""Tortoise ORM models for pixel-hawk persistence.

Person: Represents a person who can own projects.
ProjectInfo: Pure Tortoise ORM model for project metadata.
ProjectState: Enum for project state (active/passive/inactive).
HistoryChange: Per-diff event log recording pixel changes.
DiffStatus: Enum for project diff states.
TileInfo: Database-backed tile metadata (coordinates, timestamps, queue assignment, HTTP headers).
TileProject: Junction table for many-to-many tile-project relationships.
"""

import time
from enum import IntEnum, IntFlag

from tortoise import fields
from tortoise.models import Model

from .geometry import Point, Rectangle, Size, Tile


class DiffStatus(IntEnum):
    """Status of a project diff operation."""

    NOT_STARTED = 0
    IN_PROGRESS = 10
    COMPLETE = 20


class ProjectState(IntEnum):
    """State of a project for quota and monitoring purposes."""

    ACTIVE = 0  # Watched, counts towards quota
    PASSIVE = 10  # Checked if tile updates, doesn't count towards quota
    INACTIVE = 20  # Not checked, doesn't count towards quota


class BotAccess(IntFlag):
    """Bitmask for bot-level access control on a Person."""

    ADMIN = 0x10000000


class Person(Model):
    """Represents a person who can own projects."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=255)

    # Discord integration (nullable — not every Person has a Discord account)
    discord_id = fields.BigIntField(null=True, unique=True)
    access = fields.IntField(default=0)  # BotAccess bitmask

    # Calculated properties
    watched_tiles_count = fields.IntField(default=0)
    active_projects_count = fields.IntField(default=0)

    # Reverse relation (defined by ProjectInfo.owner FK with related_name="projects")
    projects: fields.ReverseRelation[ProjectInfo]

    async def update_totals(self) -> None:
        """Recalculate and save watched tiles and active projects count."""
        tiles = set()
        self.active_projects_count = 0
        # Only count active projects towards quota
        projects = await self.projects.filter(state=ProjectState.ACTIVE).all()
        for project in projects:
            self.active_projects_count += 1
            rect = project.rectangle
            tiles.update(rect.tiles)  # rect.tiles is a cached property (frozenset[Tile])
        self.watched_tiles_count = len(tiles)
        await self.save()

    class Meta(Model.Meta):
        table = "person"


class ProjectInfo(Model):
    """Persistent metadata for a project. Pure Tortoise ORM model."""

    # Primary key: auto-increment ID
    id = fields.IntField(primary_key=True)

    # Foreign key to Person (owner of this project)
    owner = fields.ForeignKeyField("models.Person", related_name="projects")

    # Project name (no longer in filename, only in database)
    name = fields.CharField(max_length=255, index=True)

    # Project state for quota control
    state = fields.IntEnumField(ProjectState, default=ProjectState.ACTIVE, index=True)

    # Project bounds
    x = fields.IntField(default=0)
    y = fields.IntField(default=0)
    width = fields.IntField(default=0)
    height = fields.IntField(default=0)

    # Timestamps (integer epoch seconds)
    first_seen = fields.IntField(default=0)
    last_check = fields.IntField(default=0)
    last_snapshot = fields.IntField(default=0)

    # Completion tracking
    max_completion_pixels = fields.IntField(default=0)
    max_completion_percent = fields.FloatField(default=0.0)
    max_completion_time = fields.IntField(default=0)

    # Lifetime counters
    total_progress = fields.IntField(default=0)
    total_regress = fields.IntField(default=0)

    # Largest regress event
    largest_regress_pixels = fields.IntField(default=0)
    largest_regress_time = fields.IntField(default=0)

    # Rate tracking
    recent_rate_pixels_per_hour = fields.FloatField(default=0.0)
    recent_rate_window_start = fields.IntField(default=0)

    # Tile updates (JSON columns for operational data)
    tile_last_update = fields.JSONField(default=dict)
    tile_updates_24h = fields.JSONField(default=list)

    # Cache state
    has_missing_tiles = fields.BooleanField(default=True)

    # Last log message
    last_log_message = fields.TextField(default="")

    # Incoming foreign keys
    tiles: fields.ReverseRelation[TileProject]

    @property
    def rectangle(self) -> Rectangle:
        return Rectangle.from_point_size(Point(self.x, self.y), Size(self.width, self.height))

    @property
    def filename(self) -> str:
        """Compute filename from coordinates (coordinates only, no name prefix)."""
        tx, ty, px, py = Point(self.x, self.y).to4()
        return f"{tx}_{ty}_{px}_{py}.png"

    @classmethod
    async def from_rect(
        cls, rect: Rectangle, owner_id: int, name: str, state: ProjectState = ProjectState.ACTIVE
    ) -> ProjectInfo:
        """Create and save a new ProjectInfo from project rectangle."""
        now = round(time.time())
        return await cls.create(
            owner_id=owner_id,
            name=name,
            state=state,
            x=rect.point.x,
            y=rect.point.y,
            width=rect.size.w,
            height=rect.size.h,
            first_seen=now,
            last_check=now,
        )

    @classmethod
    async def get_or_create_from_rect(cls, rect: Rectangle, owner_id: int, name: str) -> ProjectInfo:
        """Load existing ProjectInfo or create new from rectangle."""
        existing = await cls.filter(owner_id=owner_id, name=name).first()
        if existing:
            return existing
        return await cls.from_rect(rect, owner_id, name)

    class Meta(Model.Meta):
        table = "project"
        unique_together = (("owner_id", "name"),)  # Prevent duplicate names per person


class HistoryChange(Model):
    """Record of a single diff event for a project."""

    id = fields.IntField(primary_key=True)
    project = fields.ForeignKeyField("models.ProjectInfo", related_name="history_changes")
    timestamp = fields.IntField()

    # Status of this diff
    status = fields.IntEnumField(DiffStatus)

    # Pixel counts at time of diff
    num_remaining = fields.IntField(default=0)
    num_target = fields.IntField(default=0)
    completion_percent = fields.FloatField(default=0.0)

    # Change detected in this diff
    progress_pixels = fields.IntField(default=0)
    regress_pixels = fields.IntField(default=0)

    class Meta(Model.Meta):
        table = "history_change"
        ordering = ["-timestamp"]


class TileInfo(Model):
    """Persistent metadata for a single WPlace tile."""

    # Primary key: encoded from coordinates as x*10000+y (fits in 63 bits, manually set)
    id = fields.IntField(primary_key=True, generated=False)

    # Tile coordinates
    x = fields.IntField()
    y = fields.IntField()

    # Queue assignment (999 = burning queue, 1-998 = temperature index, 0 = not in any queue)
    heat = fields.IntField(default=999)

    # Timing metadata (IntField for integer epoch seconds, following project convention)
    last_checked = fields.IntField(default=0)  # When we last fetched this tile (0 = never checked)
    last_update = fields.IntField()  # Parsed from Last-Modified header, or current time if not provided

    # HTTP caching header (for conditional requests)
    etag = fields.CharField(max_length=255, default="")  # Raw ETag header

    # Reverse relation (defined by TileProject.tile FK with related_name="tile_projects")
    projects: fields.ReverseRelation[TileProject]

    @staticmethod
    def tile_id(x: int, y: int) -> int:
        """Compute primary key from tile coordinates."""
        return x * 10000 + y

    @property
    def tile(self) -> Tile:
        return Tile(self.x, self.y)

    class Meta(Model.Meta):
        table = "tile"
        indexes = [
            ("heat", "last_checked"),  # Composite index for LRU selection within queues
        ]


class TileProject(Model):
    """Many-to-many relationship between tiles and projects."""

    id = fields.IntField(primary_key=True)
    tile = fields.ForeignKeyField("models.TileInfo", related_name="tile_projects")
    project = fields.ForeignKeyField("models.ProjectInfo", related_name="project_tiles")

    class Meta(Model.Meta):
        table = "tile_project"
        unique_together = (("tile_id", "project_id"),)
