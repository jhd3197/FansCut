"""AI posters for a cut: an image model draws a poster from the cut's best stills.

A take is made from a few stills of the cut (the user's own footage, sent to
the image model as references) and a prompt from the cut's title, franchise
and the tags of its scenes. Takes and the chosen poster are files under
``<data_dir>/posters/<cut id>/``; the chosen one is what the cut page shows
and what a build writes as poster.jpg. It never leaves the server: a shared
recipe's ``poster`` stays an https URL.

Generation runs in a thread of the server that started it, not in the job
queue: a take is short, and several FansCut versions may share one queue.
The API key comes from the environment (Prompture reads it); it is never
written to a file.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import FansCutError, NotFoundError

if TYPE_CHECKING:
    from .core import Core

log = logging.getLogger("fanscut.posters")

MAX_STILLS = 4  # reference images per take
MAX_TAKES = 6  # older takes are deleted
SIZE = "1024x1536"  # 2:3, a film poster
TAKE_RE = re.compile(r"^take-\d{10,13}\.png$")
USE_KINDS = ("frame", "title")  # posters drawn in the browser
MAX_SIDE = 4000  # px, for a poster drawn in the browser

PROMPT = """\
A vertical film poster (2:3) for a fan-made cut called "{title}"{franchise}.
{refs}Show the places, objects, light and atmosphere; do not depict any real
person's face: people only as small figures, from behind or as silhouettes.
Compose one poster, not a collage. Mood and setting: {tags}.
{title_line} No other text, no logos, no studio names, no credits block.
"""
REFS_LINE = "The reference images are frames of the setting: match their places and colours.\n"


def prompt_for(
    title: str, franchise: str, tags: list[str], with_title: bool = True, refs: bool = True
) -> str:
    return PROMPT.format(
        refs=REFS_LINE if refs else "",
        title=title,
        franchise=f", from {franchise}" if franchise and franchise != title else "",
        tags=", ".join(tags) or "taken from the frames",
        title_line=(
            f'Put the title "{title}" in clean lettering near the bottom.'
            if with_title
            else "No title text."
        ),
    )


def png(data: bytes) -> bytes:
    """Any image the model may reject as JPEG, as PNG."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        out = io.BytesIO()
        im.convert("RGB").save(out, "PNG")
        return out.getvalue()


def image_bytes(img: Any) -> bytes:
    """Prompture's ImageContent (base64 in .data), base64 text or bytes -> bytes."""
    import base64

    if img is None:
        raise FansCutError("the image model returned no image")
    if isinstance(img, (bytes, bytearray)):
        return bytes(img)
    data = getattr(img, "data", img)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return base64.b64decode(str(data).split(",", 1)[-1])


def to_jpeg(src: Path, dst: Path) -> None:
    from PIL import Image

    with Image.open(src) as im:
        im.convert("RGB").save(dst, "JPEG", quality=90)


