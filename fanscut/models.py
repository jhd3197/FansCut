"""Plain data shapes shared across the library, planner and renderer."""

from __future__ import annotations

from dataclasses import dataclass, field

LABELS = ("canon", "mixed", "filler", "unknown")
TRIM_KINDS = ("intro", "outro", "recap", "preview")


@dataclass
class Series:
    key: str  # "<source>:<id>", e.g. sonarr:12, plex:3456, folder:Harbor Lights
    source: str
    title: str
    year: int | None = None
    alt_titles: list[str] = field(default_factory=list)
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    episode_count: int = 0
    is_anime: bool = False


@dataclass
class Episode:
    season: int
    number: int
    title: str = ""
    absolute: int | None = None
    file: str | None = None  # local path after path mapping; None = not on disk
    duration: float | None = None  # seconds
    air_date: str | None = None

    @property
    def id(self) -> str:
        return f"S{self.season:02d}E{self.number:02d}"


@dataclass
class Segment:
    episode_id: str
    file: str
    start: float
    end: float
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start
