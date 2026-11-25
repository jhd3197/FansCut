"""Plain data shapes shared across the library, planner and renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    episode_count: int = 0  # episodes with a file
    episode_total: int = 0  # episodes that exist (aired), when the source knows; else 0
    season_count: int = 0  # regular seasons, specials not counted
    is_anime: bool = False
    collections: list[str] = field(default_factory=list)  # Plex collection tags
    # where its art lives: an https URL (TMDB, via Sonarr) or a Plex path
    poster: str | None = None
    backdrop: str | None = None


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
class Movie:
    key: str  # "<source>:<id>", e.g. radarr:12, plexmovie:3456, foldermovie:Inception (2010)
    source: str
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    file: str | None = None  # local path after path mapping; None = not on disk
    duration: float | None = None  # seconds
    collections: list[str] = field(default_factory=list)  # Plex collection tags
    # where its art lives: an https URL (TMDB, via Radarr) or a Plex path
    poster: str | None = None
    backdrop: str | None = None


@dataclass
class Segment:
    episode_id: str
    file: str
    start: float
    end: float
    reason: str = ""
    xfade_ms: float = 0.0  # audio crossfade from the previous segment into this one
    fx: dict[str, Any] = field(default_factory=dict)  # edit operations (timeline/effects.py)
    audio_file: str | None = None  # music bridge: the file audio comes from instead

    @property
    def duration(self) -> float:
        """Seconds in the output (speed and hold included)."""
        return (self.end - self.start) / self.fx.get("speed", 1.0) + self.fx.get("hold", 0.0)
