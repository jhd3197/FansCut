"""Local Plex/Radarr/Sonarr detection fills empty settings, and only those."""

from __future__ import annotations

from fanscut import config
from fanscut.library import autodetect
from fanscut.library.autodetect import Found

ARR_XML = """<Config><Port>{port}</Port><UrlBase>{base}</UrlBase><EnableSsl>False</EnableSsl>
<ApiKey>{key}</ApiKey></Config>"""


def test_reads_an_arr_config_xml(tmp_path, monkeypatch):
    d = tmp_path / "Radarr"
    d.mkdir()
    (d / "config.xml").write_text(ARR_XML.format(port=7979, base="/radarr", key="k-radarr"))
    monkeypatch.setattr(autodetect, "_arr_dirs", lambda app: [tmp_path / app])
    f = autodetect._arr("Radarr", 7878)
    assert f == Found("radarr", "http://127.0.0.1:7979/radarr", "k-radarr", str(d / "config.xml"))
    assert autodetect._arr("Sonarr", 8989) is None  # no folder for it


def test_skips_a_config_without_a_key(tmp_path, monkeypatch):
    d = tmp_path / "Sonarr"
    d.mkdir()
    (d / "config.xml").write_text(ARR_XML.format(port=8989, base="", key=""))
    monkeypatch.setattr(autodetect, "_arr_dirs", lambda app: [tmp_path / app])
    assert autodetect._arr("Sonarr", 8989) is None


FOUND = [Found("radarr", "http://127.0.0.1:7878", "k-radarr", "x"),
         Found("sonarr", "http://127.0.0.1:8989", "k-sonarr", "y"),
         Found("plex", "http://127.0.0.1:32400", "t-plex", "registry")]


def test_fill_sets_empty_services():
    cfg = config.parse({})
    assert autodetect.fill(cfg, [], FOUND) == ["radarr", "sonarr", "plex"]
    assert cfg.library.radarr.url == "http://127.0.0.1:7878" and cfg.library.radarr.api_key == "k-radarr"
    assert cfg.library.plex.token == "t-plex"


def test_fill_keeps_what_is_set_and_never_sends_a_key_elsewhere():
    cfg = config.parse({"library": {
        "radarr": {"url": "http://localhost:7878", "api_key": ""},        # this machine: fill
        "sonarr": {"url": "http://nas.lan:8989", "api_key": ""},          # another host: leave
        "plex": {"url": "http://127.0.0.1:32400", "token": "mine"}}})     # already set: leave
    assert autodetect.fill(cfg, [], FOUND) == ["radarr"]
    assert cfg.library.radarr.url == "http://localhost:7878"  # the user's URL stays
    assert cfg.library.sonarr.api_key == ""
    assert cfg.library.plex.token == "mine"


def test_fill_leaves_environment_locked_secrets_alone():
    cfg = config.parse({})
    assert autodetect.fill(cfg, ["library.plex.token"], FOUND) == ["radarr", "sonarr"]
    assert cfg.library.plex.token == ""


def test_detect_endpoint_saves_and_reports_without_secrets(client, monkeypatch):
    monkeypatch.setattr(autodetect, "detect", lambda: FOUND[:1])
    core = client.app_ref.state.core
    core.cfg.library.radarr.url, core.cfg.library.radarr.api_key = "", ""
    r = client.post("/api/settings/detect").json()
    assert r["filled"] == ["radarr"]
    assert r["found"] == [{"service": "radarr", "url": "http://127.0.0.1:7878", "source": "x"}]
    assert "k-radarr" not in str(r)  # masked in the settings it returns
    assert core.cfg.library.radarr.api_key == "k-radarr"
