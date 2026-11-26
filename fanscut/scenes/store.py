"""SQLite storage for scene indexes, scene tags and cast lists."""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from typing import Any

from ..db import Database, dumps
from ..errors import FansCutError, NotFoundError

STATUSES = ("proposed", "accepted", "rejected")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_TAGS = 20
MAX_PEOPLE = 40
MAX_SUMMARY = 500
SMART = "fc:"  # FansCut's own (smart) tags; content tags have no prefix


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def norm_tag(tag: Any) -> str:
    """ "Great Hall" -> "great-hall"; "fc:No face" -> "fc:no-face" (FansCut's smart
    tags keep their prefix); rejects anything that isn't a short slug."""
    if not isinstance(tag, str):
        raise FansCutError("tags must be strings")
    raw = tag.strip().lower()
    prefix = SMART if raw.startswith(SMART) else ""
    t = re.sub(r"[^a-z0-9]+", "-", raw[len(prefix) :]).strip("-")
    if not TAG_RE.match(t):
        raise FansCutError(f"bad tag {tag!r} (letters, digits and dashes, up to 40)")
    return prefix + t


def clean_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list) or not tags:
        raise FansCutError("give at least one tag")
    out: list[str] = []
    for t in tags[:MAX_TAGS]:
        n = norm_tag(t)
        if n not in out:
            out.append(n)
    return out


def clean_people(people: Any) -> list[dict[str, str]]:
    if people is None:
        return []
    if not isinstance(people, list):
        raise FansCutError("people must be a list")
    out = []
    for p in people[:MAX_PEOPLE]:
        if isinstance(p, str):
            p = {"character": p}
        if not isinstance(p, dict):
            raise FansCutError("each person is a name or {name, character}")
        entry = {k: str(p[k]).strip()[:120] for k in ("name", "character") if p.get(k)}
        if entry:
            out.append(entry)
    return out


def clean_range(start: Any, end: Any, duration: float | None = None) -> tuple[float, float]:
    try:
        a, b = float(start), float(end)
    except (TypeError, ValueError) as exc:
        raise FansCutError("start and end must be seconds") from exc
    if a < 0 or b <= a:
        raise FansCutError("end must be after start, both from 0")
    if duration and b > duration + 5:
        raise FansCutError(f"end {b:.1f}s is past the film's end ({duration:.1f}s)")
    return round(a, 3), round(b, 3)


