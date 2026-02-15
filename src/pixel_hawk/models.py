"""Tortoise ORM models for pixel-hawk persistence.

ProjectInfo: Pure Tortoise ORM model for project metadata.
HistoryChange: Per-diff event log recording pixel changes.
DiffStatus: Enum for project diff states.
"""

import time
from enum import StrEnum, auto

from tortoise import fields
from tortoise.models import Model

from .geometry import Point, Rectangle, Size


class DiffStatus(StrEnum):
    """Status of a project diff operation."""

    NOT_STARTED = auto()
    IN_PROGRESS = auto()
    COMPLETE = auto()


class ProjectInfo(Model):
    """Persistent metadata for a project. Pure Tortoise ORM model."""

    # Primary key: project name (filename without extension)
    name = fields.CharField(max_length=255, primary_key=True)

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

    @classmethod
    async def from_rect(cls, rect: Rectangle, name: str) -> ProjectInfo:
        """Create and save a new ProjectInfo from project rectangle."""
        now = round(time.time())
        return await cls.create(
            name=name,
            x=rect.point.x,
            y=rect.point.y,
            width=rect.size.w,
            height=rect.size.h,
            first_seen=now,
            last_check=now,
        )

    @classmethod
    async def get_or_create_from_rect(cls, rect: Rectangle, name: str) -> ProjectInfo:
        """Load existing ProjectInfo or create new from rectangle."""
        existing = await cls.filter(name=name).first()
        if existing:
            return existing
        return await cls.from_rect(rect, name)

    class Meta(Model.Meta):
        table = "project_info"


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
