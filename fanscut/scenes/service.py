"""Scene tagging for the API, the MCP server and the posters: one place over Core.

The analysis itself (shots, subtitles, scenes, signals, cast, faces and the AI
tagger) runs in Marcarr, which FansCut calls over HTTP (link.py). This service
turns Marcarr's answers into the shapes FansCut's pages read. FansCut keeps its
own cut tags (``fc:``), and the frames, contact sheets and clips it cuts from the
file for its players.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import FansCutError, NotFoundError
from . import store as st
from .link import Link, quoted, title_id
from .text import match_person, speech

if TYPE_CHECKING:
    from ..core import Core

MAX_CLIP = 300.0
MAX_SHEET_TILES = 24
INDEX_STEPS = ["shots", "subtitles", "scenes"]
NEEDS_RUN = ("notrun", "rerun", "failed")  # Marcarr step states with work left


def _evidence_texts(ev: dict[str, Any]) -> list[str]:
    """Marcarr's evidence ({"seen": [...], "heard": [...]}, items a text or {"text", "at"})
    as FansCut's flat list of what was seen, then what was heard."""
    out = []
    for kind in ("seen", "heard"):
        for x in ev.get(kind) or []:
            text = x.get("text") if isinstance(x, dict) else x
            if text:
                out.append(str(text))
    return list(dict.fromkeys(out))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


