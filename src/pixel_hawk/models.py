"""Tortoise ORM models for pixel-hawk persistence.

Person: Represents a person who can own projects.
ProjectInfo: Pure Tortoise ORM model for project metadata.
ProjectState: Enum for project state (active/passive/inactive).
HistoryChange: Per-diff event log recording pixel changes.
DiffStatus: Enum for project diff states.
"""

import time
from enum import StrEnum, auto

from tortoise import fields
from tortoise.models import Model

from .geometry import Point, Rectangle, Size, Tile


class DiffStatus(StrEnum):
    """Status of a project diff operation."""

    NOT_STARTED = auto()
    IN_PROGRESS = auto()
    COMPLETE = auto()


class ProjectState(StrEnum):
    """State of a project for quota and monitoring purposes."""

    ACTIVE = "active"  # Watched, counts towards quota
    PASSIVE = "passive"  # Checked if tile updates, doesn't count towards quota
    INACTIVE = "inactive"  # Not checked, doesn't count towards quota


class Person(Model):
    """Represents a person who can own projects."""

    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=255, unique=True)

    # Cached count of unique watched tiles (updated when projects change)
    # Only counts tiles from 'active' state projects
    watched_tiles_count = fields.IntField(default=0)

    # Reverse relation (defined by ProjectInfo.owner FK with related_name="projects")
    projects: fields.ReverseRelation["ProjectInfo"]

    async def calculate_watched_tiles(self) -> set[Tile]:
        """Calculate unique tiles across all ACTIVE projects for this person."""
        tiles = set()
        # Only count active projects towards quota
        projects = await self.projects.filter(state=ProjectState.ACTIVE).all()
        for project in projects:
            rect = project.rectangle
            tiles.update(rect.tiles)  # rect.tiles is a cached property (frozenset[Tile])
        return tiles

    async def update_watched_tiles_count(self) -> None:
        """Recalculate and save watched tiles count."""
        tiles = await self.calculate_watched_tiles()
        self.watched_tiles_count = len(tiles)
        await self.save()

    class Meta:
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
    state = fields.CharEnumField(ProjectState, default=ProjectState.ACTIVE, index=True)

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

    @property
    def rectangle(self) -> Rectangle:
        return Rectangle.from_point_size(Point(self.x, self.y), Size(self.width, self.height))

    @property
    def filename(self) -> str:
        """Compute filename from coordinates (coordinates only, no name prefix)."""
        tx, ty, px, py = Point(self.x, self.y).to4()
        return f"{tx}_{ty}_{px}_{py}.png"

    @classmethod
    async def from_rect(cls, rect: Rectangle, owner_id: int, name: str, state: ProjectState = ProjectState.ACTIVE) -> ProjectInfo:
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
        table = "project_info"
        unique_together = (("owner_id", "name"),)  # Prevent duplicate names per person


class HistoryChange(Model):
    """Record of a single diff event for a project."""

    id = fields.IntField(primary_key=True)
    project = fields.ForeignKeyField("models.ProjectInfo", related_name="history_changes")
    timestamp = fields.IntField()

    # Status of this diff
    status = fields.CharEnumField(DiffStatus)

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
