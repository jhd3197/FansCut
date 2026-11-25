"""Timeline render end to end: Plex-style layout, chapters, per-part cache."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fanscut.server import create_app
from tests.conftest import make_video, needs_ffmpeg

pytestmark = needs_ffmpeg
KEY = "folder:Tide Pool"
TL = {
    "schema": 2, "kind": "timeline", "title": "Tide Pool — Chronological",
    "series": {"title": "Tide Pool"},
    "sources": {f"S01E0{n}": {"duration": 6.0} for n in (1, 2, 3)},
    "parts": [
        {"title": "The Beginning", "segments": [
            {"src": "S01E03", "start": 1.0, "end": 4.0},
            {"src": "S01E01", "start": 0.5, "end": 3.5}]},
        {"title": "The Middle", "segments": [
            {"src": "S01E02", "start": 2.0, "end": 5.5}]},
    ],
    "credits": {"author": "test"},
}
SHOW_DIR = Path("Tide Pool (Chronological)") / "Season 01"


@pytest.fixture
def tl_client(cfg, tmp_path):
    root = tmp_path / "media" / "Tide Pool"
    root.mkdir(parents=True)
    for n in (1, 2, 3):
        make_video(root / f"Tide Pool - S01E{n:02d}.mkv", seconds=6)
    cfg.library.folder.path = str(tmp_path / "media")
    cfg.catalogues.offline = True
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        c.core = c.app.state.core  # type: ignore[attr-defined]
        yield c


def _render(tl_client):
    tid = tl_client.post(f"/api/series/{KEY}/timelines", json=TL).json()["id"]
    tl_client.post(f"/api/timelines/{tid}/sync")
    tl_client.core.worker.run_pending()  # sync
    job = tl_client.post(f"/api/timelines/{tid}/render", json={"mode": "encode"}).json()
    tl_client.core.worker.run_pending()  # render
    return tid, tl_client.get(f"/api/jobs/{job['id']}").json()


def _chapters(path: str) -> list[str]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    return [c["tags"]["title"] for c in json.loads(r.stdout)["chapters"]]


def test_timeline_renders_plex_layout(tl_client, cfg):
    tid, done = _render(tl_client)
    assert done["status"] == "done", done["message"]
    res = done["result"]
    assert res["parts"] == 2 and res["cached"] == 0
    out_dir = Path(cfg.paths.output_dir) / SHOW_DIR
    p1 = out_dir / "Tide Pool (Chronological) - S01E01 - The Beginning.mkv"
    p2 = out_dir / "Tide Pool (Chronological) - S01E02 - The Middle.mkv"
    assert [Path(o).name for o in res["outputs"]] == [p1.name, p2.name]
    assert p1.is_file() and p2.is_file()
    assert _chapters(str(p1)) == ["S01E03", "S01E01"]  # one chapter per source episode
    assert (out_dir / "Tide Pool (Chronological) - S01E01 - The Beginning.nfo").is_file()
    assert (out_dir.parent / "tvshow.nfo").is_file()
    assert "Chronological" in (out_dir.parent / "tvshow.nfo").read_text()

    # second render is fully cached; refresh overwrites in place
    job2 = tl_client.post(f"/api/timelines/{tid}/render", json={"mode": "encode"}).json()
    tl_client.core.worker.run_pending()
    res2 = tl_client.get(f"/api/jobs/{job2['id']}").json()["result"]
    assert res2["cached"] == 2 and res2["outputs"] == res["outputs"]


def test_timeline_render_requires_sync(tl_client):
    tid = tl_client.post(f"/api/series/{KEY}/timelines", json=TL).json()["id"]
    r = tl_client.post(f"/api/timelines/{tid}/render", json={})
    assert r.status_code == 400 and "sync" in r.json()["detail"]


def test_timeline_dry_run_matches_render(tl_client):
    tid, done = _render(tl_client)
    dry = tl_client.post(f"/api/timelines/{tid}/render", json={"dry_run": True}).json()
    assert dry["show"] == "Tide Pool (Chronological)" and dry["synced"] is True
    assert [p["title"] for p in dry["parts"]] == ["The Beginning", "The Middle"]
    assert dry["parts"][0]["runtime"] == pytest.approx(6.0, abs=0.2)
    assert dry["parts"][0]["command"][0] == "ffmpeg"
    assert [Path(p["output"]).name for p in dry["parts"]] == [
        Path(o).name for o in done["result"]["outputs"]]


def test_missing_source_fails_loudly(tl_client):
    doc = dict(TL)
    doc["parts"] = [{"title": "p", "segments": [{"src": "S01E09", "start": 0, "end": 3}]}]
    doc["sources"]["S01E09"] = {"duration": 100.0}
    r = tl_client.post(f"/api/series/{KEY}/timelines", json=doc)
    assert r.json()["missing"] == [{"src": "S01E09", "parts": [1], "note": ""}]
    tid = r.json()["id"]
    tl_client.post(f"/api/timelines/{tid}/sync")
    tl_client.core.worker.run_pending()
    r = tl_client.post(f"/api/timelines/{tid}/render", json={"dry_run": True})
    assert r.status_code == 400 and "no file" in r.json()["detail"]