class Posters:
    def __init__(self, core: Core):
        self.core = core
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- files ----------------------------------------------------------------------
    def folder(self, tid: str) -> Path:
        self.core.get_timeline(tid, with_doc=False)  # 404 for an unknown cut
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tid):
            raise NotFoundError("no such cut")
        return self.core.cfg.data_dir / "posters" / tid

    def takes(self, tid: str) -> list[str]:
        d = self.folder(tid)
        if not d.is_dir():
            return []
        return sorted((p.name for p in d.iterdir() if TAKE_RE.match(p.name)), reverse=True)

    def take_path(self, tid: str, name: str) -> Path:
        if not TAKE_RE.match(name):
            raise NotFoundError("no such take")
        p = self.folder(tid) / name
        if not p.is_file():
            raise NotFoundError("no such take")
        return p

    def chosen(self, tid: str) -> Path | None:
        p = self.folder(tid) / "chosen.png"
        return p if p.is_file() else None

    def state(self, tid: str) -> dict[str, Any]:
        meta = self._meta(tid)
        with self._lock:
            task = dict(self._tasks.get(tid) or {})
        return {
            "takes": [{"name": n, **(meta.get(n) or {})} for n in self.takes(tid)],
            "chosen": meta.get("chosen") if self.chosen(tid) else None,
            "task": task or None,
            "model": self.core.cfg.posters.ai_model,
        }

    def _meta_path(self, tid: str) -> Path:
        return self.folder(tid) / "takes.json"

    def _meta(self, tid: str) -> dict[str, Any]:
        p = self._meta_path(tid)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except (OSError, ValueError):
            return {}

    def _save_meta(self, tid: str, meta: dict[str, Any]) -> None:
        p = self._meta_path(tid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    def keep(self, tid: str, name: str) -> dict[str, Any]:
        import shutil

        src = self.take_path(tid, name)
        shutil.copyfile(src, src.parent / "chosen.png")
        meta = self._meta(tid)
        meta["chosen"] = name
        self._save_meta(tid, meta)
        return self.state(tid)

    def discard(self, tid: str, name: str) -> dict[str, Any]:
        self.take_path(tid, name).unlink()
        meta = self._meta(tid)
        meta.pop(name, None)
        self._save_meta(tid, meta)
        return self.state(tid)

    def use_image(self, tid: str, data: bytes, kind: str) -> dict[str, Any]:
        """A poster drawn in the browser (a frame from the cut with its title, or the
        title only) as the cut's poster, in place of any AI take (takes are kept)."""
        from PIL import Image, UnidentifiedImageError

        if kind not in USE_KINDS:
            raise FansCutError("kind is frame or title")
        try:
            with Image.open(io.BytesIO(data)) as im:
                if max(im.size) > MAX_SIDE or min(im.size) < 16:
                    raise FansCutError("the poster is too big or too small")
                out = io.BytesIO()
                im.convert("RGB").save(out, "PNG")
        except (UnidentifiedImageError, OSError) as exc:
            raise FansCutError("that is not an image") from exc
        folder = self.folder(tid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "chosen.png").write_bytes(out.getvalue())
        meta = self._meta(tid)
        meta["chosen"] = f"{kind}-{int(time.time() * 1000)}"
        self._save_meta(tid, meta)
        return self.state(tid)

    def clear(self, tid: str) -> dict[str, Any]:
        p = self.chosen(tid)
        if p:
            p.unlink()
        meta = self._meta(tid)
        meta.pop("chosen", None)
        self._save_meta(tid, meta)
        return self.state(tid)

    def write_for_build(self, tid: str, show_dir: Path) -> str | None:
        """poster.jpg next to a build from the chosen AI poster, if there is one."""
        try:
            src = self.chosen(tid)
        except NotFoundError:
            return None
        if src is None:
            return None
        dst = show_dir / "poster.jpg"
        to_jpeg(src, dst)
        return str(dst)

    # -- the prompt -------------------------------------------------------------------
    def describe(self, tid: str) -> tuple[str, str, list[str]]:
        """(title, franchise, the most common content tags of the cut's scenes)."""
        from .scenes import vocab

        doc = self.core.get_timeline(tid)["doc"]
        title = str(doc.get("title") or "Untitled cut")
        franchise = str((doc.get("series") or {}).get("title") or "")
        films = {f["tmdb_id"]: f["key"] for f in self.core.scenes.films() if f.get("tmdb_id")}
        counts: Counter[str] = Counter()
        for part in doc.get("parts") or []:
            for seg in part.get("segments") or []:
                src = (doc.get("sources") or {}).get(seg.get("src")) or {}
                key = films.get(src.get("tmdb"))
                if not key:
                    continue
                for row in self.core.scenes.store.tags([key]):
                    if row["status"] == "rejected":
                        continue
                    if row["start"] < seg["end"] and row["end"] > seg["start"]:
                        for t in row["tags"]:
                            if not vocab.is_smart(t):
                                counts[t] += 1
        return title, franchise, [vocab.name(t).lower() for t, _ in counts.most_common(8)]

    # -- generating -----------------------------------------------------------------
    def start(self, tid: str, stills: list[dict[str, Any]], with_title: bool = True) -> dict:
        model = (self.core.cfg.posters.ai_model or "").strip()
        if not model:
            raise FansCutError("set an image model under Settings, Posters first")
        if not stills:
            raise FansCutError("pick at least one still")
        doc = self.core.get_timeline(tid)["doc"]
        sources = doc.get("sources") or {}
        refs = []
        for s in stills[:MAX_STILLS]:
            src, t = str(s.get("src") or ""), s.get("t")
            if src not in sources or not isinstance(t, (int, float)) or t < 0:
                raise FansCutError("each still is {src, t} of one of the cut's sources")
            refs.append((src, float(t)))
        with self._lock:
            if (self._tasks.get(tid) or {}).get("status") == "running":
                raise FansCutError("a poster for this cut is already being made")
            self._tasks[tid] = {"status": "running", "started": time.time(), "message": "Starting"}
        threading.Thread(
            target=self._run,
            args=(tid, refs, with_title, model),
            daemon=True,
            name=f"poster-{tid}",
        ).start()
        return self.state(tid)

    def _set(self, tid: str, **kw: Any) -> None:
        with self._lock:
            self._tasks[tid] = {**self._tasks.get(tid, {}), **kw}

    def faceless(self, tid: str, refs: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """The stills moved to moments with no face on screen (the film's no-face
        signal), inside the same segment; stills that can't move are dropped. Image
        models refuse to redraw real people, and a poster shouldn't need them."""
        doc = self.core.get_timeline(tid)["doc"]
        sources = doc.get("sources") or {}
        films = {f["tmdb_id"]: f["key"] for f in self.core.scenes.films() if f.get("tmdb_id")}
        segs = [g for part in doc.get("parts") or [] for g in part.get("segments") or []]
        out: list[tuple[str, float]] = []
        for src, t in refs:
            key = films.get((sources.get(src) or {}).get("tmdb"))
            sig = self.core.scenes.store.signals(key) if key else None
            ranges = ((sig or {}).get("signals") or {}).get("no-face") or []
            if any(a <= t < b for a, b in ranges):
                out.append((src, t))
                continue
            seg = next(
                (g for g in segs if g.get("src") == src and g["start"] <= t < g["end"]), None
            )
            if seg is None:
                continue
            inside = [
                (max(a, seg["start"]), min(b, seg["end"]))
                for a, b in ranges
                if a < seg["end"] and b > seg["start"]
            ]
            inside = [(a, b) for a, b in inside if b - a >= 1 and (src, (a + b) / 2) not in out]
            if inside:
                a, b = max(inside, key=lambda r: r[1] - r[0])
                out.append((src, round((a + b) / 2, 2)))
        return list(dict.fromkeys(out))

    def _run(self, tid: str, refs: list[tuple[str, float]], with_title: bool, model: str) -> None:
        try:
            self._set(tid, message="Grabbing stills")
            refs = self.faceless(tid, refs)
            images = [png(self.core.timeline_source_frame(tid, s, t).read_bytes()) for s, t in refs]
            title, franchise, tags = self.describe(tid)
            self._set(tid, message="Drawing (about a minute)")
            from prompture.drivers.img_gen_registry import get_img_gen_driver_for_model

            drv = get_img_gen_driver_for_model(model)
            quality = self.core.cfg.posters.ai_quality
            opts = {"size": SIZE, "quality": quality}
            res = None
            if images:
                try:
                    # OpenAI's driver takes several images; Prompture's base class
                    # still types `image` as one. Drop the ignore once it's widened.
                    res = drv.edit_image(
                        images,  # type: ignore[arg-type, unused-ignore]
                        prompt_for(title, franchise, tags, with_title),
                        opts,
                    )
                except Exception as exc:  # noqa: BLE001 - a refusal falls back to words only
                    if "moderation" not in str(exc) and "safety" not in str(exc):
                        raise
                    log.info("image model refused the stills for %s; drawing from words", tid)
                    refs = []
            if res is None:
                res = drv.generate_image(
                    prompt_for(title, franchise, tags, with_title, False), opts
                )
            data = image_bytes((res.get("images") or [None])[0])
            folder = self.folder(tid)
            folder.mkdir(parents=True, exist_ok=True)
            name = f"take-{int(time.time() * 1000)}.png"
            (folder / name).write_bytes(png(data))
            cost = float((res.get("meta") or {}).get("cost") or 0)
            meta = self._meta(tid)
            meta[name] = {
                "model": model,
                "quality": quality,
                "cost": round(cost, 4),
                "tags": tags,
                "stills": [list(r) for r in refs],
            }
            for old in self.takes(tid)[MAX_TAKES:]:
                if old != meta.get("chosen"):
                    (folder / old).unlink(missing_ok=True)
                    meta.pop(old, None)
            self._save_meta(tid, meta)
            if self.chosen(tid) is None:  # the first take is the poster until one is kept
                self.keep(tid, name)
            self._set(tid, status="done", message="Done", take=name, cost=round(cost, 4))
        except Exception as exc:  # noqa: BLE001 - reported to the page, not raised
            log.exception("AI poster for %s failed", tid)
            self._set(tid, status="failed", message=str(exc)[:300] or exc.__class__.__name__)
