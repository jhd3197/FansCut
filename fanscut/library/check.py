"""Library check: is this library fit to cut? Every finding names the file and the fix.

Checks
  edition     runtime vs known theatrical/extended lengths; filename disagreement
  languages   audio/subtitle tags: original or English audio, forced subtitles
  incomplete  temp names (.tmp, muxing…), runtime far below every known edition,
              decode errors in the last 30 s
  missing     episodes/extras a known release has and the library doesn't;
              timeline sources the library can't resolve
  mismatch    codec, frame size, picture shape (black bars), HDR/SDR, frame
              rate across files that are cut together
  filename    Plex-style renames ("Title (Year) {edition-Extended}.mkv"); the
              check only proposes them, apply_rename() runs after confirmation

Findings are plain dicts: check, severity (error|warn|info), file, title,
message, fix. Nothing here writes to the library except apply_rename().
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database, dumps
from ..models import Movie, Series
from ..render import ffmpeg
from . import known, scan

Finding = dict[str, Any]
Prober = Callable[[str], dict[str, Any]]

TEMP_NAME = re.compile(r"(^muxing|\.tmp\b|\.part\b|\.partial\b|\.crdownload$|\.!qb$|^~)",
                       re.IGNORECASE)
EDITION_TAG = re.compile(r"\{edition-([^}]+)\}", re.IGNORECASE)
EDITION_WORDS = [("Extended", re.compile(r"extended", re.I)),
                 ("Theatrical", re.compile(r"theatrical", re.I)),
                 ("Director's Cut", re.compile(r"director'?s[ ._-]?cut", re.I)),
                 ("Unrated", re.compile(r"unrated", re.I))]
COMMENTARY = re.compile(r"commentary|комментар", re.I)
BAD_CHARS = re.compile(r'[<>:"/\\|?*]')
LANG_NAMES = {"eng": "English", "jpn": "Japanese", "spa": "Spanish", "fre": "French",
              "fra": "French", "chi": "Chinese", "zho": "Chinese", "rus": "Russian",
              "ukr": "Ukrainian", "ger": "German", "deu": "German", "ita": "Italian",
              "kor": "Korean", "por": "Portuguese", "cze": "Czech", "und": "undetermined"}


def lang_name(code: str) -> str:
    return LANG_NAMES.get((code or "und").lower(), code or "undetermined")


def finding(check: str, severity: str, file: str | None, title: str, message: str,
            fix: str = "") -> Finding:
    return {"check": check, "severity": severity, "file": file, "title": title,
            "message": message, "fix": fix}


# -- pure helpers -------------------------------------------------------------
def edition_from_name(name: str) -> str | None:
    m = EDITION_TAG.search(name)
    if m:
        return m.group(1).strip()
    for label, rx in EDITION_WORDS:
        if rx.search(name):
            return label
    return None


def edition_from_runtime(minutes: float, editions: dict[str, float]) -> str | None:
    """The known edition whose length is within max(3 min, 2 %) of `minutes`."""
    if not editions or minutes <= 0:
        return None
    label, length = min(editions.items(), key=lambda kv: abs(kv[1] - minutes))
    return label if abs(length - minutes) <= max(3.0, length * 0.02) else None


def plex_name(title: str, year: int | None, edition: str | None, ext: str) -> str:
    base = BAD_CHARS.sub("", title.replace(": ", " - ").replace(":", " -")).strip()
    base = f"{base} ({year})" if year else base
    if edition:
        base += f" {{edition-{edition}}}"
    return base + ext


def _real_audio(info: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in info.get("audio_tracks") or [] if not COMMENTARY.search(t.get("title", ""))]


def check_movie_file(path: str, title: str, year: int | None, info: dict[str, Any],
                     k: dict[str, Any] | None, sub_lang: str, audio_lang: str,
                     tail_errors: list[str] | None = None) -> tuple[list[Finding], str | None]:
    """All per-file checks for one movie file. Returns (findings, edition)."""
    out: list[Finding] = []
    name = Path(path).name
    minutes = (info.get("duration") or 0) / 60
    temp = bool(TEMP_NAME.search(name))

    # incomplete
    if temp:
        out.append(finding("incomplete", "warn", path, title,
                           f"{name} has a temporary name: it may be an unfinished mux or download",
                           "let the mux finish (or delete the file), then give it a "
                           "Plex-style name"))
    if minutes <= 0:
        out.append(finding("incomplete", "error", path, title,
                           "ffprobe reports no duration: the file is unreadable or truncated",
                           "re-download or re-mux the file"))
    if tail_errors:
        out.append(finding("incomplete", "error", path, title,
                           "decode errors in the last 30 s: " + " | ".join(tail_errors[:3]),
                           "the file is truncated or damaged; re-download or re-mux it"))

    # edition from runtime
    edition = None
    named = edition_from_name(name)
    if k and minutes > 0:
        edition = edition_from_runtime(minutes, k["editions"])
        lengths = ", ".join(f"{e} {m} min" for e, m in k["editions"].items())
        shortest = min(k["editions"].values())
        if edition and not named:
            out.append(finding("edition", "info", path, title,
                               f"runtime {minutes:.0f} min proves the {edition} edition "
                               "(the filename doesn't say)",
                               f"add {{edition-{edition}}} to the filename"))
        elif edition and named and named.lower() != edition.lower():
            out.append(finding("edition", "warn", path, title,
                               f"filename says {named} but the runtime ({minutes:.0f} min) "
                               f"matches the {edition} edition",
                               f"rename it with {{edition-{edition}}}"))
        elif not edition and minutes < shortest * 0.9:
            out.append(finding("incomplete", "error", path, title,
                               f"runtime {minutes:.0f} min is far shorter than any known "
                               f"edition ({lengths})",
                               "the file is probably cut short; re-download or re-mux it"))
        elif not edition:
            out.append(finding("edition", "info", path, title,
                               f"runtime {minutes:.0f} min matches no known edition ({lengths})",
                               "check which cut this is before using it in a recipe"))
    edition = edition or named

    # audio languages
    audio = _real_audio(info)
    langs = sorted({(t.get("language") or "und").lower() for t in audio})
    if not info.get("audio_tracks") and not info.get("audio"):
        out.append(finding("languages", "error", path, title, "no audio track",
                           "re-mux the file with its audio"))
    else:
        original = (k or {}).get("lang")
        want = [x for x in dict.fromkeys([original, audio_lang, "eng"]) if x]
        if original and not ({original, "eng"} & set(langs)):
            out.append(finding("languages", "warn", path, title,
                               f"no {lang_name(original)} or English audio; found "
                               + ", ".join(lang_name(x) for x in langs),
                               "replace the file with a release that has the original audio"))
        elif audio_lang and audio_lang not in langs:
            out.append(finding("languages", "warn", path, title,
                               f"no {lang_name(audio_lang)} audio (your preferred language); "
                               "found " + ", ".join(lang_name(x) for x in langs),
                               "change render.language or use a release with that track"))
        elif langs == ["und"]:
            out.append(finding("languages", "info", path, title,
                               "audio tracks have no language tag",
                               f"tag them, e.g. mkvpropedit --edit track:a1 --set language="
                               f"{want[0] if want else 'eng'}"))

    # forced subtitles
    if k and k.get("forced_subs"):
        subs = info.get("subtitle_tracks") or []
        forced = [s for s in subs if s.get("forced")]
        side = Path(path).with_suffix("")
        sidecar = any(Path(f"{side}.{sub_lang}.forced.{e}").is_file() for e in ("srt", "ass"))
        if not sidecar and not any((s.get("language") or "") == sub_lang for s in forced):
            found = ", ".join(sorted({lang_name(s.get("language", "")) for s in forced}))
            out.append(finding("languages", "warn", path, title,
                               f"no {lang_name(sub_lang)} forced subtitles for the film's "
                               "foreign-language lines"
                               + (f" (forced tracks found: {found})" if found else ""),
                               f"mux in a forced {lang_name(sub_lang)} track, or put "
                               f"'{side.name}.{sub_lang}.forced.srt' next to the file"))
    return out, edition


def mismatch(group: str, members: list[tuple[str, str, dict[str, Any], str | None]]
             ) -> list[Finding]:
    """members: (title, path, probe info, crop 'w:h:x:y' | None). One finding per property."""
    if len(members) < 2:
        return []
    out: list[Finding] = []

    def prop(label: str, get: Callable[[dict[str, Any], str | None], Any], fix: str) -> None:
        vals = [(t, p, get(i, c)) for t, p, i, c in members]
        vals = [v for v in vals if v[2] not in (None, "")]
        counts = Counter(v[2] for v in vals)
        if len(counts) < 2:
            return
        common = counts.most_common(1)[0][0]
        odd = [v for v in vals if v[2] != common] or vals
        listing = "; ".join(f"{t}: {v}" for t, _p, v in vals)
        f = finding("mismatch", "warn", odd[0][1], group,
                    f"{label} differs within {group}: {listing}", fix)
        f["files"] = [v[1] for v in odd]  # the odd ones out
        out.append(f)

    prop("video codec", lambda i, c: (i.get("video") or {}).get("codec"),
         "fine to cut together, but the render re-encodes (no stream copy)")
    prop("frame size", lambda i, c: "{}x{}".format((i.get("video") or {}).get("width"),
                                                   (i.get("video") or {}).get("height")),
         "the render scales/pads every segment to one frame size")
    prop("picture shape", lambda i, c: _shape(i, c),
         "a file has black bars inside the picture; crop it to the shared shape "
         "(segment video.crop) so the bars don't jump between segments")
    prop("HDR", lambda i, c: ((i.get("video") or {}).get("hdr") or "").replace("DV+", ""),
         "mixing HDR and SDR needs tone-mapping or a grade; use matching releases if you can")
    boxed = [(t, p, i, c) for t, p, i, c in members if _bars(i, c)]
    if boxed and len(boxed) < len(members):
        for t, p, i, c in boxed:
            v = i.get("video") or {}
            cw, ch, _x, y = (int(n) for n in (c or "0:0:0:0").split(":"))
            out.append(finding(
                "mismatch", "warn", p, group,
                f"{t}: the {v.get('width')}x{v.get('height')} frame holds a {cw}x{ch} picture "
                f"({y} px black bars inside it); the other files in {group} don't",
                f"crop it (segment video.crop, or crop {cw}:{ch}:{_x}:{y}) so the bars "
                "don't appear and vanish at every cut"))
    before = len(out)
    prop("Dolby Vision", lambda i, c: "DV" if "DV" in ((i.get("video") or {}).get("hdr") or "")
         else "no DV", "the render keeps the shared HDR10 base layer and drops Dolby Vision")
    for f in out[before:]:
        f["severity"] = "info"
    prop("frame rate", lambda i, c: round((i.get("video") or {}).get("fps") or 0, 3) or None,
         "the render conforms every segment to one frame rate")
    return out


def _bars(info: dict[str, Any], crop: str | None) -> bool:
    """Black bars baked into the frame (cropped picture over 5 % smaller)."""
    v = info.get("video") or {}
    if not crop or not v.get("width") or not v.get("height"):
        return False
    cw, ch = (int(x) for x in crop.split(":")[:2])
    return bool(ch < v["height"] * 0.95 or cw < v["width"] * 0.95)


def _shape(info: dict[str, Any], crop: str | None) -> str | None:
    v = info.get("video") or {}
    w, h = v.get("width"), v.get("height")
    if crop:
        cw, ch = (int(x) for x in crop.split(":")[:2])
        w, h = cw, ch
    if not w or not h:
        return None
    return f"{w / h:.1f}:1"  # 2.39 vs 2.40 is the same shape; bars change it by far more


# -- the checker --------------------------------------------------------------
class LibraryChecker:
    def __init__(self, cfg: Config, db: Database, prober: Prober, library: Any):
        self.cfg = cfg
        self.db = db
        self.prober = prober
        self.library = library

    # cached per-file probes that cost a decode
    def _cached(self, path: str, kind: str, fn: Callable[[], Any]) -> Any:
        st = os.stat(path)
        row = self.db.one("SELECT mtime, size, result FROM file_checks WHERE path=? AND kind=?",
                          (path, kind))
        if row and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
            return json.loads(row["result"])
        val = fn()
        self.db.execute("INSERT OR REPLACE INTO file_checks VALUES (?,?,?,?,?)",
                        (path, kind, st.st_mtime, st.st_size, dumps(val)))
        return val

    def tail_errors(self, path: str) -> list[str]:
        def run() -> list[str]:
            cmd = [self.cfg.ffmpeg.binary, "-v", "error", "-sseof", "-30", "-i", path,
                   "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                      timeout=600, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return [f"decode failed: {exc.__class__.__name__}"]
            lines = [x.strip() for x in proc.stderr.splitlines() if x.strip()]
            if proc.returncode != 0:
                return lines[-3:] or [f"ffmpeg exited {proc.returncode}"]
            # a handful of benign warnings is normal; a flood means damage
            return lines[:3] if len(lines) > 5 else []
        result: list[str] = self._cached(path, "tail", run)
        return result

    def crop(self, path: str, duration: float) -> str | None:
        def run() -> str | None:
            try:
                return ffmpeg.detect_crop(self.cfg.ffmpeg, path, duration * 0.3, 4.0)
            except Exception:  # noqa: BLE001 - shape is a nicety
                return None
        result: str | None = self._cached(path, "crop2", run)
        return result

    def _probe(self, path: str) -> dict[str, Any] | None:
        try:
            return self.prober(path)
        except Exception:  # noqa: BLE001 - reported as an unreadable file
            return None

    def movie_files(self, m: Movie) -> list[str]:
        """Every video file behind a movie (folder movies may hold several)."""
        if m.source == "folder" and m.file:
            return [str(p) for p in scan._videos(Path(m.file).parent)]
        return [m.file] if m.file else []

    def check_file(self, path: str, title: str, year: int | None, tmdb: int | None,
                   deep: bool = False) -> tuple[list[Finding], str | None, dict[str, Any] | None]:
        info = self._probe(path)
        if info is None:
            return [finding("incomplete", "error", path, title, "ffprobe cannot read the file",
                            "re-download or re-mux it")], None, None
        k = known.movie(tmdb, title, year)
        minutes = (info.get("duration") or 0) / 60
        suspicious = bool(TEMP_NAME.search(Path(path).name)) or minutes <= 0 or bool(
            k and minutes < min(k["editions"].values()) * 0.9)
        tail = self.tail_errors(path) if (deep or suspicious) and minutes > 0 else None
        found, edition = check_movie_file(path, title, year, info, k, self.sub_lang,
                                          self.cfg.render.language.lower(), tail)
        return found, edition, info

    @property
    def sub_lang(self) -> str:
        return "eng"

    def run(self, q: str = "", deep: bool = False,
            timelines: list[tuple[str, dict[str, Any], dict[str, Any]]] | None = None
            ) -> dict[str, Any]:
        """The whole check. timelines: (title, doc, {src: file|None}) for each timeline."""
        needle = known.norm(q)
        findings: list[Finding] = []
        renames: list[dict[str, str]] = []
        files: list[dict[str, Any]] = []
        movies, m_err = self.library.list_movies()
        series, s_err = self.library.list_series()
        for src, err in {**m_err, **s_err}.items():
            findings.append(finding("missing", "error", None, src, f"{src} unreachable: {err}",
                                    "fix the connection on the Settings page"))

        probed: dict[str, tuple[str, dict[str, Any]]] = {}
        sets: dict[str, list[str]] = {}
        for m in movies:
            if needle and needle not in known.norm(m.title):
                continue
            paths = self.movie_files(m)
            if not paths:
                continue
            if len(paths) > 1:
                findings.append(finding(
                    "filename", "info", str(Path(paths[0]).parent), m.title,
                    f"{len(paths)} video files in one movie folder: "
                    + ", ".join(Path(p).name for p in paths),
                    "Plex shows them as versions; keep one per edition and name each "
                    "with {edition-…}"))
            for p in paths:
                found, edition, info = self.check_file(p, m.title, m.year, m.tmdb_id, deep)
                findings += found
                k = known.movie(m.tmdb_id, m.title, m.year)
                if info is not None:
                    probed[p] = (m.title, info)
                    v = info.get("video") or {}
                    files.append({"file": p, "title": m.title, "edition": edition,
                                  "minutes": round((info.get("duration") or 0) / 60, 1),
                                  "video": f"{v.get('codec')} {v.get('width')}x{v.get('height')}"
                                           f" {v.get('hdr', '')}".strip(),
                                  "audio": [t.get("language") for t in _real_audio(info)],
                                  "forced_subs": [s.get("language")
                                                  for s in info.get("subtitle_tracks") or []
                                                  if s.get("forced")]})
                    if k:
                        sets.setdefault(k["set"], []).append(p)
                if TEMP_NAME.search(Path(p).name):
                    continue
                ext = Path(p).suffix
                want = plex_name(m.title, m.year, edition, ext)
                if Path(p).name != want:
                    renames.append({"file": p, "to": str(Path(p).with_name(want)),
                                    "reason": "Plex-style name"
                                    + (f" with the {edition} edition" if edition else "")})
            for c in m.collections:
                sets.setdefault(c, []).extend(x for x in paths if x in probed)

        # mismatch within sets/collections and timelines
        groups = {name: sorted(set(ps)) for name, ps in sets.items()}
        for title, _doc, srcs in timelines or []:
            ps = sorted({f for f in srcs.values() if f})
            for p in ps:
                if p not in probed:
                    info = self._probe(p)
                    if info:
                        probed[p] = (Path(p).stem, info)
            groups[f"timeline “{title}”"] = [p for p in ps if p in probed]
            for src, f in srcs.items():
                if not f:
                    findings.append(finding("missing", "warn", None, title,
                                            f"timeline “{title}” needs {src}, which isn't "
                                            "in the library",
                                            "add that file, or mark the source optional"))
        for name, ps in groups.items():
            if len(ps) < 2:
                continue
            members = [(probed[p][0], p, probed[p][1],
                        self.crop(p, probed[p][1].get("duration") or 0)) for p in ps]
            findings += mismatch(name, members)

        # missing pieces for known series (+ Sonarr's own missing list)
        for s in series:
            if needle and needle not in known.norm(s.title):
                continue
            findings += self.check_series(s)

        order = {"error": 0, "warn": 1, "info": 2}
        findings.sort(key=lambda f: (order[f["severity"]], f["title"], f["check"]))
        counts = Counter(f["severity"] for f in findings)
        return {"summary": {"files": len(files), "error": counts["error"],
                            "warn": counts["warn"], "info": counts["info"]},
                "findings": findings, "renames": renames, "files": files}

    def _series_languages(self, title: str, eps: list[Any], original: str) -> list[Finding]:
        """Audio languages of every episode: a missing original, or a set that changes."""
        sets: dict[tuple[str, ...], list[str]] = {}
        first_file: dict[tuple[str, ...], str] = {}
        for e in eps:
            if not e.file or e.season == 0:
                continue
            info = self._probe(e.file)
            if not info:
                continue
            langs = tuple(sorted({(t.get("language") or "und").lower()
                                  for t in _real_audio(info)}))
            sets.setdefault(langs, []).append(e.id)
            first_file.setdefault(langs, e.file)
        out = []
        for langs, ids in sets.items():
            if original not in langs and "eng" not in langs:
                out.append(finding("languages", "warn", first_file[langs], title,
                                   f"{ranges(ids)}: no {lang_name(original)} or English audio; "
                                   "found " + ", ".join(lang_name(x) for x in langs),
                                   "replace those episodes, or fix wrong tags with mkvpropedit"))
        if len(sets) > 1:
            common = max(sets, key=lambda k: len(sets[k]))
            odd = {k: v for k, v in sets.items() if k != common}
            parts = [f"{ranges(v)}: {', '.join(lang_name(x) for x in k)}"
                     for k, v in sorted(odd.items(), key=lambda kv: kv[1][0])]
            lost = sorted(set(common) - set().union(*odd))
            n = sum(len(v) for v in odd.values())
            out.append(finding(
                "languages", "warn", first_file[next(iter(odd))], title,
                f"audio languages change across episodes — most have "
                f"{', '.join(lang_name(x) for x in common)}, but " + "; ".join(parts),
                (f"{n} episode(s) lack the {', '.join(lang_name(x) for x in lost)} track; "
                 "a cut that switches mid-way will jump languages — replace those episodes "
                 "or render with a language every episode has") if lost else
                "check the odd episodes' releases"))
        return out

    def check_series(self, s: Series) -> list[Finding]:
        k = known.series(s.tvdb_id, s.title)
        sonarr = s.source == "sonarr"
        if not k and not sonarr:
            return []
        try:
            eps = self.library.episodes(s.key)
        except Exception as exc:  # noqa: BLE001
            return [finding("missing", "error", None, s.title, f"episodes unreadable: {exc}",
                            "check the library connection")]
        have = {e.id for e in eps if e.file}
        out: list[Finding] = []
        if k:
            listed: set[str] = set()
            for extra in k.get("extras", []):
                if "episodes" in extra:
                    gone = [x for x in extra["episodes"] if x not in have]
                    listed |= set(extra["episodes"])
                    if gone:
                        out.append(finding("missing", "warn", None, s.title,
                                           f"{extra['name']} missing ({', '.join(gone)})",
                                           "add those episodes in Sonarr/Plex"))
                elif "season" in extra and not any(e.season == extra["season"] and e.file
                                                   for e in eps):
                    out.append(finding("missing", "info", None, s.title,
                                       f"{extra['name']} missing (season "
                                       f"{extra['season']:02d} is empty)",
                                       "add the specials if your cut uses them"))
            want = [f"S{sn:02d}E{n:02d}" for sn, cnt in k["seasons"].items()
                    for n in range(1, cnt + 1)]
            gone = [x for x in want if x not in have and x not in listed]
            total = sum(k["seasons"].values())
            n_have = sum(1 for x in want if x in have)
            if gone or n_have < total:
                out.append(finding("missing", "warn" if gone else "info", None, s.title,
                                   f"{n_have} of {total} episodes"
                                   + (f"; missing {', '.join(gone[:10])}"
                                      + ("…" if len(gone) > 10 else "") if gone else ""),
                                   "add the missing episodes in Sonarr/Plex"))
            out += self._series_languages(s.title, eps, k["lang"])
        elif sonarr:
            gone = [e.id for e in eps if not e.file and e.season > 0]
            if gone:
                out.append(finding("missing", "info", None, s.title,
                                   f"{len(gone)} episode(s) without a file: "
                                   + ", ".join(gone[:10]) + ("…" if len(gone) > 10 else ""),
                                   "search for them in Sonarr"))
        return out


def ranges(ids: list[str]) -> str:
    """['S01E01', 'S01E02', 'S01E03', 'S03E03'] -> 'S01E01–S01E03, S03E03'."""
    out: list[list[str]] = []
    for x in ids:
        if out and x[:3] == out[-1][-1][:3] and int(x[4:]) == int(out[-1][-1][4:]) + 1:
            out[-1].append(x)
        else:
            out.append([x])
    return ", ".join(r[0] if len(r) == 1 else f"{r[0]}–{r[-1]}" for r in out)


def apply_rename(proposals: list[dict[str, str]], file: str, to: str) -> dict[str, str]:
    """Rename one file, only if (file, to) is one of the check's own proposals."""
    if not any(p["file"] == file and p["to"] == to for p in proposals):
        raise ValueError("not a proposed rename; run the library check again")
    src, dst = Path(file), Path(to)
    if src.parent != dst.parent:
        raise ValueError("renames stay in the same folder")
    if dst.exists():
        raise ValueError(f"{dst.name} already exists")
    src.rename(dst)
    return {"file": file, "to": to}
