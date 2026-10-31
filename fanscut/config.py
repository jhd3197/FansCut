"""config.yaml schema and loading.

Everything has a default, so a missing or empty file still boots. Secrets
(Sonarr key, Plex token) live in the same file; the settings API masks them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .errors import ConfigError

MASK = "***"


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6464


class PathsCfg(BaseModel):
    data_dir: str = "./data"
    output_dir: str = "./output"


class SonarrCfg(BaseModel):
    url: str = ""
    api_key: str = ""


class PlexCfg(BaseModel):
    url: str = ""
    token: str = ""
    sections: list[str] = Field(default_factory=list)  # empty = every show section


class FolderCfg(BaseModel):
    path: str = ""


class PathMapping(BaseModel):
    remote: str
    local: str


class LibraryCfg(BaseModel):
    sonarr: SonarrCfg = Field(default_factory=SonarrCfg)
    plex: PlexCfg = Field(default_factory=PlexCfg)
    folder: FolderCfg = Field(default_factory=FolderCfg)
    path_mappings: list[PathMapping] = Field(default_factory=list)


class CataloguesCfg(BaseModel):
    offline: bool = False
    cache_days: int = 14


class FillersCfg(BaseModel):
    dir: str = ""  # defaults to <data_dir>/fillers
    url: str = "https://raw.githubusercontent.com/AniraTeam/AniFiller/main/anifiller.min.json"
    use_jikan: bool = True


class FingerprintCfg(BaseModel):
    sample_rate: int = 8000
    head_minutes: float = 5.0
    tail_minutes: float = 5.0
    min_segment_seconds: float = 15.0
    max_segment_seconds: float = 180.0
    threshold: float = 0.75


class FFmpegCfg(BaseModel):
    binary: str = "ffmpeg"
    probe_binary: str = "ffprobe"
    nvenc: bool = False
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    container: Literal["mkv", "mp4"] = "mkv"


class CutCfg(BaseModel):
    preset: Literal["canon_only", "canon_mixed", "everything", "custom"] = "canon_only"
    unknown_policy: Literal["keep", "skip"] = "keep"
    keep_intro_first_episode: bool = True
    pad: float = 0.5
    merge_gap: float = 1.0
    min_segment: float = 5.0
    transition: Literal["none", "fade"] = "none"


class Config(BaseModel):
    server: ServerCfg = Field(default_factory=ServerCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)
    library: LibraryCfg = Field(default_factory=LibraryCfg)
    catalogues: CataloguesCfg = Field(default_factory=CataloguesCfg)
    fillers: FillersCfg = Field(default_factory=FillersCfg)
    fingerprint: FingerprintCfg = Field(default_factory=FingerprintCfg)
    ffmpeg: FFmpegCfg = Field(default_factory=FFmpegCfg)
    cut: CutCfg = Field(default_factory=CutCfg)

    @property
    def data_dir(self) -> Path:
        return Path(self.paths.data_dir)

    @property
    def output_dir(self) -> Path:
        return Path(self.paths.output_dir)

    @property
    def fillers_dir(self) -> Path:
        return Path(self.fillers.dir) if self.fillers.dir else self.data_dir / "fillers"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "fanscut.db"


def default_path() -> Path:
    env = os.environ.get("FANSCUT_CONFIG")
    if env:
        return Path(env)
    for p in (Path("config.yaml"), Path("/config/config.yaml")):
        if p.is_file():
            return p
    return Path("config.yaml")


def parse(raw: dict[str, Any] | None) -> Config:
    try:
        return Config.model_validate(raw or {})
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def load(path: Path | str | None = None) -> Config:
    p = Path(path) if path else default_path()
    if not p.is_file():
        return Config()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p}: {exc}") from exc
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    return parse(raw)


def save(cfg: Config, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False), encoding="utf-8")


def masked(cfg: Config) -> dict[str, Any]:
    d = cfg.model_dump()
    if d["library"]["sonarr"]["api_key"]:
        d["library"]["sonarr"]["api_key"] = MASK
    if d["library"]["plex"]["token"]:
        d["library"]["plex"]["token"] = MASK
    return d


def merge_update(current: Config, incoming: dict[str, Any]) -> Config:
    """Apply a settings payload; a masked secret keeps the stored value."""
    merged = current.model_dump()

    def walk(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                walk(dst[k], v)
            elif v != MASK:
                dst[k] = v

    walk(merged, incoming)
    return parse(merged)
