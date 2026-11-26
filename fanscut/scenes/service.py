"""Scene tagging for the API, the MCP server and the jobs: one place over Core."""

from __future__ import annotations

import contextlib
import hashlib
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import FansCutError, NotFoundError
from . import cast as cast_mod
from . import index as idx
from . import store as st
from . import tagger

if TYPE_CHECKING:
    from ..core import Core
    from ..jobs import JobContext

MAX_CLIP = 300.0
MAX_SHEET_TILES = 24


class SceneService:
    def __init__(self, core: Core):
        self.core = core
        self.store = st.SceneStore(core.db)

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

    def films(self, collection: str | None = None) -> list[dict[str, Any]]:
        from ..browse import _collections_of, resolve_collection

        indexed = self.store.indexed()
        tags = self.store.tags()
        out = []
        movies = self._movies()
        if collection:
            collection = resolve_collection(movies, collection)
        for m in movies:
            cols = _collections_of(m)
            if collection and collection not in cols:
                continue
            mine = [t for t in tags if t["film_key"] == m.key]
            out.append(
                {
                    "key": m.key,
                    "title": m.title,
                    "year": m.year,
                    "tmdb_id": m.tmdb_id,
                    "duration": m.duration,
                    "on_disk": bool(m.file),
                    "collections": cols,
                    "indexed": m.key in indexed,
                    "tags": {
                        "proposed": sum(t["status"] == "proposed" for t in mine),
                        "accepted": sum(t["status"] == "accepted" for t in mine),
                    },
                }
            )
        return sorted(out, key=lambda f: (f["year"] or 0, f["title"]))

    def overview(self, key: str, with_lines: bool = True) -> dict[str, Any]:
        m = self.film(key)
        doc = self.store.index(key)
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
            "tags": self.store.tags([key]),
            "cast": (self.store.cast(key) or {}).get("cast", []),
        }

    def _index_or_fail(self, key: str) -> dict[str, Any]:
        doc = self.store.index(key)
        if doc is None:
            raise FansCutError("this film has no scene index yet: index it first")
        return doc

    # -- index job ----------------------------------------------------------------
    def submit_index(self, key: str, transcribe: bool = False) -> dict[str, Any]:
        m, _ = self._file(key)
        return self.core.worker.submit(
            "scene_index",
            f"Index scenes of {m.title}",
            {"key": key, "transcribe": bool(transcribe)},
        )

    def job_index(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        key = payload["key"]
        _, path = self._file(key)
        ff = self.core.cfg.ffmpeg
        doc = idx.build(
            path,
            ffmpeg=ff.binary,
            ffprobe=ff.probe_binary,
            transcribe=bool(payload.get("transcribe")),
            cancel=ctx.cancel,
            progress=ctx.progress,
        )
        self.store.save_index(key, doc)
        with contextlib.suppress(FansCutError):
            self.get_cast(key)
        return {
            "key": key,
            "shots": len(doc["shots"]),
            "scenes": len(doc["scenes"]),
            "lines": len(doc["lines"]),
            "subtitles": doc["subtitles"]["kind"],
        }

    # -- looking at the film ------------------------------------------------------
    def transcript(
        self, key: str, start: float = 0, end: float | None = None
    ) -> list[dict[str, Any]]:
        doc = self._index_or_fail(key)
        hi = end if end is not None else float("inf")
        return [x for x in doc.get("lines") or [] if x["end"] > start and x["start"] < hi]

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
        if not out.is_file():
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
                    "could not cut the clip: "
                    + (proc.stderr or b"").decode("utf-8", "replace")[-300:]
                )
            tmp.replace(out)
        return out

    # -- cast -----------------------------------------------------------------------
    def get_cast(self, key: str, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh and (cached := self.store.cast(key)) is not None:
            return list(cached.get("cast") or [])
        m = self.film(key)
        people: list[dict[str, Any]] = []
        source = ""
        plex = self.core.library.plex()
        if plex and m.key.startswith("plexmovie:"):
            people, source = cast_mod.fetch_plex(plex, m.key.split(":", 1)[1]), "plex"
        if not people and m.tmdb_id and self.core.cfg.catalogues.tmdb_api_key:
            people = cast_mod.fetch_tmdb(
                self.core.cfg.catalogues.tmdb_api_key, m.tmdb_id, self.core.session
            )
            source = "tmdb"
        if not source:
            raise FansCutError("no cast source: the film isn't in Plex and no TMDB API key is set")
        self.store.save_cast(key, {"source": source, "cast": people})
        return people

    def _cast_or_empty(self, key: str) -> list[dict[str, Any]]:
        try:
            return self.get_cast(key)
        except FansCutError:
            return []

    # -- tags -----------------------------------------------------------------------
    def add_tag(self, key: str, body: dict[str, Any], source: str = "user") -> dict[str, Any]:
        m = self.film(key)
        doc = self.store.index(key)
        a, b = st.clean_range(
            body.get("start"), body.get("end"), (doc or {}).get("duration") or m.duration
        )
        if doc and body.get("snap", True):
            a, b = idx.snap(doc, a), idx.snap(doc, b)
        people = cast_mod.resolve_people(
            self._cast_or_empty(key), st.clean_people(body.get("people"))
        )
        status = body.get("status") or ("accepted" if source == "user" else "proposed")
        conf = body.get("confidence")
        return self.store.add_tag(
            key,
            m.tmdb_id,
            a,
            b,
            st.clean_tags(body.get("tags")),
            people,
            str(body.get("summary") or ""),
            source=str(body.get("source") or source),
            status=status,
            confidence=float(conf) if conf is not None else None,
        )

    def update_tag(self, tid: str, changes: dict[str, Any]) -> dict[str, Any]:
        if "people" in changes:
            row = self.store.tag(tid)
            changes = {
                **changes,
                "people": cast_mod.resolve_people(
                    self._cast_or_empty(row["film_key"]), st.clean_people(changes["people"])
                ),
            }
        return self.store.update_tag(tid, changes)

    def delete_tag(self, tid: str) -> None:
        self.store.delete_tag(tid)

    def xray(self, key: str, t: float) -> dict[str, Any]:
        """Who is on screen at *t*: the people of every non-rejected tag covering it."""
        here = [
            x
            for x in self.store.tags([key])
            if x["status"] != "rejected" and x["start"] <= t < x["end"]
        ]
        cast = {c["name"]: c for c in self._cast_or_empty(key)}
        seen: dict[str, dict[str, Any]] = {}
        for x in here:
            for p in x["people"]:
                k = p.get("name") or p.get("character") or ""
                if k and k not in seen:
                    seen[k] = {**p, "photo": (cast.get(p.get("name", "")) or {}).get("photo", "")}
        others = {}
        for k, p in seen.items():
            name = p.get("name")
            if name:
                others[k] = [
                    {"start": x["start"], "end": x["end"], "tags": x["tags"]}
                    for x in self.store.tags([key])
                    if x["status"] == "accepted" and any(q.get("name") == name for q in x["people"])
                ]
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
            "people": [{**p, "scenes": others.get(k, [])} for k, p in seen.items()],
        }

    # -- AI job ---------------------------------------------------------------------
    def submit_ai(
        self, key: str, tag: str, description: str = "", verify: bool = True, sweep: bool = False
    ) -> dict[str, Any]:
        m, _ = self._file(key)
        self._index_or_fail(key)
        t = st.norm_tag(tag)
        if not self.core.cfg.ai.model:
            raise FansCutError("set an AI model in Settings first")
        return self.core.worker.submit(
            "scene_tag",
            f"Find '{t}' in {m.title}",
            {
                "key": key,
                "tag": t,
                "description": description[:500],
                "verify": bool(verify),
                "sweep": bool(sweep),
            },
        )

    def job_ai(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        key, tag = payload["key"], payload["tag"]
        desc = payload.get("description") or ""
        m, path = self._file(key)
        doc = self._index_or_fail(key)
        cfg = self.core.cfg.ai
        cast = self._cast_or_empty(key)
        title = f"{m.title} ({m.year})" if m.year else m.title

        def text_progress(p: float, msg: str) -> None:
            ctx.progress(0.5 * p, msg)

        cands = tagger.text_pass(cfg, doc, title, tag, desc, cast, ctx.cancel, text_progress)
        if payload.get("sweep"):
            cands += tagger.sweep_candidates(doc)
        cands = tagger.merge(cands)
        model = cfg.vision_model or cfg.model
        made, dropped = [], 0
        for i, c in enumerate(cands):
            if ctx.cancel.is_set():
                break
            ctx.progress(0.5 + 0.5 * i / max(1, len(cands)), f"Checking {i + 1}/{len(cands)}")
            summary, people, conf = c["why"], c["people"], c["confidence"]
            if payload.get("verify", True):
                from prompture.media.understand import contact_sheet, sample_times

                cols, rows, tile_h = tagger.sheet_shape(c["end"] - c["start"])
                times = sample_times(c["start"], c["end"], cols * rows)
                sheet = contact_sheet(path, times=times, cols=cols, rows=rows, tile_height=tile_h)
                check = tagger.look(cfg, sheet, title, tag, desc, cast)
                if not check.match:
                    dropped += 1
                    continue
                c["start"], c["end"] = tagger.refine(c, check, times)
                summary = check.summary or summary
                # Edge pass: frames 5 s apart around each edge pin it down.
                ecols, erows = tagger.EDGE_GRID
                for which in ("start", "end"):
                    lo, hi = tagger.edge_window(c[which], doc.get("duration"))
                    etimes = sample_times(lo, hi, ecols * erows)
                    esheet = contact_sheet(path, times=etimes, cols=ecols, rows=erows)
                    t = tagger.edge_time(
                        etimes, tagger.edge(cfg, esheet, title, tag, desc, which, summary), which
                    )
                    if t is not None:
                        c[which] = t
                if c["end"] - c["start"] < 3:
                    dropped += 1
                    continue
                people = list(dict.fromkeys([*check.people, *people]))
                conf = (conf + check.confidence) / 2
            # edges on a camera cut nobody talks across, so no line is cut off
            a, b = idx.quiet_start(doc, c["start"]), idx.quiet_end(doc, c["end"])
            if b - a < 3 or self.store.overlapping(key, a, b, tag):
                continue
            made.append(
                self.add_tag(
                    key,
                    {
                        "start": a,
                        "end": b,
                        "tags": [tag],
                        "people": people,
                        "summary": summary,
                        "confidence": round(conf, 2),
                        "snap": False,
                        "status": "proposed",
                    },
                    source=f"ai:{model}",
                )["id"]
            )
        return {
            "key": key,
            "tag": tag,
            "candidates": len(cands),
            "proposed": len(made),
            "rejected_by_look": dropped,
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
        tags = self.store.tags([f["key"] for f in films], tag, status)
        by_film: dict[str, list[dict[str, Any]]] = {f["key"]: [] for f in films}
        for x in tags:
            by_film[x["film_key"]].append(x)
        counts: dict[str, int] = {}
        for x in self.store.tags([f["key"] for f in films]):
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
            dur = m.duration or (self.store.index(f["key"]) or {}).get("duration")
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
