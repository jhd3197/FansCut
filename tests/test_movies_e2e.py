"""Movie sources end to end: a 3-source timeline where the user's movie copy
is the theatrical edition with a scene removed."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fanscut.config import FFmpegCfg, FingerprintCfg
from fanscut.server import create_app
from fanscut.timeline import anchors
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
KEY = "folder:Tide Pool"


def chirp_video(path: Path, seconds: float, seed: int):
    audio = (f"aevalsrc=0.3*sin(6.283*({300 + seed * 97}+400*t/{seconds})*t)"
             f"+0.15*random({seed}):s=8000:duration={seconds}")
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "mandelbrot=size=320x240:rate=24", "-t", str(seconds),
                    "-f", "lavfi", "-i", audio,
                    "-c:v", "libx264", "-g", "24", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)


def _dur(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


@pytest.fixture(scope="module")
def movie_library(tmp_path_factory):
    """2 episodes + a movie in two editions; the theatrical one lacks seconds 5–8."""
    d = tmp_path_factory.mktemp("movielib")
    eps = d / "media" / "Tide Pool"
    eps.mkdir(parents=True)
    chirp_video(eps / "Tide Pool - S01E01.mkv", 12, seed=1)
    chirp_video(eps / "Tide Pool - S01E02.mkv", 12, seed=2)
    ext = d / "movies" / "Tide Pool Film (2021) Extended {tmdb-555}"
    ext.mkdir(parents=True)
    chirp_video(ext / "film.mkv", 30, seed=3)
    th = d / "movies" / "Tide Pool Film (2021) Theatrical {tmdb-555}"
    th.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(ext / "film.mkv"), "-filter_complex",
                    "[0:v]trim=0:12,setpts=PTS-STARTPTS[v0];[0:a]atrim=0:12,asetpts=PTS-STARTPTS[a0];"
                    "[0:v]trim=15:30,setpts=PTS-STARTPTS[v1];[0:a]atrim=15:30,asetpts=PTS-STARTPTS[a1];"
                    "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(th / "film.mkv")], check=True)
    return {"root": d, "extended": ext / "film.mkv"}


@pytest.fixture
def movie_client(cfg, tmp_path, movie_library):
    cfg.library.folder.path = str(movie_library["root"] / "media")
    cfg.library.movies.path = str(movie_library["root"] / "movies")
    cfg.catalogues.offline = True
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        c.core = c.app.state.core  # type: ignore[attr-defined]
        yield c


def _doc(author_anchors):
    def anch(t):
        if t[0] not in author_anchors:
            return None
        return {"start": author_anchors[t[0]], "end": author_anchors[t[1]]}

    def seg(src, start, end, **kw):
        s = {"src": src, "start": start, "end": end, **kw}
        a = anch((start, end))
        if a:
            s["anchors"] = a
        return s

    return {
        "schema": 2, "kind": "timeline", "title": "Tide Pool — Film Cut",
        "series": {"title": "Tide Pool"},
        "sources": {
            "S01E01": {"duration": 12.0},
            "S01E02": {"duration": 12.0},
            "M555": {"kind": "movie", "tmdb": 555, "edition": "theatrical", "duration": 30.0},
        },
        "parts": [{"title": "Whole Story", "segments": [
            seg("S01E01", 1.0, 4.0),
            seg("S01E02", 2.0, 5.0, xfade_ms=400),
            seg("M555", 12.5, 14.5),
            seg("M555", 20.0, 23.0),
        ]}],
    }


def test_movie_source_sync_and_render(movie_client, movie_library, cfg):
    # the author owns the extended edition; anchors come from it
    ff, fp = FFmpegCfg(), FingerprintCfg()
    author = {t: anchors.make_anchors(ff, str(movie_library["extended"]), t, fp)
              for t in (12.5, 14.5, 20.0, 23.0)}
    r = movie_client.post(f"/api/series/{KEY}/timelines", json=_doc(author))
    assert r.status_code == 201 and r.json()["missing"] == []  # M555 resolves via the folder
    tid = r.json()["id"]

    movie_client.post(f"/api/timelines/{tid}/sync")
    movie_client.core.worker.run_pending()
    rep = movie_client.get(f"/api/timelines/{tid}/sync").json()
    m555 = rep["sources"]["M555"]
    assert m555["kind"] == "movie" and m555["edition"] == "theatrical"
    assert m555["segments"]["1:3"] == "missing"  # the scene the theatrical cut removes
    assert m555["segments"]["1:4"] == "ok"
    # the surviving scene is re-timed across the 3 s gap
    b = next(x for x in m555["boundaries"] if x["segment"] == 4 and x["side"] == "start")
    assert abs(b["corrected"] - 17.0) <= 0.3

    # dry run: single part -> movie layout, crossfade forces the filter graph
    dry = movie_client.post(f"/api/timelines/{tid}/render", json={"dry_run": True}).json()
    assert dry["layout"] == "movie"
    part = dry["parts"][0]
    assert part["mode"] == "encode"  # crossfades need the filter graph, never stream copy
    assert part["output"].replace("\\", "/").endswith(
        "Tide Pool (Film Cut)/Tide Pool (Film Cut).mkv")
    assert any("not in your edition" in w for w in part["warnings"])
    assert len(part["segments"]) == 3  # the missing scene is skipped
    assert "acrossfade=d=0.400" in part["files"]["filter.txt"]

    job = movie_client.post(f"/api/timelines/{tid}/render", json={}).json()
    movie_client.core.worker.run_pending()
    done = movie_client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done["message"]
    out = Path(done["result"]["outputs"][0])
    assert out.name == "Tide Pool (Film Cut).mkv" and out.is_file()
    assert out.with_suffix(".nfo").read_text().startswith('<?xml version="1.0"')
    assert "<movie>" in out.with_suffix(".nfo").read_text()
    # 3s + 3s + 1.5s; the audio crossfade extends the first segment, not the runtime
    assert _dur(out) == pytest.approx(9.0, abs=0.4)


def test_movie_missing_from_library_reports(movie_client):
    d = _doc({})
    for p in d["parts"]:
        for s in p["segments"]:
            s.pop("anchors", None)
    d["sources"]["M999"] = {"kind": "movie", "tmdb": 999, "edition": "extended",
                            "duration": 6000.0}
    d["parts"][0]["segments"].append({"src": "M999", "start": 10, "end": 60})
    r = movie_client.post(f"/api/series/{KEY}/timelines", json=d)
    assert r.json()["missing"] == [{"src": "M999", "parts": [1], "note": ""}]


def test_movie_and_timeline_source_previews_and_frames(movie_client):
    """Build and the editor preview films the same way as episodes; stills are jpegs."""
    movies = movie_client.get("/api/library/movies").json()["movies"]
    key = next(m["key"] for m in movies if m.get("on_disk"))
    clip = movie_client.get(f"/api/movies/{key}/preview", params={"t": 10})
    assert clip.status_code == 200 and clip.headers["content-type"] == "video/mp4"
    still = movie_client.get(f"/api/movies/{key}/frame", params={"t": 10})
    assert still.status_code == 200 and still.content[:2] == b"\xff\xd8"  # a jpeg
    assert movie_client.get(f"/api/movies/{key}/frame", params={"t": 999}).status_code == 404

    tid = movie_client.post(f"/api/series/{KEY}/timelines", json=_doc({})).json()["id"]
    for src in ("S01E01", "M555"):
        r = movie_client.get(f"/api/timelines/{tid}/sources/{src}/frame", params={"t": 2})
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8", src
    r = movie_client.get(f"/api/timelines/{tid}/sources/M555/preview", params={"t": 13})
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert movie_client.get(f"/api/timelines/{tid}/sources/NOPE/frame",
                            params={"t": 1}).status_code == 404
    eps = movie_client.get(f"/api/series/{KEY}/episodes/S01E02/frame", params={"t": 1})
    assert eps.status_code == 200


def test_check_frame_only_serves_flagged_files(movie_client, movie_library, tmp_path):
    core = movie_client.core
    film = str(movie_library["extended"])
    assert movie_client.get("/api/library/check/frame", params={"file": film}).status_code == 404
    (core.cfg.data_dir).mkdir(parents=True, exist_ok=True)
    (core.cfg.data_dir / "library-check.json").write_text(json.dumps(
        {"findings": [{"check": "mismatch", "file": None, "files": [film]}]}))
    r = movie_client.get("/api/library/check/frame", params={"file": film})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    other = tmp_path / "secret.mkv"
    other.write_bytes(b"x")
    assert movie_client.get("/api/library/check/frame",
                            params={"file": str(other)}).status_code == 404