class SceneStore:
    def __init__(self, db: Database):
        self.db = db

    # -- index ------------------------------------------------------------------
    def index(self, film_key: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT doc, updated_at FROM scene_index WHERE film_key = ?", (film_key,))
        if row is None:
            return None
        return {**json.loads(row["doc"]), "updated_at": row["updated_at"]}

    def save_index(self, film_key: str, doc: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO scene_index (film_key, doc, updated_at) VALUES (?,?,?)",
            (film_key, dumps(doc), _now()),
        )

    def indexed(self) -> set[str]:
        return {r["film_key"] for r in self.db.query("SELECT film_key FROM scene_index")}

    def save_signals(self, film_key: str, doc: dict[str, Any]) -> None:
        """Signals live in the film's index document (re-indexing measures them again)."""
        doc = {**doc, "measured_at": _now()}
        self.db.execute(
            "UPDATE scene_index SET doc = json_set(doc, '$.signals', json(?)) WHERE film_key = ?",
            (dumps(doc), film_key),
        )

    def signals(self, film_key: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT json_extract(doc, '$.signals') AS s FROM scene_index WHERE film_key = ?",
            (film_key,),
        )
        return json.loads(row["s"]) if row and row["s"] else None

    def scene_counts(self) -> dict[str, int]:
        """Automatic scenes per indexed film, without loading the documents."""
        rows = self.db.query(
            "SELECT film_key, json_array_length(doc, '$.scenes') AS n FROM scene_index"
        )
        return {r["film_key"]: int(r["n"] or 0) for r in rows}

    # -- tags -------------------------------------------------------------------
    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["tags"] = json.loads(d["tags"])
        d["people"] = json.loads(d["people"])
        d["evidence"] = json.loads(d.get("evidence") or "[]")
        return d

    def add_tag(
        self,
        film_key: str,
        tmdb_id: int | None,
        start: float,
        end: float,
        tags: list[str],
        people: list[dict[str, str]],
        summary: str = "",
        source: str = "user",
        status: str = "proposed",
        confidence: float | None = None,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        if status not in STATUSES:
            raise FansCutError(f"status must be one of {', '.join(STATUSES)}")
        tid = uuid.uuid4().hex[:12]
        now = _now()
        self.db.execute(
            "INSERT INTO scene_tags (id, film_key, tmdb_id, start, end, tags, people, summary,"
            " source, status, confidence, evidence, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                film_key,
                tmdb_id,
                start,
                end,
                dumps(tags),
                dumps(people),
                summary[:MAX_SUMMARY],
                source[:80],
                status,
                confidence,
                dumps([str(x)[:60] for x in (evidence or [])][:12]),
                now,
                now,
            ),
        )
        return self.tag(tid)

    def tag(self, tid: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM scene_tags WHERE id = ?", (tid,))
        if r is None:
            raise NotFoundError(f"scene tag {tid} not found")
        return self._row(r)

    def update_tag(self, tid: str, changes: dict[str, Any]) -> dict[str, Any]:
        cur = self.tag(tid)
        fields: dict[str, Any] = {}
        if "start" in changes or "end" in changes:
            fields["start"], fields["end"] = clean_range(
                changes.get("start", cur["start"]), changes.get("end", cur["end"])
            )
        if "tags" in changes:
            fields["tags"] = dumps(clean_tags(changes["tags"]))
        if "people" in changes:
            fields["people"] = dumps(clean_people(changes["people"]))
        if "summary" in changes:
            fields["summary"] = str(changes["summary"] or "")[:MAX_SUMMARY]
        if "status" in changes:
            if changes["status"] not in STATUSES:
                raise FansCutError(f"status must be one of {', '.join(STATUSES)}")
            fields["status"] = changes["status"]
        if not fields:
            return cur
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE scene_tags SET {sets} WHERE id = ?", (*fields.values(), tid))  # noqa: S608
        return self.tag(tid)

    def delete_tag(self, tid: str) -> None:
        self.tag(tid)
        self.db.execute("DELETE FROM scene_tags WHERE id = ?", (tid,))

    def delete_source(self, film_key: str, source: str) -> int:
        """Drop every tag range of one film made by *source*."""
        with self.db.transaction():
            n = self.db.one(
                "SELECT COUNT(*) AS n FROM scene_tags WHERE film_key = ? AND source = ?",
                (film_key, source),
            )
            self.db.execute(
                "DELETE FROM scene_tags WHERE film_key = ? AND source = ?", (film_key, source)
            )
        return int(n["n"]) if n else 0

    def tags(
        self, film_keys: list[str] | None = None, tag: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM scene_tags WHERE 1=1", []
        if film_keys is not None:
            if not film_keys:
                return []
            sql += f" AND film_key IN ({','.join('?' * len(film_keys))})"
            args += film_keys
        if status:
            sql += " AND status = ?"
            args.append(status)
        rows = [self._row(r) for r in self.db.query(sql + " ORDER BY film_key, start", args)]
        if tag:
            want = norm_tag(tag)
            rows = [r for r in rows if want in r["tags"]]
        return rows

    def overlapping(
        self, film_key: str, start: float, end: float, tag: str
    ) -> dict[str, Any] | None:
        """An existing tag range with *tag* that overlaps [start, end] (to avoid duplicates)."""
        for r in self.tags([film_key], tag):
            if r["status"] != "rejected" and r["start"] < end and r["end"] > start:
                return r
        return None

    def tag_counts(self) -> list[dict[str, Any]]:
        counts: dict[str, dict[str, int]] = {}
        for r in self.db.query("SELECT tags, status FROM scene_tags"):
            for t in json.loads(r["tags"]):
                c = counts.setdefault(t, {"proposed": 0, "accepted": 0, "rejected": 0})
                c[r["status"]] += 1
        from . import vocab

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

    # -- cast -------------------------------------------------------------------
    def cast(self, film_key: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT doc FROM cast_cache WHERE film_key = ?", (film_key,))
        return json.loads(row["doc"]) if row else None

    def save_cast(self, film_key: str, doc: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cast_cache (film_key, doc, updated_at) VALUES (?,?,?)",
            (film_key, dumps(doc), _now()),
        )
