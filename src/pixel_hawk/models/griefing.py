"""Painter identity and grief report types.

Painter represents the WPlace user who last painted a specific pixel,
as returned by the pixel authorship API. GriefReport aggregates the
results of investigating a regression event.
"""

from typing import NamedTuple


class Painter(NamedTuple):
    user_id: int
    user_name: str
    alliance_name: str
    discord_id: str
    discord_name: str

    @classmethod
    def new(cls, **kwargs) -> Painter:
        return Painter(
            user_id=kwargs.get("id", 0),
            user_name=kwargs.get("name", ""),
            alliance_name=kwargs.get("allianceName", ""),
            discord_id=kwargs.get("discordId", ""),
            discord_name=kwargs.get("discord", ""),
        )

    def __bool__(self):
        return self.user_id != 0 or self.user_name != ""

    def __str__(self):
        if not self.__bool__():
            return "(unknown user)"
        parts = [
            self.alliance_name and f"%{self.alliance_name}",
            f"~{self.user_name} (#{self.user_id})",
            self.discord_name and f"@{self.discord_name}",
            self.discord_id and f"<@{self.discord_id}>",
        ]
        return " ".join(p for p in parts if p)


class GriefReport(NamedTuple):
    regress_count: int = 0
    painters: tuple[Painter, ...] = ()

    def __bool__(self):
        return self.regress_count > 0
