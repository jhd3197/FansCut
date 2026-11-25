"""config.yaml schema and loading.

Everything has a default, so a missing or empty file still boots. Secrets
(Sonarr key, Plex token) can live in the file or come from the environment
(see ENV_FIELDS); the settings API masks them. Environment values are applied
on load and never written back by save().
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .errors import ConfigError

MASK = "***"

# env var -> dotted config path. NAME_FILE holds a path to a file with the value
# (Docker secrets); if both NAME and NAME_FILE are set, NAME wins.
ENV_FIELDS = {
    "FANSCUT_SONARR_URL": "library.sonarr.url",
    "FANSCUT_SONARR_API_KEY": "library.sonarr.api_key",
    "FANSCUT_RADARR_URL": "library.radarr.url",
    "FANSCUT_RADARR_API_KEY": "library.radarr.api_key",
    "FANSCUT_PLEX_URL": "library.plex.url",
    "FANSCUT_PLEX_TOKEN": "library.plex.token",
    "FANSCUT_AI_API_KEY": "ai.api_key",
    "FANSCUT_GITHUB_TOKEN": "community.github_token",
}


def Secret(default: str = "") -> Any:  # noqa: N802 - reads like a type in the models
    """A secret field: masked by the settings API and kept when "***" comes back."""
    return Field(default=default, json_schema_extra={"secret": True})


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6464
    # "none": anyone who can reach the server may use it (keep it on a trusted
    # network). "api_key": every /api call needs the API key (or viewer_key, read-only).
    auth: Literal["none", "api_key"] = "none"
    api_key: str = Secret()  # generated on first start; webhooks always need it
    viewer_key: str = Secret()  # optional second key that may only read
    allowed_hosts: list[str] = Field(default_factory=list)  # extra Host names to serve
    trusted_proxies: list[str] = Field(default_factory=list)  # IPs whose X-Forwarded-* count


class PathsCfg(BaseModel):
    data_dir: str = "./data"
    output_dir: str = "./output"


class SonarrCfg(BaseModel):
    url: str = ""
    api_key: str = Secret()


class RadarrCfg(BaseModel):
    url: str = ""
    api_key: str = Secret()


class PlexCfg(BaseModel):
    url: str = ""
    token: str = Secret()
    sections: list[str] = Field(default_factory=list)  # empty = every show section
    movie_sections: list[str] = Field(default_factory=list)  # empty = every movie section


class FolderCfg(BaseModel):
    path: str = ""


class MoviesFolderCfg(BaseModel):
    path: str = ""  # one subfolder per movie; "{tmdb-123}" in the name sets the id


class PathMapping(BaseModel):
    remote: str
    local: str


class LibraryCfg(BaseModel):
    sonarr: SonarrCfg = Field(default_factory=SonarrCfg)
    radarr: RadarrCfg = Field(default_factory=RadarrCfg)
    plex: PlexCfg = Field(default_factory=PlexCfg)
    folder: FolderCfg = Field(default_factory=FolderCfg)
    movies: MoviesFolderCfg = Field(default_factory=MoviesFolderCfg)
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
    # auto: HEVC 10-bit when a source is HDR (keeps PQ/HLG + mastering data), else H.264
    video_codec: Literal["auto", "h264", "hevc"] = "auto"


class CutCfg(BaseModel):
    preset: Literal["canon_only", "canon_mixed", "everything", "custom"] = "canon_only"
    unknown_policy: Literal["keep", "skip"] = "keep"
    keep_intro_first_episode: bool = True
    pad: float = 0.5
    merge_gap: float = 1.0
    min_segment: float = 5.0
    transition: Literal["none", "fade"] = "none"


class RenderCfg(BaseModel):
    keep: bool = True  # default for new renders; False = evict automatically
    ephemeral_days: int = 7  # sweep deletes non-kept renders older than this; 0 = never
    language: str = ""  # preferred audio language tag (e.g. jpn, eng); "" = first track
    effectcraft_cli: str = ""  # path to effectcraft-cli; "" = grades stay in ffmpeg
    effectcraft_grade: bool = True  # route segment grades through EffectCraft when it's set


class AutomationCfg(BaseModel):
    sonarr_webhook: bool = False  # accept POST /api/webhooks/sonarr (no auth: keep local)
    auto_detect: bool = True  # run OP/ED detection on affected seasons before rendering
    keep: bool = True  # keep flag for webhook-triggered renders


class CommunityCfg(BaseModel):
    index_url: str = "https://raw.githubusercontent.com/jhd3197/fanscut-recipes/main/index.json"
    github_token: str = Secret()  # secret, for private cut repos; prefer FANSCUT_GITHUB_TOKEN
    offline: bool = False


class AICfg(BaseModel):
    base_url: str = ""  # OpenAI-compatible endpoint, e.g. http://localhost:11434/v1 (Ollama)
    api_key: str = Secret()  # secret; prefer FANSCUT_AI_API_KEY over the config file
    model: str = ""
    timeout: int = 120


class Config(BaseModel):
    server: ServerCfg = Field(default_factory=ServerCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)
    library: LibraryCfg = Field(default_factory=LibraryCfg)
    catalogues: CataloguesCfg = Field(default_factory=CataloguesCfg)
    fillers: FillersCfg = Field(default_factory=FillersCfg)
    fingerprint: FingerprintCfg = Field(default_factory=FingerprintCfg)
    ffmpeg: FFmpegCfg = Field(default_factory=FFmpegCfg)
    cut: CutCfg = Field(default_factory=CutCfg)
    render: RenderCfg = Field(default_factory=RenderCfg)
    automation: AutomationCfg = Field(default_factory=AutomationCfg)
    ai: AICfg = Field(default_factory=AICfg)
    community: CommunityCfg = Field(default_factory=CommunityCfg)

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


def env_overrides() -> dict[str, str]:
    """Dotted config path -> value for every field set through the environment."""
    out: dict[str, str] = {}
    for name, path in ENV_FIELDS.items():
        val = os.environ.get(name, "").strip()
        file = os.environ.get(f"{name}_FILE", "").strip()
        if not val and file:
            try:
                val = Path(file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigError(f"{name}_FILE: cannot read {file}: {exc.strerror}") from exc
        if val:
            out[path] = val
    return out


def _dig(d: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    *parents, leaf = path.split(".")
    for k in parents:
        d = d.setdefault(k, {})
    return d, leaf


def parse(raw: dict[str, Any] | None) -> Config:
    try:
        return Config.model_validate(raw or {})
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def load(path: Path | str | None = None) -> Config:
    p = Path(path) if path else default_path()
    raw = _read_raw(p)
    for path_, val in env_overrides().items():
        d, leaf = _dig(raw, path_)
        d[leaf] = val
    return parse(raw)


def _read_raw(p: Path) -> dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # str(exc) quotes the offending line, which may hold a secret
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        problem = getattr(exc, "problem", None) or "parse error"
        raise ConfigError(f"{p}: invalid YAML{where}: {problem}") from exc
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    return raw or {}


def save(cfg: Config, path: Path | str) -> None:
    """Write the config. Environment-provided values are not persisted: those
    fields keep what the file already had (or empty)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump()
    env = env_overrides()
    if env:
        try:
            old = _read_raw(p)
        except ConfigError:
            old = {}
        for path_ in env:
            d, leaf = _dig(data, path_)
            src: Any = old
            for k in path_.split("."):
                src = src.get(k) if isinstance(src, dict) else None
            d[leaf] = src if isinstance(src, str) else ""
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with contextlib.suppress(OSError):
        p.chmod(0o600)  # may hold secrets; best effort, fails quietly on Windows


