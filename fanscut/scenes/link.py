"""Marcarr inside FansCut: the scene analysis (shots, subtitles, scenes, signals,
cast, faces, tags) runs in an embedded Marcarr, which keeps its own database under
``<data_dir>/marcarr``. FansCut keeps what is its own: the film list, cut tags
(``fc:``), frames, contact sheets and clips cut from the file.

A FansCut film becomes a Marcarr title (``movie:tmdb:<id>``, or
``local:fanscut:<slug>`` for a film with no TMDB id) and its file a Marcarr video.
The Marcarr settings follow FansCut's (AI model, TMDB key, Plex, tag families).
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import FansCutError

if TYPE_CHECKING:
    from marcarr.core import Core as MarcarrCore

    from ..config import Config


def title_id(film: Any) -> str:
    """The Marcarr title a FansCut film is."""
    if getattr(film, "tmdb_id", None):
        return f"movie:tmdb:{int(film.tmdb_id)}"
    slug = re.sub(r"[^a-z0-9]+", "-", str(film.key).lower()).strip("-")[:80]
    return f"local:fanscut:{slug}"


def marcarr_config(cfg: Config) -> Any:
    """Marcarr's settings, worked out from FansCut's."""
    from marcarr.config import Config as MConfig

    ai, sc = cfg.ai, cfg.scenes
    data = Path(cfg.marcarr.data_dir) if cfg.marcarr.data_dir else cfg.data_dir / "marcarr"
    off = [f for f in sc.families_off if f]
    steps = {"signals": bool(sc.signals), "faces": bool(cfg.marcarr.faces)}
    plex = cfg.library.plex
    return MConfig.model_validate(
        {
            "server": {"data_dir": str(data)},
            "setup_done": True,
            "plex": {"url": plex.url or "", "token": plex.token or ""},
            "ai": {
                "enabled": bool(ai.model),
                "model": ai.model,
                "vision_model": ai.vision_model,
                "base_url": ai.base_url,
                "api_key": ai.api_key,
                "timeout": ai.timeout,
                "auto_accept": sc.auto_accept,
            },
            "tags": {"families_off": off, "search_closely": list(sc.search_closely)},
            "analysis": {"steps": steps, "on_new": False},
            "catalogues": {"tmdb_api_key": cfg.catalogues.tmdb_api_key},
            "ffmpeg": {"binary": cfg.ffmpeg.binary, "probe_binary": cfg.ffmpeg.probe_binary},
        }
    )


class Link:
    """The embedded Marcarr, started on first use."""

    def __init__(self, get_cfg: Any):
        self._get_cfg = get_cfg
        self._core: MarcarrCore | None = None
        self._lock = threading.Lock()
        self._run_worker = False
        self._videos: dict[tuple[str, str, float, int], str] = {}

    @property
    def core(self) -> MarcarrCore:
        with self._lock:
            if self._core is None:
                try:
                    from marcarr.core import Core
                except ImportError as exc:  # pragma: no cover - a broken install
                    raise FansCutError("scene analysis needs Marcarr: pip install marcarr") from exc
                mcfg = marcarr_config(self._get_cfg())
                self._core = Core(
                    mcfg, mcfg.data_dir / "config.yaml", start_worker=self._run_worker
                )
            return self._core

    def start_worker(self) -> None:
        """Run Marcarr's jobs in the background (the server does; tests run them by hand)."""
        self._run_worker = True
        if self._core is not None:
            self._core.worker.start()

    def apply_config(self, cfg: Config) -> None:
        """FansCut's settings changed: Marcarr's follow (kept in memory, never written)."""
        if self._core is not None:
            self._core.cfg = marcarr_config(cfg)
            self._core.forget_caps()

    def close(self) -> None:
        with self._lock:
            if self._core is not None:
                self._core.close()
                self._core = None

    # -- films and videos ----------------------------------------------------------
    def video(self, film: Any, register: bool = False) -> str | None:
        """The Marcarr video of a film's file. Only a lookup unless *register*: telling
        Marcarr about a file reads parts of it, so it waits until analysis is asked for."""
        m = self.core
        tid = title_id(film)
        path = getattr(film, "file", None) or ""
        if register:
            if not path or not Path(path).is_file():
                raise FansCutError(f"{film.title} has no file on disk")
            st = Path(path).stat()
            slot = (tid, path, st.st_mtime, st.st_size)
            if slot in self._videos:
                return self._videos[slot]
            self._upsert_title(film, tid)
            vid = m.library.add_file(tid, path)
            self._videos[slot] = vid
            return vid
        cur = m.store.current_video(tid)
        return cur["id"] if cur else None

    def _upsert_title(self, film: Any, tid: str) -> None:
        key = str(film.key)
        plex_key = key.split(":", 1)[1] if key.startswith("plexmovie:") else None
        name = film.title or key
        self.core.store.upsert_title(
            {
                "id": tid,
                "type": "movie",
                "name": name,
                "sort_name": name.lower(),
                "year": film.year,
                "tmdb_id": film.tmdb_id,
                "plex_key": plex_key,
                "duration": film.duration,
                "file": getattr(film, "file", "") or "",
            }
        )
