"""Folder mode with real tiny videos: list, label, detect nothing, render, CLI."""

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from fanscut import cli
from fanscut.server import create_app
from tests.conftest import make_video, needs_ffmpeg

pytestmark = needs_ffmpeg
KEY = "folder:Tide Pool"


@pytest.fixture
def media_client(cfg, tmp_path):
    root = tmp_path / "media" / "Tide Pool"
    root.mkdir(parents=True)
    for n in (1, 2, 3):
        make_video(root / f"Tide Pool - S01E{n:02d}.mkv", seconds=6)
    cfg.library.folder.path = str(tmp_path / "media")
    cfg.catalogues.offline = True
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        c.core = c.app.state.core  # type: ignore[attr-defined]
        yield c


def test_folder_series_and_probed_durations(media_client):
    series = media_client.get("/api/library/series").json()["series"]
    assert [(s["key"], s["episode_count"]) for s in series] == [(KEY, 3)]
    eps = media_client.get(f"/api/series/{KEY}/episodes").json()["episodes"]
    assert [e["id"] for e in eps] == ["S01E01", "S01E02", "S01E03"]
    assert all(e["duration"] == pytest.approx(6, abs=0.3) for e in eps)


def test_render_job_end_to_end(media_client, cfg):
    media_client.put(f"/api/series/{KEY}/episodes/S01E02/override", json={"include": False})
    media_client.put(f"/api/series/{KEY}/episodes/S01E01/trims/outro", json={"start": 4, "end": 6})
    media_client.put(f"/api/series/{KEY}/settings", json={"pad": 0, "min_segment": 1})
    body = {"mode": "encode"}
    dry = media_client.post(f"/api/series/{KEY}/render", json={**body, "dry_run": True}).json()
    assert dry["cut"]["totals"]["kept"] == 2
    job = media_client.post(f"/api/series/{KEY}/render", json=body).json()
    media_client.core.worker.run_pending()
    done = media_client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done["message"]
    out = done["result"]["output"]
    assert out.startswith(str(cfg.output_dir)) and out.endswith("Tide Pool - fan cut.mkv")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "json", out], capture_output=True, text=True, check=True)
    assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(10, abs=0.4)
    # a second render never overwrites the first
    job2 = media_client.post(f"/api/series/{KEY}/render", json=body).json()
    media_client.core.worker.run_pending()
    assert media_client.get(f"/api/jobs/{job2['id']}").json()["result"]["output"].endswith("(2).mkv")


def test_cli_doctor_scan_and_help(cfg, tmp_path, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(f"paths:\n  data_dir: {cfg.paths.data_dir}\n  output_dir: {cfg.paths.output_dir}\n"
                    f"library:\n  folder:\n    path: {tmp_path / 'nothing'}\n")
    assert cli.main(["--config", str(path), "doctor"]) == 1  # the media folder does not exist
    assert "ok   ffmpeg" in capsys.readouterr().out
    assert cli.main(["--config", str(path), "scan"]) == 0
    with pytest.raises(SystemExit):
        cli.main(["--version"])


def test_module_entrypoint_prints_help():
    out = subprocess.run([sys.executable, "-m", "fanscut", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "serve" in out.stdout
