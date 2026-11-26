"""Who is on screen, from faces (docs/markers.md section 4).

* **Detect**: YuNet finds faces in a frame.
* **Recognise**: SFace compares each face with the cast photos Plex (or TMDB)
  keeps for each actor. People already named in a tag covering the moment are
  tried with a loose threshold; anyone else needs a stricter one. Names at
  ``SURE`` or above were right in every case checked by eye; below it the UI
  shows "Name?" for a person to confirm.

Both models are the OpenCV Zoo ONNX files, kept in ``<data_dir>/models`` under
their official names and downloaded once when missing. OpenCV is optional:
without it, face features are simply unavailable. Boxes and features stay on
the server; nothing here is ever exported.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from ..errors import FansCutError

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
DETECT = ("face_detection_yunet_2023mar.onnx", f"{ZOO}/face_detection_yunet")
RECOGNISE = ("face_recognition_sface_2021dec.onnx", f"{ZOO}/face_recognition_sface")

LOOSE = 0.30  # cosine for someone already named in an overlapping tag
STRICT = 0.42  # cosine for anyone else in the cast
SURE = 0.40  # at or above: shown as a name, below: "Name?"
MIN_FACE = 0.035  # faces narrower than this share of the frame are ignored
REF_SIZE = 400  # cast photos are scaled to this, then padded, before detection
REF_PAD = 120


def available() -> bool:
    try:
        import cv2
    except ImportError:
        return False
    return hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")


def ensure_models(models_dir: Path, session: Any = None) -> tuple[Path, Path]:
    """Paths of both models, downloading any that is missing."""
    models_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for name, base in (DETECT, RECOGNISE):
        path = models_dir / name
        if not path.is_file():
            if session is None:
                raise FansCutError(f"face model {name} is missing from {models_dir}")
            r = session.get(f"{base}/{name}", timeout=120)
            r.raise_for_status()
            tmp = path.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)
        out.append(path)
    return out[0], out[1]


class FaceEngine:
    """YuNet + SFace behind a lock (OpenCV's detector keeps per-call state)."""

    def __init__(self, detect_model: Path, recognise_model: Path):
        import cv2

        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(str(detect_model), "", (320, 320), 0.75, 0.3, 50)
        self._rec = cv2.FaceRecognizerSF.create(str(recognise_model), "")
        self._lock = threading.Lock()

    def detect(self, img: Any, min_face: float = MIN_FACE) -> list[Any]:
        """Face rows (x, y, w, h, 5 landmarks, score) at least *min_face* wide."""
        h, w = img.shape[:2]
        with self._lock:
            self._det.setInputSize((w, h))
            _, found = self._det.detect(img)
        if found is None:
            return []
        return [f for f in found if f[2] >= w * min_face]

    def feature(self, img: Any, face: Any) -> Any:
        with self._lock:
            return self._rec.feature(self._rec.alignCrop(img, face))

    def match(self, a: Any, b: Any) -> float:
        with self._lock:
            return float(self._rec.match(a, b, self._cv2.FaceRecognizerSF_FR_COSINE))

    def reference(self, photo: bytes) -> Any | None:
        """The feature of the largest face in a cast photo, or None."""
        import numpy as np

        cv2 = self._cv2
        img = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        k = REF_SIZE / max(img.shape[:2])
        img = cv2.resize(img, None, fx=k, fy=k)
        # headshots fill the frame; a border lets the detector see the whole face
        img = cv2.copyMakeBorder(
            img, REF_PAD, REF_PAD, REF_PAD, REF_PAD, cv2.BORDER_CONSTANT, value=(40, 40, 40)
        )
        found = self.detect(img, min_face=0)
        if not found:
            return None
        return self.feature(img, max(found, key=lambda f: f[2] * f[3]))

    def read(self, path: Path) -> Any:
        img = self._cv2.imread(str(path))
        if img is None:
            raise FansCutError(f"could not read {path.name}")
        return img


def pick(scores: list[tuple[float, str]], candidates: set[str]) -> tuple[str, float] | None:
    """The best name: a candidate at LOOSE or anyone at STRICT, best score first."""
    for s, name in sorted(scores, reverse=True):
        if (name in candidates and s >= LOOSE) or s >= STRICT:
            return name, s
    return None


def name_faces(
    engine: FaceEngine,
    img: Any,
    refs: dict[str, dict[str, Any]],
    candidates: set[str],
) -> list[dict[str, Any]]:
    """Every face in *img*, boxes as fractions of the frame, named when it matches.

    *refs* maps an actor's name to {character, photo, feature}.
    """
    h, w = img.shape[:2]
    found = engine.detect(img)
    out = []
    for f in found:
        box = [
            round(max(0.0, float(f[0])) / w, 4),
            round(max(0.0, float(f[1])) / h, 4),
            round(float(f[2]) / w, 4),
            round(float(f[3]) / h, 4),
        ]
        out.append(
            {
                "box": box,
                "score": round(float(f[14]), 2),
                "name": None,
                "character": "",
                "photo": "",
                "match": None,
                "sure": False,
            }
        )
    if not refs or not found:
        return out
    # Each actor is on screen once: the best face-name pairs are settled first.
    scores = [
        [(engine.match(engine.feature(img, f), r["feature"]), n) for n, r in refs.items()]
        for f in found
    ]
    taken: set[str] = set()
    pairs = sorted(((s, n, i) for i, row in enumerate(scores) for s, n in row), reverse=True)
    for s, name, i in pairs:
        face = out[i]
        if face["name"] or name in taken:
            continue
        if pick([(s, name)], candidates) is None:
            continue
        taken.add(name)
        face.update(
            name=name,
            character=refs[name].get("character") or "",
            photo=refs[name].get("photo") or "",
            match=round(s, 2),
            sure=s >= SURE,
        )
    return out


def photo_cache(cache_dir: Path, url: str, session: Any) -> bytes | None:
    """A cast photo, downloaded once into *cache_dir*."""
    if not url.startswith(("http://", "https://")):
        return None
    path = cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.jpg"
    if path.is_file():
        return path.read_bytes()
    try:
        r = session.get(url, timeout=30)
        r.raise_for_status()
    except Exception:  # noqa: BLE001 - a missing photo only means no reference face
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content
