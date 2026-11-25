"""Render cache: hashing, store, reuse in the render job, keep/evict."""

import datetime as _dt
from pathlib import Path

from fanscut.config import FFmpegCfg
from fanscut.planner import CutOptions, PlanEpisode, plan_cut
from fanscut.render.cache import RenderStore, render_hash
from fanscut.render.plan import build_plan
from tests.conftest import SERIES
from tests.test_api import E, core_of, fake_probe, matched  # noqa: F401  (fixtures)


def _cut(tmp_path: Path, start: float = 0.0) -> tuple:
    f = tmp_path / "ep1.mkv"
    f.write_bytes(b"x" * 100)
    ep = PlanEpisode(id="S01E01", number=1, season=1, file=str(f), duration=100.0, label="canon")
    return plan_cut([ep], CutOptions(preset="everything", min_segment=0)), f


def test_render_hash_is_stable_and_sensitive(tmp_path):
    ff = FFmpegCfg()
    cut, f = _cut(tmp_path)
    h1 = render_hash("folder:X", cut, "copy", ff, [])
    assert h1 == render_hash("folder:X", cut, "copy", ff, [])
    assert h1 != render_hash("folder:X", cut, "encode", ff, [])
    assert h1 != render_hash("folder:Y", cut, "copy", ff, [])
    f.write_bytes(b"y" * 200)  # same path, new size -> source changed
    assert h1 != render_hash("folder:X", cut, "copy", ff, [])


def test_render_store_roundtrip(db, tmp_path):
    store = RenderStore(db)
    out = tmp_path / "cut.mkv"
    out.write_bytes(b"data")
    row = store.add("h1", SERIES, "Harbor Lights", str(out), "copy", keep=True, job_id="j1")
    assert row["keep"] is True and row["size_bytes"] == 4 and row["file_exists"] is True
    assert store.hit("h1")["path"] == str(out)
    assert store.set_keep("h1", False)["keep"] is False
    out.unlink()
    assert store.hit("h1") is None  # file vanished: stale row dropped
    assert store.get("h1") is None


def test_render_store_due_only_old_non_kept(db, tmp_path):
    store = RenderStore(db)
    for h, keep in (("old-temp", False), ("old-keep", True), ("new-temp", False)):
        p = tmp_path / f"{h}.mkv"
        p.write_bytes(b"x")
        store.add(h, SERIES, "t", str(p), "copy", keep)
    old = (_dt.datetime.now() - _dt.timedelta(days=30)).isoformat(timespec="seconds")
    db.execute("UPDATE renders SET created_at = ? WHERE hash != 'new-temp'", (old,))
    cutoff = (_dt.datetime.now() - _dt.timedelta(days=7)).isoformat(timespec="seconds")
    assert [r["hash"] for r in store.due(cutoff)] == ["old-temp"]


def _fake_execute(monkeypatch):
    calls = []

    def fake(rp, workdir, on_progress, cancel=None):
        calls.append(rp.cache_key)
        Path(rp.output).parent.mkdir(parents=True, exist_ok=True)
        Path(rp.output).write_bytes(b"fake video")
        on_progress(1.0)
        return rp.output

    monkeypatch.setattr("fanscut.core.run.execute", fake)
    return calls


def test_render_job_caches_and_reuses(matched, fake_probe, monkeypatch):  # noqa: F811
    core = core_of(matched)
    calls = _fake_execute(monkeypatch)
    job = matched.post(f"{E}/render", json={}).json()
    core.worker.run_pending()
    done = matched.get(f"/api/jobs/{job['id']}").json()["result"]
    assert done["cached"] is False and len(calls) == 1
    assert Path(done["output"]).is_file()

    again = matched.post(f"{E}/render", json={}).json()
    core.worker.run_pending()
    hit = matched.get(f"/api/jobs/{again['id']}").json()["result"]
    assert hit["cached"] is True and hit["output"] == done["output"] and len(calls) == 1

    refreshed = matched.post(f"{E}/render", json={"refresh": True}).json()
    core.worker.run_pending()
    assert matched.get(f"/api/jobs/{refreshed['id']}").json()["result"]["cached"] is False
    assert len(calls) == 2


def test_render_keep_flag_and_api(matched, fake_probe, monkeypatch):  # noqa: F811
    core = core_of(matched)
    _fake_execute(monkeypatch)
    job = matched.post(f"{E}/render", json={"keep": False}).json()
    core.worker.run_pending()
    result = matched.get(f"/api/jobs/{job['id']}").json()["result"]
    assert result["keep"] is False

    renders = matched.get("/api/renders").json()["renders"]
    assert len(renders) == 1 and renders[0]["keep"] is False
    assert matched.get(f"/api/renders?series={SERIES}").json()["renders"] == renders

    h = renders[0]["hash"]
    assert matched.put(f"/api/renders/{h}", json={"keep": True}).json()["keep"] is True
    path = renders[0]["path"]
    assert matched.delete(f"/api/renders/{h}").json() == {"ok": True}
    assert not Path(path).exists() and matched.get("/api/renders").json()["renders"] == []
    assert matched.delete(f"/api/renders/{h}").status_code == 404


def test_sweep_deletes_old_ephemeral_keeps_the_rest(matched, fake_probe, monkeypatch,  # noqa: F811
                                                    app_cfg):
    core = core_of(matched)
    _fake_execute(monkeypatch)
    app_cfg.render.ephemeral_days = 7
    matched.post(f"{E}/render", json={"keep": False})
    core.worker.run_pending()
    h = matched.get("/api/renders").json()["renders"][0]["hash"]
    old = (_dt.datetime.now() - _dt.timedelta(days=30)).isoformat(timespec="seconds")
    core.db.execute("UPDATE renders SET created_at = ? WHERE hash = ?", (old, h))

    assert matched.post("/api/renders/sweep").json()["removed"]  # old + non-kept: gone
    assert matched.get("/api/renders").json()["renders"] == []

    app_cfg.render.ephemeral_days = 0  # 0 = never evict
    matched.post(f"{E}/render", json={"keep": False, "refresh": True})
    core.worker.run_pending()
    core.db.execute("UPDATE renders SET created_at = ?", (old,))
    assert matched.post("/api/renders/sweep").json()["removed"] == []


def test_build_plan_still_works_with_provisional_output(tmp_path):
    # Core rewrites output/command after hashing; the plan builder stays pure.
    cut, _f = _cut(tmp_path)
    probe = lambda _p: {"duration": 100.0, "video": {"codec": "h264", "width": 100,  # noqa: E731
                        "height": 100, "pix_fmt": "yuv420p", "fps": 24.0},
                        "audio": {"codec": "aac", "sample_rate": 48000, "channels": 2}}
    rp = build_plan(cut, FFmpegCfg(), tmp_path / "out.mkv", tmp_path, probe)
    assert rp.command[-1].endswith(".part.mkv") and rp.cache_key == ""