class SceneService:
    def __init__(self, core: Core):
        self.core = core
        self.store = st.SceneStore(core.db)  # FansCut's cut tags (fc:)
        self.link = Link(lambda: self.core.cfg)
        self._clip_lock = threading.Lock()
        self._clip_locks: dict[str, threading.Lock] = {}

    def close(self) -> None:
        self.link.close()

    # -- films --------------------------------------------------------------------
    def _movies(self) -> list[Any]:
        ms, _ = self.core.library.list_movies()
        return ms

    def film(self, key: str) -> Any:
        m = next((x for x in self._movies() if x.key == key), None)
        if m is None:
            raise NotFoundError(f"movie {key} not found")
        return m

    def _file(self, key: str) -> tuple[Any, str]:
        m = self.film(key)
        if not m.file or not Path(m.file).is_file():
            raise NotFoundError(f"{m.title} has no file on disk")
        return m, m.file

    def _video(self, key: str) -> str | None:
        return self.link.video(self.film(key))

    def _info(self, vid: str) -> dict[str, Any]:
        """Marcarr's video: step states and details, counts, subtitle source."""
        return dict(self.link.get(f"/videos/{vid}"))

    def _doc(self, key: str) -> dict[str, Any] | None:
        """The film's index (shots, subtitle lines, automatic scenes) once Marcarr has
        found the scenes; None before."""
        vid = self._video(key)
        if vid is None:
            return None
        info = self._info(vid)
        if info["steps"].get("scenes") != "done":
            return None
        a = self.link.get(f"/videos/{vid}/analysis")
        v = a["video"]
        lines = a.get("subtitles") or []
        kind = (info.get("subtitles") or {}).get("kind") or "none"
        return {
            "duration": v.get("duration"),
            "width": v.get("width"),
            "height": v.get("height"),
            "shots": a.get("shots") or [],
            "lines": lines,
            "scenes": [
                {"start": s["start"], "end": s["end"], "n": s["n"]} for s in a.get("scenes") or []
            ],
            "subtitles": {"kind": kind, "lines": len(lines)},
        }

    def _index_or_fail(self, key: str) -> dict[str, Any]:
        doc = self._doc(key)
        if doc is None:
            raise FansCutError("this film has no scene index yet: index it first")
        return doc

    def films(self, collection: str | None = None) -> list[dict[str, Any]]:
        from ..browse import NOT_A_FRANCHISE, _collections_of, resolve_collection

        movies = self._movies()
        if collection:
            collection = resolve_collection(movies, collection)
        titles = self.link.titles()
        out = []
        for m in movies:
            cols = _collections_of(m)
            if collection and collection not in cols:
                continue
            t = titles.get(title_id(m)) or {}
            steps = t.get("steps") or {}
            indexed = steps.get("scenes") == "done"
            out.append(
                {
                    "key": m.key,
                    "title": m.title,
                    "year": m.year,
                    "tmdb_id": m.tmdb_id,
                    "duration": m.duration,
                    "on_disk": bool(m.file),
                    "collections": cols,
                    # what a cut may span: Plex smart lists ("Popular on My Server") aren't
                    "franchises": [c for c in cols if not NOT_A_FRANCHISE.search(c)],
                    "in_marcarr": bool(t.get("video_id")),
                    "indexed": indexed,
                    "scenes": None,
                    "tags": {
                        "proposed": int(t.get("to_review") or 0),
                        "accepted": 0,
                    },
                }
            )
        return sorted(out, key=lambda f: (f["year"] or 0, f["title"]))

    def overview(self, key: str, with_lines: bool = True) -> dict[str, Any]:
        m = self.film(key)
        doc = self._doc(key)
        if doc and not with_lines:
            doc = {k: v for k, v in doc.items() if k != "lines"}
        from ..browse import NOT_A_FRANCHISE, _collections_of, collection_ids

        ids = collection_ids(self._movies())
        return {
            "film": {
                "key": m.key,
                "title": m.title,
                "year": m.year,
                "tmdb_id": m.tmdb_id,
                "duration": m.duration,
                "collections": [
                    {"id": ids.get(c, c), "name": c}
                    for c in _collections_of(m)
                    if not NOT_A_FRANCHISE.search(c)
                ],
                "on_disk": bool(m.file),
            },
            "index": doc,
            "tags": self.tags([key]),
            "cast": self._cast_rows(title_id(m)),
        }

    # -- analysis jobs (run by Marcarr) -------------------------------------------------
    def _run(self, key: str, steps: list[str] | None) -> dict[str, Any]:
        """Ask Marcarr to run *steps* (None: every step it has left) on the film."""
        film = self.film(key)
        vid = self.link.video_or_fail(film)
        if steps is None:
            info = self._info(vid)
            steps = [s for s, state in info["steps"].items() if state in NEEDS_RUN]
            if not steps:
                return {
                    "id": None,
                    "kind": "marcarr",
                    "title": f"Analyse {film.title}",
                    "status": "done",
                    "video": vid,
                    "steps": [],
                    "position": 0,
                }
        out = self.link.post(f"/videos/{vid}/run", {"steps": steps})
        self.link.forget()
        return {
            "id": out.get("job"),
            "kind": "marcarr",
            "title": f"Analyse {film.title}",
            "status": "queued" if out.get("queued") else "done",
            "video": vid,
            "steps": out.get("steps") or [],
            "position": out.get("position", 0),
        }

    def submit_index(self, key: str, transcribe: bool = False) -> dict[str, Any]:
        """Shots, subtitle lines and automatic scenes (Marcarr follows on with the rest
        its settings ask for). *transcribe* is Marcarr's setting now."""
        steps = [*INDEX_STEPS, "cast"]
        if self.core.cfg.scenes.signals:
            steps.append("signals")
        return self._run(key, steps)

    def submit_signals(self, key: str) -> dict[str, Any]:
        self._index_or_fail(key)
        return self._run(key, ["signals"])

    def submit_label(
        self, key: str, families: list[str] | None = None, sweep: bool = False, mode: str = "full"
    ) -> dict[str, Any]:
        """Tag a film with the whole tag list: Marcarr's tags step, with the families,
        AI model and searches set in Marcarr. *families*, *sweep* and *mode* are
        checked and kept for old callers."""
        from . import vocab

        self._index_or_fail(key)
        if mode not in ("full", "focused"):
            raise FansCutError("mode is full or focused")
        if families is not None:
            bad = [f for f in families if f not in vocab.families()]
            if bad:
                raise FansCutError(f"unknown tag family: {', '.join(bad)}")
            if not families:
                raise FansCutError("pick at least one tag family")
        return self._run(key, ["tags"])

    def submit_ai(
        self, key: str, tag: str, description: str = "", verify: bool = True, sweep: bool = False
    ) -> dict[str, Any]:
        """Look for one tag. A tag outside the list becomes a Marcarr custom tag of this
        film first ("My tags"), with *description* as its hint; then the tags step runs."""
        from . import vocab

        film = self.film(key)
        self._index_or_fail(key)
        t = st.norm_tag(tag)
        if not vocab.get(t):
            mine = self.link.get("/tags").get("mine") or []
            if not any(x["id"] == t for x in mine):
                self.link.post(
                    "/tags/mine",
                    {
                        "name": t.replace("-", " ").capitalize(),
                        "id": t,
                        "hint": description[:300],
                        "scope": title_id(film),
                    },
                )
        return self._run(key, ["tags"])

    def tidy(self, key: str) -> dict[str, int]:
        """Merge duplicate cut-tag moments (Marcarr keeps sure AI tags by itself)."""
        return self.store.tidy(key, self.core.cfg.scenes.auto_accept)

    def job(self, jid: str) -> dict[str, Any] | None:
        """A Marcarr analysis job, shaped like a FansCut job (for /api/jobs/{id})."""
        try:
            rows = self.link.get("/jobs", limit=500)
        except FansCutError:
            return None
        return next((dict(j) for j in rows if j.get("id") == jid), None)

    def cancel_job(self, jid: str) -> dict[str, Any]:
        return dict(self.link.post(f"/jobs/{jid}/cancel"))

    # -- signals (measured, never tags) ---------------------------------------------
    def signals_of(self, key: str) -> dict[str, Any] | None:
        vid = self._video(key)
        if not vid:
            return None
        rows = self.link.get(f"/videos/{vid}/signals").get("items") or []
        if not rows:
            return None
        out: dict[str, list[list[float]]] = {}
        for r in rows:
            out.setdefault(r["signal"], []).append([r["start"], r["end"]])
        return {"signals": out}

    def signals(self, key: str) -> dict[str, Any]:
        found = self.signals_of(key)
        if found is None:
            raise NotFoundError("this film's signals haven't been measured yet")
        return {"key": key, **found}

    # -- looking at the film ------------------------------------------------------
    def transcript(
        self, key: str, start: float = 0, end: float | None = None, speech_only: bool = False
    ) -> list[dict[str, Any]]:
        """Subtitle lines in [start, end]; *speech_only* leaves sound descriptions out."""
        doc = self._index_or_fail(key)
        hi = end if end is not None else float("inf")
        return [
            x
            for x in doc.get("lines") or []
            if x["end"] > start and x["start"] < hi and (not speech_only or speech(x["text"]))
        ]

    def shots(self, key: str, start: float = 0, end: float | None = None) -> list[list[float]]:
        doc = self._index_or_fail(key)
        hi = end if end is not None else float("inf")
        return [s for s in doc.get("shots") or [] if s[1] > start and s[0] < hi]

    def _cache(self, kind: str, path: str, *parts: Any, ext: str) -> Path:
        stat = Path(path).stat()
        raw = "|".join(str(x) for x in (path, stat.st_mtime, stat.st_size, *parts))
        digest = hashlib.sha256(raw.encode()).hexdigest()[:20]
        out = self.core.cfg.data_dir / "scenes" / kind / f"{digest}.{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        return out

    def sheet(
        self,
        key: str,
        start: float,
        end: float,
        cols: int = 3,
        rows: int = 2,
        tile_height: int = 240,
    ) -> tuple[Path, list[float]]:
        from prompture.media.understand import contact_sheet, sample_times

        _, path = self._file(key)
        a, b = st.clean_range(start, end)
        cols, rows = max(1, min(int(cols), 6)), max(1, min(int(rows), 6))
        if cols * rows > MAX_SHEET_TILES:
            raise FansCutError(f"at most {MAX_SHEET_TILES} tiles")
        out = self._cache("sheets", path, a, b, cols, rows, tile_height, ext="jpg")
        times = sample_times(a, b, cols * rows)
        if not out.is_file():
            contact_sheet(path, times=times, cols=cols, rows=rows, tile_height=tile_height).save(
                out
            )
        return out, times

    def frame(self, key: str, t: float, height: int = 240) -> Path:
        from prompture.media.understand import extract_frame

        _, path = self._file(key)
        height = max(90, min(int(height), 1080))
        out = self._cache("frames", path, round(float(t), 2), height, ext="jpg")
        if not out.is_file():
            out.write_bytes(extract_frame(path, float(t), height=height))
        return out

    def clip(self, key: str, start: float, end: float) -> Path:
        """A browser-playable 480p MP4 of a range (5 minutes at most), cached."""
        _, path = self._file(key)
        a, b = st.clean_range(start, end)
        b = min(b, a + MAX_CLIP)
        out = self._cache("clips", path, a, b, ext="mp4")
        with self._clip_lock:  # one lock per clip, so two requests cut it once
            lock = self._clip_locks.setdefault(out.name, threading.Lock())
        with lock:
            if not out.is_file():
                self._cut_clip(path, a, b, out)
        return out

    def _cut_clip(self, path: str, a: float, b: float, out: Path) -> None:
        tmp = out.with_suffix(".tmp.mp4")
        proc = subprocess.run(  # noqa: S603 - argv list
            [
                self.core.cfg.ffmpeg.binary,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                f"{a:.3f}",
                "-i",
                path,
                "-t",
                f"{b - a:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "scale=-2:480",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-b:a",
                "128k",
                "-sn",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            capture_output=True,
            timeout=900,
            check=False,
        )
        if proc.returncode:
            tmp.unlink(missing_ok=True)
            raise FansCutError(
                "could not cut the clip: " + (proc.stderr or b"").decode("utf-8", "replace")[-300:]
            )
        tmp.replace(out)

    # -- cast and people ----------------------------------------------------------------
    def _photo(self, url: str | None) -> str:
        """A Marcarr photo URL a browser on FansCut's page can open."""
        if not url:
            return ""
        return url if url.startswith("http") or not self.link.base else self.link.base + url

    def _cast_rows(self, tid: str) -> list[dict[str, Any]]:
        """Marcarr's cast of a title in FansCut's shape: {name (the actor), character,
        photo, id}."""
        try:
            t = self.link.get(f"/titles/{quoted(tid)}")
        except NotFoundError:
            return []
        return [
            {
                "name": c.get("actor") or "",
                "character": c.get("character") or "",
                "photo": self._photo(c.get("photo")),
                "id": c["id"],
            }
            for c in t.get("cast") or []
        ]

    def get_cast(self, key: str, refresh: bool = False) -> list[dict[str, Any]]:
        film = self.film(key)
        rows = self._cast_rows(title_id(film))
        if refresh or not rows:
            vid = self.link.video(film)
            if vid is None:
                raise FansCutError(f"Marcarr doesn't have {film.title} yet, so it has no cast")
            if refresh:
                self.link.post(f"/videos/{vid}/run", {"steps": ["cast"]})
        return rows

    def _people_out(self, tid: str) -> dict[str, dict[str, str]]:
        """Person id -> {name, character} for the tags of a title."""
        return {
            c["id"]: {k: c[k] for k in ("name", "character") if c[k]} for c in self._cast_rows(tid)
        }

    def _person_ids(self, tid: str, people: list[dict[str, str]]) -> list[str]:
        """FansCut's people ({name, character}) -> Marcarr person ids: a cast member
        when one matches, else the name as an id."""
        cast = [
            {"name": c["name"], "character": c["character"], "person": c["id"]}
            for c in self._cast_rows(tid)
        ]
        out = []
        for p in people:
            said = p.get("character") or p.get("name") or ""
            hit = match_person(cast, said) or (
                match_person(cast, p["name"]) if p.get("name") else None
            )
            pid = hit["person"] if hit else _slug(said)
            if pid:
                out.append(pid)
        return list(dict.fromkeys(out))

    @staticmethod
    def _readable(pid: str) -> dict[str, str]:
        return {"character": pid.replace("-", " ").title()}

    # -- tags -----------------------------------------------------------------------
    def _films_by_title(self) -> dict[str, Any]:
        return {title_id(m): m for m in self._movies()}

    def _row(
        self, r: dict[str, Any], film: Any, people: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        """A Marcarr tag in FansCut's tag shape."""
        return {
            "id": r["id"],
            "film_key": film.key,
            "tmdb_id": film.tmdb_id,
            "start": r["start"],
            "end": r["end"],
            "tags": [r["tag"]],
            "people": [people.get(p) or self._readable(p) for p in r["people"]],
            "summary": r["summary"],
            # made in FansCut (a person here) or in Marcarr's own pages: a person's
            "source": "user" if r["source"] in ("person", "fanscut") else r["source_detail"],
            "status": r["status"],
            "confidence": r["confidence"],
            "evidence": _evidence_texts(r["evidence"]),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }

    def tags(
        self, film_keys: list[str] | None = None, tag: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Tagged moments: Marcarr's content tags and FansCut's own cut tags together."""
        local = self.store.tags(film_keys, tag, status)
        want = st.norm_tag(tag) if tag else None
        if want and want.startswith(st.SMART):
            return local
        movies = self._movies()
        if film_keys is not None:  # unknown keys (a film gone from the library) have none
            by_key = {m.key: m for m in movies}
            movies = [by_key[k] for k in film_keys if k in by_key]
        titles = self.link.titles() if movies else {}
        out = []
        for film in movies:
            t = titles.get(title_id(film)) or {}
            # only films Marcarr has tagged or been told about can have tags there
            if not t.get("video_id") or (t.get("steps") or {}).get("scenes") != "done":
                continue
            rows = (
                self.link.get(f"/videos/{t['video_id']}/tags", status=status or "all").get("items")
                or []
            )
            rows = [r for r in rows if not want or r["tag"] == want]
            if rows:
                people = self._people_out(title_id(film))
                out += [self._row(r, film, people) for r in rows]
        return sorted([*out, *local], key=lambda r: (r["film_key"], r["start"]))

    def tag_counts(self) -> list[dict[str, Any]]:
        from . import vocab

        counts: dict[str, dict[str, int]] = {}
        for r in self.tags():
            for t in r["tags"]:
                c = counts.setdefault(t, {"proposed": 0, "accepted": 0, "rejected": 0})
                c[r["status"]] += 1
        return [
            {
                "tag": t,
                "name": vocab.name(t),
                "family": (vocab.get(t) or {}).get("family"),
                "smart": vocab.is_smart(t),
                **c,
            }
            for t, c in sorted(counts.items())
        ]

    def add_tag(self, key: str, body: dict[str, Any], source: str = "user") -> dict[str, Any]:
        """Tag a range. Content tags go to Marcarr (which snaps them to camera cuts), cut
        tags (fc:) stay in FansCut; a person's tag is kept at once. Returns the first
        moment made."""
        film = self.film(key)
        tags = st.clean_tags(body.get("tags"))
        content = [t for t in tags if not t.startswith(st.SMART)]
        smart = [t for t in tags if t.startswith(st.SMART)]
        a, b = st.clean_range(body.get("start"), body.get("end"), film.duration)
        people = st.clean_people(body.get("people"))
        status = body.get("status") or ("accepted" if source == "user" else "proposed")
        if status not in st.STATUSES:
            raise FansCutError(f"status must be one of {', '.join(st.STATUSES)}")
        conf = body.get("confidence")
        conf = float(conf) if conf is not None else None
        src = str(body.get("source") or source)
        evidence = [str(x) for x in body.get("evidence") or []]
        out: dict[str, Any] | None = None
        if content:
            vid = self.link.video_or_fail(film)
            tid = title_id(film)
            made = self.link.post(
                f"/videos/{vid}/tags",
                {
                    "tags": content,
                    "start": a,
                    "end": b,
                    "snap": bool(body.get("snap", True)),
                    "people": self._person_ids(tid, people),
                    "evidence": {"seen": evidence},
                    "summary": str(body.get("summary") or ""),
                    "confidence": conf,
                    "status": status if status != "rejected" else "proposed",
                },
            )
            made = made if isinstance(made, list) else [made]
            if status != "proposed":  # an app proposes; FansCut's person decides here
                verb = "keep" if status == "accepted" else "drop"
                made = [
                    self.link.post(f"/tags/{r['id']}/{verb}") if r["status"] != status else r
                    for r in made
                ]
            out = {
                **self._row(made[0], film, self._people_out(tid)),
                "tags": content,
                "ids": [r["id"] for r in made],
            }
        if smart:
            row = self.store.add_tag(
                key,
                film.tmdb_id,
                a,
                b,
                smart,
                people,
                str(body.get("summary") or ""),
                source=src,
                status=status,
                confidence=conf,
                evidence=evidence,
            )
            out = out or row
        assert out is not None
        return out

    def _marcarr_tag(self, tid: str) -> dict[str, Any] | None:
        """A Marcarr tag by id (Marcarr's ids start ``tag_``, FansCut's are plain hex)."""
        if not tid.startswith("tag_"):
            return None
        for film in self._movies():
            vid = self.link.video(film)
            if not vid:
                continue
            for r in self.link.get(f"/videos/{vid}/tags", status="all").get("items") or []:
                if r["id"] == tid:
                    return {**r, "_film": film}
        raise NotFoundError(f"scene tag {tid} not found")

    def update_tag(self, tid: str, changes: dict[str, Any]) -> dict[str, Any]:
        cur = self._marcarr_tag(tid)
        if cur is None:
            return self.store.update_tag(tid, changes)
        film = cur["_film"]
        fields: dict[str, Any] = {}
        if "start" in changes or "end" in changes:
            fields["start"], fields["end"] = st.clean_range(
                changes.get("start", cur["start"]), changes.get("end", cur["end"])
            )
        if "tags" in changes:
            fields["tag"] = st.clean_tags(changes["tags"])[0]
        if "people" in changes:
            fields["people"] = self._person_ids(title_id(film), st.clean_people(changes["people"]))
        if "summary" in changes:
            fields["summary"] = str(changes["summary"] or "")[: st.MAX_SUMMARY]
        row = self.link.call("PATCH", f"/tags/{tid}", json=fields) if fields else cur
        if "status" in changes:
            status = changes["status"]
            if status not in st.STATUSES:
                raise FansCutError(f"status must be one of {', '.join(st.STATUSES)}")
            verb = {"accepted": "keep", "rejected": "drop", "proposed": "undo"}[status]
            row = self.link.post(f"/tags/{tid}/{verb}")
        return self._row(row, film, self._people_out(title_id(film)))

    def delete_tag(self, tid: str) -> None:
        if tid.startswith("tag_"):
            self.link.call("DELETE", f"/tags/{tid}")
        else:
            self.store.delete_tag(tid)

    # -- who is on screen ---------------------------------------------------------------
    def faces_at(self, key: str, t: float) -> dict[str, Any]:
        """Faces Marcarr matched near *t* (its faces step looks at a few frames per
        scene), as boxes in fractions of the frame, named from the cast."""
        film = self.film(key)
        vid = self.link.video(film)
        if vid is None:
            raise FansCutError("this film's faces haven't been looked at yet: analyse it first")
        info = self._info(vid)
        cast = {c["id"]: c for c in self._cast_rows(title_id(film))}
        rows = self.link.get(f"/videos/{vid}/faces").get("items") or []
        near = sorted((f for f in rows if abs(f["at"] - t) <= 2), key=lambda f: abs(f["at"] - t))
        best: dict[str, dict[str, Any]] = {}
        for f in near:
            if f["confirmed"] is False:
                continue
            slot = f["person"] or f["id"]
            if slot in best:
                continue
            known = bool(f["person"])
            c = cast.get(f["person"] or "") or {}
            best[slot] = {
                "box": f["box"],
                "score": f["score"],
                "name": c.get("name") if known else None,
                "character": c.get("character")
                or (self._readable(f["person"])["character"] if known else ""),
                "photo": c.get("photo") or "",
                "match": f["score"] if known else None,
                "sure": known and (f["confirmed"] is True or f["score"] >= 0.45),
                "at": f["at"],
            }
        return {
            "t": t,
            "width": info.get("width") or 0,
            "height": info.get("height") or 0,
            "faces": list(best.values()),
        }

    def xray(self, key: str, t: float) -> dict[str, Any]:
        """Who is on screen at *t* (Marcarr's scene there) and the tags covering it, with
        each person's other kept scenes."""
        film = self.film(key)
        here = [
            x for x in self.tags([key]) if x["status"] != "rejected" and x["start"] <= t < x["end"]
        ]
        vid = self.link.video(film)
        cast = {c["id"]: c for c in self._cast_rows(title_id(film))}
        scenes = (self.link.get(f"/videos/{vid}/scenes").get("items") or []) if vid else []
        now = next((s for s in scenes if s["start"] <= t < s["end"]), None)
        people: list[dict[str, Any]] = []
        for pid in (now.get("seen") or now.get("people") or []) if now else []:
            c = cast.get(pid) or {
                "name": "",
                "character": self._readable(pid)["character"],
                "photo": "",
            }
            others = [
                {
                    "start": s["start"],
                    "end": s["end"],
                    "tags": [r["tag"] for r in s["tags"] if r["status"] == "accepted"],
                }
                for s in scenes
                if pid in (s.get("seen") or s.get("people") or []) and s is not now
            ]
            people.append(
                {
                    "name": c["name"],
                    "character": c["character"],
                    "photo": c["photo"],
                    "scenes": others,
                }
            )
        # people a tag names but no scene shows (a tag made by hand, say)
        for x in here:
            for p in x["people"]:
                k = p.get("name") or p.get("character")
                if k and k not in {q.get("name") or q.get("character") for q in people}:
                    people.append({**p, "photo": "", "scenes": []})
        return {
            "t": t,
            "tags": [
                {
                    "id": x["id"],
                    "tags": x["tags"],
                    "summary": x["summary"],
                    "start": x["start"],
                    "end": x["end"],
                }
                for x in here
            ],
            "people": people,
        }

    # -- collections ----------------------------------------------------------------
    def collection(
        self,
        name: str,
        tag: str | None = None,
        status: str | None = None,
        keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Tagged scenes across a collection (by id, or name for old links), or across
        the films in *keys* (a franchise whose films don't all carry the Plex tag)."""
        from ..browse import collection_ids, resolve_collection

        movies = self._movies()
        name = resolve_collection(movies, name)
        if keys:
            want = set(keys)
            films = [f for f in self.films() if f["key"] in want]
            missing = want - {f["key"] for f in films}
            if missing:
                raise NotFoundError(f"films not found: {', '.join(sorted(missing))}")
        else:
            films = self.films(name)
        if not films:
            raise NotFoundError(f"no films in collection {name!r}")
        keys_ = [f["key"] for f in films]
        by_film: dict[str, list[dict[str, Any]]] = {f["key"]: [] for f in films}
        for x in self.tags(keys_, tag, status):
            by_film[x["film_key"]].append(x)
        counts: dict[str, int] = {}
        for x in self.tags(keys_):
            if x["status"] != "rejected":
                for t in x["tags"]:
                    counts[t] = counts.get(t, 0) + 1
        return {
            "id": collection_ids(movies).get(name, name),
            "name": name,
            "tag": tag,
            "films": [{**f, "scenes": by_film[f["key"]]} for f in films],
            "tags": [
                {"tag": t, "count": n} for t, n in sorted(counts.items(), key=lambda kv: -kv[1])
            ],
        }

    def to_timeline(
        self, name: str, tag: str, title: str = "", keys: list[str] | None = None
    ) -> dict[str, Any]:
        """Accepted scenes with *tag* across a collection (or the films in *keys*) -> a
        timeline cut, in film order."""
        t = st.norm_tag(tag)
        col = self.collection(name, t, "accepted", keys)
        name = col["name"]
        sources: dict[str, Any] = {}
        segments = []
        for f in col["films"]:
            if not f["scenes"] or not f["tmdb_id"]:
                continue
            m = self.film(f["key"])
            src = f"M{f['tmdb_id']}"
            dur = m.duration
            sources[src] = {
                "kind": "movie",
                "tmdb": f["tmdb_id"],
                "note": f["title"],
                **({"duration": round(float(dur), 3)} if dur else {}),
            }
            for x in f["scenes"]:
                segments.append(
                    {
                        "src": src,
                        "start": x["start"],
                        "end": x["end"],
                        **({"desc": x["summary"][:200]} if x["summary"] else {}),
                    }
                )
        if not segments:
            raise FansCutError(f"no accepted '{t}' scenes in {name} yet")
        label = title or f"{name}: {t.replace('-', ' ')}"
        doc = {
            "schema": 2,
            "kind": "timeline",
            "title": label[:200],
            "series": {"title": name},
            "sources": sources,
            "parts": [{"title": label[:200], "segments": segments}],
            "credits": {"author": "FansCut scene tags", "based_on": f"'{t}' scenes in {name}"},
        }
        first = next(f for f in col["films"] if f["scenes"] and f["tmdb_id"])
        return self.core.create_timeline(f"movie:{first['tmdb_id']}", doc)
