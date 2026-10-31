"""The REST API end to end against a mocked Sonarr (series 7, Harbor Lights, six episodes)."""

import pytest
import responses

from fanscut.catalogues.http import RateLimiter
from fanscut.fillers.anifiller import CACHE_NAME
from tests.conftest import SERIES

E = f"/api/series/{SERIES}"
VID = {"codec": "h264", "width": 1280, "height": 720, "pix_fmt": "yuv420p", "fps": 24.0}
AUD = {"codec": "aac", "sample_rate": 48000, "channels": 2}


def core_of(client):
    return client.app_ref.state.core


@pytest.fixture
def fake_probe(client):
    core_of(client).prober = lambda _f: {"duration": 1440.0, "video": VID, "audio": AUD}


@pytest.fixture
def matched(client):
    r = client.put(f"{E}/match", json={"mal_id": 900001, "anilist_id": 800001, "title": "Harbor Lights"})
    assert r.status_code == 200
    return client


def episodes(client):
    return {e["id"]: e for e in client.get(f"{E}/episodes").json()["episodes"]}


# -- basics --------------------------------------------------------------------
def test_healthz_and_docs(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/series/{key}/render" in schema["paths"]


def test_doctor(client):
    body = client.get("/api/doctor").json()
    assert {c["name"] for c in body["checks"]} >= {"ffmpeg", "data dir", "output dir"}


def test_unknown_series_is_404(client):
    assert client.get("/api/series/sonarr:999/episodes").status_code == 404
    assert client.get("/api/series/bogus/episodes").status_code == 404


# -- settings ------------------------------------------------------------------
def test_settings_masked_saved_and_applied(client, tmp_path):
    shown = client.get("/api/settings").json()
    assert shown["library"]["sonarr"]["api_key"] == "***"
    shown["cut"]["pad"] = 2.5
    r = client.put("/api/settings", json=shown)
    assert r.status_code == 200 and r.json()["cut"]["pad"] == 2.5
    saved = (tmp_path / "config.yaml").read_text()
    assert "sonarr-key" in saved and "pad: 2.5" in saved
    assert core_of(client).cfg.cut.pad == 2.5


def test_settings_validation_error(client):
    assert client.put("/api/settings", json={"server": {"port": "many"}}).status_code == 400


def test_connection_tests(client, tmp_path):
    assert client.post("/api/settings/test/sonarr").json() == {"ok": True, "message": "Sonarr 4.0.0"}
    bad = client.post("/api/settings/test/sonarr", json={"url": "http://down.test", "api_key": "x"})
    assert bad.json()["ok"] is False
    assert client.post("/api/settings/test/folder", json={"path": str(tmp_path)}).json()["ok"]
    assert not client.post("/api/settings/test/folder", json={"path": str(tmp_path / "no")}).json()["ok"]
    assert client.post("/api/settings/test/nope").status_code == 400


# -- library -------------------------------------------------------------------
def test_library_list_search_and_match_status(client):
    body = client.get("/api/library/series").json()
    assert body["sources"] == ["sonarr"] and body["errors"] == {}
    s = body["series"][0]
    assert (s["key"], s["title"], s["matched"], s["is_anime"]) == (SERIES, "Harbor Lights", False, True)
    assert client.get("/api/library/series?q=zzz").json()["series"] == []
    assert client.get("/api/library/series?source=plex").json()["series"] == []
    client.put(f"{E}/match", json={"mal_id": 1})
    assert client.get("/api/library/series").json()["series"][0]["matched"] is True


def test_library_reports_failed_source_without_failing(client, app_cfg):
    app_cfg.library.sonarr.url = "http://down.test"
    core_of(client).apply_config(app_cfg)
    body = client.get("/api/library/series")
    assert body.status_code == 200 and "sonarr" in body.json()["errors"]


def test_folder_key_cannot_escape_the_media_folder(client, app_cfg, tmp_path):
    media = tmp_path / "media"
    (media / "Show").mkdir(parents=True)
    (tmp_path / "secret").mkdir()
    app_cfg.library.folder.path = str(media)
    core_of(client).apply_config(app_cfg)
    assert client.get("/api/series/folder:..%2Fsecret/episodes").status_code == 404


# -- episodes, labels, matching ----------------------------------------------
def test_unmatched_series_is_all_unknown_and_kept(client):
    eps = episodes(client)
    assert len(eps) == 6 and {e["label"] for e in eps.values()} == {"unknown"}
    assert eps["S01E02"]["include"] is True and eps["S01E01"]["duration"] == 1440
    assert eps["S01E01"]["has_file"]


def test_match_brings_filler_labels_and_preset_decides_inclusion(matched):
    eps = episodes(matched)
    assert [eps[f"S01E0{n}"]["label"] for n in range(1, 7)] == [
        "canon", "filler", "mixed", "canon", "filler", "canon"]
    assert eps["S01E02"]["label_source"] == "anifiller"
    assert [eps[f"S01E0{n}"]["include"] for n in range(1, 7)] == [True, False, False, True, False, True]
    view = matched.get(f"{E}/episodes").json()
    assert view["filler_sources"]["-1"]["anifiller"] is True


def test_season_specific_match_counts_within_the_season(client):
    # A match for season 1 only: numbering is the in-season number.
    client.put(f"{E}/match", json={"mal_id": 900001, "season": 1})
    assert episodes(client)["S01E03"]["label"] == "mixed"
    assert client.delete(f"{E}/match?season=1").json()["matches"] == []
    assert episodes(client)["S01E03"]["label"] == "unknown"


def test_override_label_and_include(matched):
    r = matched.put(f"{E}/episodes/S01E02/override", json={"label": "canon"})
    assert r.json() == {"label": "canon", "include": None}
    eps = episodes(matched)
    assert eps["S01E02"]["label"] == "canon" and eps["S01E02"]["label_source"] == "override"
    assert eps["S01E02"]["include"] is True
    matched.put(f"{E}/episodes/S01E01/override", json={"include": False})
    assert episodes(matched)["S01E01"]["include"] is False
    # Sending only include leaves an earlier label override alone; null clears it.
    matched.put(f"{E}/episodes/S01E02/override", json={"include": True})
    assert matched.get(f"{E}/episodes").json()["episodes"][1]["override"]["label"] == "canon"
    matched.put(f"{E}/episodes/S01E02/override", json={"label": None})
    assert episodes(matched)["S01E02"]["label"] == "filler"
    matched.delete(f"{E}/episodes/S01E02/override")
    assert episodes(matched)["S01E02"]["override"] is None


def test_bad_label_override(client):
    assert client.put(f"{E}/episodes/S01E01/override", json={"label": "epic"}).status_code == 400


def test_jikan_fallback_when_no_snapshot(client, sonarr_mock):
    core = core_of(client)
    core.http_jikan.offline = False
    core.http_jikan.limiter = RateLimiter(0, sleep=lambda _s: None)
    client.put(f"{E}/match", json={"mal_id": 424242})  # not in the bundled snapshot
    flags = [{"mal_id": n, "filler": n in (2, 4), "recap": n == 5} for n in range(1, 7)]
    sonarr_mock.add(responses.GET, "https://api.jikan.moe/v4/anime/424242/episodes",
                    json={"data": flags, "pagination": {"has_next_page": False}})
    eps = episodes(client)
    assert [eps[f"S01E0{n}"]["label"] for n in range(1, 7)] == [
        "canon", "filler", "canon", "filler", "filler", "canon"]
    assert eps["S01E02"]["label_source"] == "jikan"


def test_candidates_and_auto_match(client, sonarr_mock):
    core = core_of(client)
    for h in (core.http_anilist, core.http_jikan):
        h.offline = False
        h.limiter = RateLimiter(0, sleep=lambda _s: None)
    sonarr_mock.add(responses.POST, "https://graphql.anilist.co", json={"data": {"Page": {"media": [
        {"id": 800001, "idMal": 900001, "format": "TV", "episodes": 6, "seasonYear": 2020,
         "title": {"romaji": "Minato no Akari", "english": "Harbor Lights", "native": None},
         "synonyms": []}]}}})
    sonarr_mock.add(responses.GET, "https://api.jikan.moe/v4/anime", json={"data": []})
    cands = client.get(f"{E}/match/candidates?q=harbor").json()["candidates"]
    assert cands[0]["mal_id"] == 900001
    auto = client.post(f"{E}/match/auto").json()
    assert auto["matched"] and auto["matches"][0]["source"] == "auto"
    assert auto["matches"][0]["anilist_id"] == 800001


def test_offline_candidates_are_empty_not_an_error(client):
    assert client.get(f"{E}/match/candidates").json() == {"candidates": []}
    assert client.post(f"{E}/match/auto").json()["matched"] is False


# -- trims ---------------------------------------------------------------------
def test_trim_edit_clear_reset(client):
    t = f"{E}/episodes/S01E01/trims"
    assert client.put(f"{t}/intro", json={"start": 60, "end": 150}).json()["intro"]["source"] == "user"
    assert episodes(client)["S01E01"]["trims"]["intro"]["end"] == 150
    cleared = client.put(f"{t}/intro", json={}).json()["intro"]
    assert cleared["start"] is None and cleared["source"] == "user"
    assert client.delete(f"{t}/intro").json() == {}


@pytest.mark.parametrize(("kind", "body"), [
    ("intro", {"start": 10, "end": 5}), ("intro", {"start": -1, "end": 5}),
    ("intro", {"start": 5}), ("credits", {"start": 1, "end": 2})])
def test_bad_trims_rejected(client, kind, body):
    assert client.put(f"{E}/episodes/S01E01/trims/{kind}", json=body).status_code == 400


def test_detection_job_stores_auto_trims_but_respects_user_edits(client, monkeypatch):
    core = core_of(client)
    found = {"S01E01": {"intro": (60.0, 150.0), "outro": (1350.0, 1440.0)},
             "S01E02": {"intro": (55.0, 145.0)}}
    monkeypatch.setattr("fanscut.core.detect.analyze_files", lambda *a, **k: found)
    client.put(f"{E}/episodes/S01E01/trims/intro", json={"start": 62, "end": 151})  # user edit
    client.put(f"{E}/episodes/S01E02/trims/intro", json={})  # user cleared it on purpose
    job = client.post(f"{E}/detect-trims", json={"season": 1})
    assert job.status_code == 202
    core.worker.run_pending()
    done = client.get(f"/api/jobs/{job.json()['id']}").json()
    assert done["status"] == "done" and done["result"]["episodes"] == 6
    eps = episodes(client)
    assert eps["S01E01"]["trims"]["intro"] == {"start": 62, "end": 151, "source": "user"}
    assert eps["S01E01"]["trims"]["outro"] == {"start": 1350.0, "end": 1440.0, "source": "auto"}
    assert eps["S01E02"]["trims"]["intro"]["start"] is None
    # re-running replaces auto rows
    found["S01E01"] = {}
    client.post(f"{E}/detect-trims", json={"season": 1})
    core.worker.run_pending()
    assert "outro" not in episodes(client)["S01E01"]["trims"]


def test_detection_needs_two_episodes_with_files(client, app_cfg):
    job = client.post(f"{E}/detect-trims", json={"season": 5}).json()
    core_of(client).worker.run_pending()
    got = client.get(f"/api/jobs/{job['id']}").json()
    assert got["status"] == "failed" and "two episodes" in got["message"]


# -- series settings and the cut -----------------------------------------------
def test_series_settings_roundtrip_and_validation(client):
    assert client.get(f"{E}/settings").json()["preset"] == "canon_only"
    r = client.put(f"{E}/settings", json={"preset": "everything", "pad": 1.5})
    assert r.json()["preset"] == "everything" and r.json()["pad"] == 1.5
    assert client.get(f"{E}/settings").json()["pad"] == 1.5
    assert client.put(f"{E}/settings", json={"preset": "bogus"}).status_code == 400
    assert client.put(f"{E}/settings", json={"pad": -1}).status_code == 422


def test_cut_endpoint(matched):
    matched.put(f"{E}/episodes/S01E01/trims/outro", json={"start": 1350, "end": 1440})
    cut = matched.post(f"{E}/cut", json={"options": {"pad": 0, "keep_intro_first_episode": False}}).json()
    assert cut["kept_episodes"] == ["S01E01", "S01E04", "S01E06"]
    assert {s["episode_id"] for s in cut["skipped_episodes"]} == {"S01E02", "S01E03", "S01E05"}
    assert cut["segments"][0] == {"episode_id": "S01E01", "file": "/tv/Harbor Lights/S01E01.mkv",
                                  "start": 0.0, "end": 1350.0, "reason": "trimmed outro", "duration": 1350.0}
    t = cut["totals"]
    assert (t["kept"], t["original_runtime"], t["final_runtime"]) == (3, 8640, 1350 + 1440 * 2)
    assert t["time_saved"] == 8640 - t["final_runtime"]


def test_cut_options_per_request_and_seasons(matched):
    all_ = matched.post(f"{E}/cut", json={"options": {"preset": "everything"}}).json()
    assert all_["totals"]["kept"] == 6
    assert matched.post(f"{E}/cut", json={"seasons": [9]}).status_code == 404
    assert matched.post(f"{E}/cut", json={"options": {"bogus": 1}}).status_code == 400
    assert matched.post(f"{E}/cut", json={"options": {"preset": "bogus"}}).status_code == 400


# -- render --------------------------------------------------------------------
def test_dry_run_returns_the_ffmpeg_plan(matched, fake_probe):
    out = matched.post(f"{E}/render", json={"dry_run": True}).json()
    r = out["render"]
    assert r["mode"] == "copy" and r["command"][0] == "ffmpeg"
    assert r["files"]["concat.txt"].count("file ") == 3
    assert r["output"].endswith("Harbor Lights - fan cut.mkv")
    assert out["cut"]["totals"]["kept"] == 3
    assert [c["episode_id"] for c in r["chapters"]] == ["S01E01", "S01E04", "S01E06"]
    forced = matched.post(f"{E}/render", json={"dry_run": True, "mode": "encode"}).json()
    assert forced["render"]["mode"] == "encode"


def test_dry_run_does_not_touch_disk(matched, fake_probe, app_cfg):
    matched.post(f"{E}/render", json={"dry_run": True})
    assert not app_cfg.output_dir.exists()


def test_dry_run_with_empty_cut_is_400(client, fake_probe):
    r = client.post(f"{E}/render", json={"dry_run": True, "options": {"preset": "custom"}})
    assert r.status_code == 200  # custom keeps everything
    client.put(f"{E}/settings", json={"preset": "canon_only", "unknown_policy": "skip"})
    assert client.post(f"{E}/render", json={"dry_run": True}).status_code == 400


def test_render_job_failure_is_reported_on_the_job(matched, fake_probe):
    # The files in the mocked Sonarr do not exist, so ffmpeg fails; the job says why.
    job = matched.post(f"{E}/render", json={}).json()
    assert job["status"] == "queued" and job["kind"] == "render"
    core_of(matched).worker.run_pending()
    got = matched.get(f"/api/jobs/{job['id']}").json()
    assert got["status"] == "failed" and "ffmpeg" in got["message"]


# -- jobs ----------------------------------------------------------------------
def test_job_endpoints(matched, fake_probe):
    job = matched.post(f"{E}/render", json={}).json()
    assert [j["id"] for j in matched.get("/api/jobs").json()["jobs"]] == [job["id"]]
    assert matched.delete(f"/api/jobs/{job['id']}").status_code == 400  # still queued
    assert matched.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert matched.delete(f"/api/jobs/{job['id']}").json() == {"ok": True}
    assert matched.get(f"/api/jobs/{job['id']}").status_code == 404


# -- fillers -------------------------------------------------------------------
def test_fillers_status_and_refresh(client, sonarr_mock):
    assert client.get("/api/fillers").json()["shows"] == 1
    assert client.post("/api/fillers/refresh").json()["ok"] is False  # offline mode
    core = core_of(client)
    core.cfg.catalogues.offline = False
    core.cfg.fillers.url = "https://raw.example.test/f.json"
    sonarr_mock.add(responses.GET, "https://raw.example.test/f.json", json={"shows": [
        {"title": "Tide Pool", "mal_id": 5, "anilist_id": 6, "episodes": [{"episode": 1, "type": "filler"}]}]})
    res = client.post("/api/fillers/refresh").json()
    assert res["ok"] and (core.cfg.fillers_dir / CACHE_NAME).is_file()
    assert client.get("/api/fillers").json()["shows"] == 2
    sonarr_mock.replace(responses.GET, "https://raw.example.test/f.json", status=404)
    assert client.post("/api/fillers/refresh").json()["ok"] is False


# -- recipes -------------------------------------------------------------------
def test_recipe_export_import_round_trip(matched):
    matched.put(f"{E}/episodes/S01E02/override", json={"label": "canon", "include": True})
    matched.put(f"{E}/episodes/S01E01/trims/intro", json={"start": 60, "end": 150})
    matched.put(f"{E}/episodes/S01E01/trims/outro", json={})
    matched.put(f"{E}/settings", json={"preset": "canon_mixed", "pad": 2.0})
    doc = matched.get(f"{E}/recipe").json()
    assert doc["schema"] == 1 and doc["series"] == {
        "title": "Harbor Lights", "mal_id": 900001, "anilist_id": 800001}
    assert doc["preset"] == "canon_mixed" and doc["options"]["pad"] == 2.0
    assert doc["episodes"]["S01E01"]["trims"] == {"intro": [60.0, 150.0], "outro": None}
    assert doc["episodes"]["S01E02"] == {"label": "canon", "include": True}
    assert "file" not in str(doc) and "/tv/" not in str(doc)

    # wipe local choices, then import: everything comes back
    for eid in ("S01E01", "S01E02"):
        matched.delete(f"{E}/episodes/{eid}/override")
    for kind in ("intro", "outro"):
        matched.delete(f"{E}/episodes/S01E01/trims/{kind}")
    matched.put(f"{E}/settings", json={"preset": "everything"})
    res = matched.post(f"{E}/recipe", json=doc).json()
    assert res == {"applied": 2, "skipped": []}
    eps = episodes(matched)
    assert eps["S01E02"]["label"] == "canon" and eps["S01E01"]["trims"]["intro"]["start"] == 60
    assert eps["S01E01"]["trims"]["outro"]["start"] is None
    assert matched.get(f"{E}/settings").json()["preset"] == "canon_mixed"


def test_recipe_import_skips_unknown_episodes_and_adopts_ids(client):
    doc = {"schema": 1, "series": {"title": "Harbor Lights", "mal_id": 900001},
           "episodes": {"S01E01": {"include": False}, "S09E99": {"include": False}}}
    res = client.post(f"{E}/recipe", json=doc).json()
    assert res == {"applied": 1, "skipped": ["S09E99"]}
    assert client.get(f"{E}/match").json()["matches"][0]["mal_id"] == 900001
    assert episodes(client)["S01E01"]["include"] is False


def test_recipe_import_rejects_garbage_atomically(client):
    assert client.post(f"{E}/recipe", json={"schema": 7}).status_code == 400
    bad = {"schema": 1, "episodes": {"S01E01": {"include": False}, "S01E02": {"label": "epic"}}}
    assert client.post(f"{E}/recipe", json=bad).status_code == 400
    assert episodes(client)["S01E01"]["override"] is None  # validation ran before any write


def test_serves_built_ui_without_shadowing_the_api(app_cfg, tmp_path, monkeypatch, sonarr_mock):
    from fastapi.testclient import TestClient

    from fanscut.server import create_app

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>FansCut UI</title>")
    monkeypatch.setenv("FANSCUT_UI_DIR", str(dist))
    with TestClient(create_app(app_cfg, tmp_path / "c.yaml", start_worker=False)) as c:
        assert "FansCut UI" in c.get("/").text
        assert c.get("/healthz").json()["status"] == "ok"
        assert c.get("/api/jobs").json() == {"jobs": []}
        assert c.get("/docs").status_code == 200


def test_no_ui_dir_means_no_root_route(app_cfg, tmp_path, monkeypatch, sonarr_mock):
    from fastapi.testclient import TestClient

    from fanscut.server import create_app

    monkeypatch.setenv("FANSCUT_UI_DIR", str(tmp_path / "missing"))
    with TestClient(create_app(app_cfg, tmp_path / "c.yaml", start_worker=False)) as c:
        assert c.get("/").status_code == 404


# -- built UI ------------------------------------------------------------------
def test_serves_ui_with_spa_fallback(app_cfg, tmp_path, monkeypatch, sonarr_mock):
    from fastapi.testclient import TestClient

    from fanscut.server import create_app

    ui = tmp_path / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html>shell</html>")
    (ui / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setenv("FANSCUT_UI_DIR", str(ui))
    with TestClient(create_app(app_cfg, tmp_path / "c.yaml", start_worker=False)) as c:
        assert c.get("/").text == "<html>shell</html>"
        assert c.get("/series/folder%3AHarbor%20Lights/cut").text == "<html>shell</html>"
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/healthz").json()["status"] == "ok"
        assert c.get("/api/nope").status_code == 404  # API misses stay JSON 404s
        assert "shell" not in c.get("/api/nope").text
        assert c.get("/docs").status_code == 200
        assert c.get("/%2e%2e/secret.txt").text == "<html>shell</html>"  # no escaping the dist dir
