"""The link to Marcarr: the server that analyses the films (shots, subtitles, scenes,
signals, cast, faces and tags). FansCut talks to it over its HTTP API
(``marcarr.url``, with ``marcarr.api_key`` when it asks for one).

A FansCut film is the Marcarr title ``movie:tmdb:<id>``: Marcarr knows a film once
it has scanned it from Plex. Marcarr keeps the tag list and every tag, FansCut's
cut tags (``fc:``) included. FansCut keeps the frames, contact sheets and clips it
cuts from the file.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any
from urllib.parse import quote

import requests

from ..errors import ClientError, FansCutError, NotFoundError
from . import vocab

TITLES_FOR = 20.0  # seconds a listing of Marcarr's titles is reused
log = logging.getLogger(__name__)


def title_id(film: Any) -> str:
    """The Marcarr title a FansCut film is."""
    if getattr(film, "tmdb_id", None):
        return f"movie:tmdb:{int(film.tmdb_id)}"
    slug = re.sub(r"[^a-z0-9]+", "-", str(film.key).lower()).strip("-")[:80]
    return f"local:fanscut:{slug}"


def quoted(tid: str) -> str:
    return quote(tid, safe="")


class Link:
    """Marcarr's API, called from FansCut."""

    def __init__(self, get_cfg: Any):
        self._get_cfg = get_cfg
        self._http: Any = None  # tests: a client bound to a Marcarr app
        self._session = requests.Session()
        self._titles: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._lock = threading.Lock()

    def use(self, http: Any) -> None:
        """Send requests through *http* instead (tests): anything with ``request()``
        whose base URL already ends in ``/api/v1``."""
        self._http = http
        self.forget()

    def close(self) -> None:
        self._session.close()

    def forget(self) -> None:
        with self._lock:
            self._titles = None
        vocab.forget()

    @property
    def base(self) -> str:
        return str(self._get_cfg().marcarr.url or "").rstrip("/")

    # -- calls ---------------------------------------------------------------------
    def call(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Any:
        cfg = self._get_cfg().marcarr
        headers = {"X-Marcarr-Client": "fanscut"}
        if cfg.api_key:
            headers["X-Api-Key"] = cfg.api_key
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            if self._http is not None:
                r = self._http.request(method, path, params=params, json=json, headers=headers)
            else:
                if not self.base:
                    raise FansCutError("set Marcarr's address in Settings (marcarr.url)")
                r = self._session.request(
                    method,
                    self.base + "/api/v1" + path,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=cfg.timeout,
                )
        except requests.RequestException as exc:
            raise ClientError(f"Marcarr at {self.base} did not answer: {exc}") from exc
        if r.status_code == 404:
            raise NotFoundError(self._detail(r) or f"Marcarr has no {path}")
        if r.status_code in (401, 403):
            raise ClientError(
                f"Marcarr refused FansCut ({r.status_code}): set marcarr.api_key "
                "to a key with the read, propose, review and run scopes",
                r.status_code,
            )
        if r.status_code >= 500:
            raise ClientError(f"Marcarr failed ({r.status_code}): {self._detail(r)}", r.status_code)
        if r.status_code >= 400:
            raise FansCutError(self._detail(r) or f"Marcarr said {r.status_code}")
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    @staticmethod
    def _detail(r: Any) -> str:
        try:
            d = r.json()
        except ValueError:
            return str(r.text or "")[:300]
        return str(d.get("detail") or "") if isinstance(d, dict) else ""

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, params=params)

    def post(self, path: str, body: Any = None) -> Any:
        return self.call("POST", path, json=body if body is not None else {})

    # -- films and videos ----------------------------------------------------------
    def titles(self) -> dict[str, dict[str, Any]]:
        """Marcarr's titles by id (a listing reused for a few seconds). Empty when
        Marcarr can't be reached, so lists show what FansCut has on its own."""
        with self._lock:
            if self._titles and time.monotonic() - self._titles[0] < TITLES_FOR:
                return self._titles[1]
        if self._http is None and not self.base:
            return {}  # no Marcarr set up
        try:
            out = self._list_titles()
        except ClientError as exc:  # remembered too, so a page doesn't wait on it again and again
            log.warning("scene data from Marcarr is not available: %s", exc)
            out = {}
        with self._lock:
            self._titles = (time.monotonic(), out)
        return out

    def _list_titles(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            d = self.get("/titles", type="movie", per_page=500, page=page)
            for t in d.get("items") or []:
                out[t["id"]] = t
            if page * 500 >= int(d.get("total") or 0):
                break
            page += 1
        return out

    def summary(self, film: Any) -> dict[str, Any] | None:
        return self.titles().get(title_id(film))

    def video(self, film: Any) -> str | None:
        """The Marcarr video of a film, or None when Marcarr doesn't have it."""
        t = self.summary(film)
        return t.get("video_id") if t else None

    def video_or_fail(self, film: Any) -> str:
        vid = self.video(film)
        if vid:
            return vid
        self.forget()  # it may have just been scanned
        vid = self.video(film)
        if not vid:
            raise FansCutError(
                f"Marcarr doesn't have {film.title} yet: it picks films up from Plex "
                "on its next library scan"
            )
        return vid