def _secret_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    out: list[str] = []
    for name, f in model.model_fields.items():
        extra = f.json_schema_extra
        if isinstance(extra, dict) and extra.get("secret"):
            out.append(prefix + name)
        ann = f.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out += _secret_paths(ann, f"{prefix}{name}.")
    return out


# Every field declared with Secret(), as dotted paths. New secrets are masked by default.
SECRET_PATHS = tuple(_secret_paths(Config))

# Fields the settings API will not change: they name programs FansCut runs, or
# hold the keys that guard the API. Set them in config.yaml or the environment.
READ_ONLY_HTTP = ("ffmpeg.binary", "ffmpeg.probe_binary", "render.effectcraft_cli",
                  "server.api_key", "server.viewer_key", "server.auth",
                  "server.allowed_hosts", "server.trusted_proxies")


def masked(cfg: Config) -> dict[str, Any]:
    d = cfg.model_dump()
    for path in SECRET_PATHS:
        parent, leaf = _dig(d, path)
        if parent.get(leaf):
            parent[leaf] = MASK
    return d


def env_locked() -> list[str]:
    return sorted(env_overrides())


def merge_update(current: Config, incoming: dict[str, Any], *, from_http: bool = False
                 ) -> Config:
    """Apply a settings payload; a masked secret keeps the stored value.

    Unknown keys are refused rather than silently dropped. With from_http, a
    change to a READ_ONLY_HTTP field is refused too (sending the current value
    back is fine: the settings page posts the whole object)."""
    merged = current.model_dump()

    def walk(dst: dict[str, Any], src: dict[str, Any], prefix: str) -> None:
        for k, v in src.items():
            path = prefix + k
            if k not in dst:
                if prefix == "" and k == "env_locked":  # echoed back by the settings page
                    continue
                raise ConfigError(f"unknown setting {path!r}")
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                walk(dst[k], v, path + ".")
            elif v != MASK:
                if from_http and path in READ_ONLY_HTTP and v != dst[k]:
                    raise ConfigError(f"{path} can only be changed in config.yaml or the "
                                      "environment")
                dst[k] = v

    walk(merged, incoming, "")
    return parse(merged)
