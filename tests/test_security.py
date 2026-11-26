"""Request guards: Host check, cross-site refusal, API-key mode, settings and test-endpoint safety."""

from __future__ import annotations

import responses

from fanscut import config, security
from fanscut.community import GitHub
from tests.conftest import SONARR
from tests.test_api import core_of


def test_host_guard_blocks_rebinding(client):
    assert client.get("/api/settings", headers={"Host": "evil.example"}).status_code == 421
    assert client.get("/api/settings", headers={"Host": "127.0.0.1:6464"}).status_code == 200
    assert client.get("/api/settings", headers={"Host": "[::1]:6464"}).status_code == 200


def test_host_allowed_rules():
    assert security.host_allowed("nas.lan:6464", "0.0.0.0", [])
    assert not security.host_allowed("nas.lan", "127.0.0.1", [])
    assert security.host_allowed("nas.lan", "127.0.0.1", ["nas.lan"])
    assert not security.host_allowed("other.lan", "0.0.0.0", ["nas.lan"])


def test_cross_site_writes_refused(client):
    r = client.put("/api/settings", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.put("/api/settings", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert client.put("/api/settings", json={},
                      headers={"Origin": "http://testserver"}).status_code == 200


def test_saved_key_never_sent_to_a_new_url(client, sonarr_mock):
    sonarr_mock.add(responses.GET, "http://attacker.test/api/v3/system/status", json={})
    r = client.post("/api/settings/test/sonarr",
                    json={"url": "http://attacker.test", "api_key": "***"}).json()
    assert not r["ok"] and "key again" in r["message"]
    assert not any("attacker.test" in c.request.url for c in sonarr_mock.calls)


def test_folder_test_needs_absolute_path(client):
    r = client.post("/api/settings/test/folder", json={"path": "relative/dir"}).json()
    assert not r["ok"] and "absolute" in r["message"]


def test_binaries_are_read_only_over_http(client):
    r = client.put("/api/settings", json={"ffmpeg": {"binary": "/tmp/evil"}})
    assert r.status_code == 400 and "config.yaml" in r.json()["detail"]
    same = client.put("/api/settings", json={"ffmpeg": {"binary": "ffmpeg"}})
    assert same.status_code == 200


def test_unknown_settings_refused(client):
    r = client.put("/api/settings", json={"library": {"sonar": {}}})
    assert r.status_code == 400 and "unknown setting" in r.json()["detail"]


def test_every_secret_is_masked(client):
    s = client.get("/api/settings").json()
    assert s["server"]["api_key"] == "***"
    assert "server.api_key" in config.SECRET_PATHS and "library.plex.token" in config.SECRET_PATHS


def test_api_key_mode(client):
    core = core_of(client)
    core.cfg.server.auth = "api_key"
    core.cfg.server.viewer_key = "viewer-secret"
    key = core.cfg.server.api_key
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/auth/status").json() == {"auth": "api_key", "role": None}
    assert client.get("/api/settings", headers={"X-Api-Key": key}).status_code == 200
    assert client.post("/api/auth/login", json={"key": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"key": key}).json()["role"] == "admin"
    assert client.get("/api/settings").status_code == 200  # the cookie works now
    assert key not in str(client.cookies.get("fanscut_key"))  # a token, not the key
    client.post("/api/auth/logout")
    client.cookies.clear()
    v = {"X-Api-Key": "viewer-secret"}
    assert client.get("/api/settings", headers=v).status_code == 200
    assert client.put("/api/settings", json={}, headers=v).status_code == 403


def test_rate_limit(client):
    codes = [client.post("/api/settings/test/folder", json={"path": "/"}).status_code
             for _ in range(22)]
    assert codes[0] == 200 and codes[-1] == 429


def test_github_token_only_goes_to_github():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://cdn.example.test/p.jpg", body=b"\xff\xd8\xff",
                 content_type="image/jpeg")
        rsps.add(responses.GET, "https://api.github.com/repos/kaito/cut", json={})
        gh = GitHub("tok")
        gh.poster("https://cdn.example.test/p.jpg")
        gh._get("https://api.github.com/repos/kaito/cut")
        assert "Authorization" not in rsps.calls[0].request.headers
        assert rsps.calls[1].request.headers["Authorization"] == "Bearer tok"


def test_capped_download():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://cdn.example.test/big.jpg", body=b"x" * 2000,
                 content_type="image/jpeg")
        import pytest

        from fanscut.community import CommunityError
        with pytest.raises(CommunityError, match="too large"):
            GitHub()._get("https://cdn.example.test/big.jpg", limit=1000)


def test_sonarr_test_returns_tags(client, sonarr_mock):
    sonarr_mock.add(responses.GET, f"{SONARR}/api/v3/tag", json=[{"id": 1, "label": "fanscut"}])
    sonarr_mock.add(responses.GET, f"{SONARR}/api/v3/qualityprofile", json=[])
    r = client.post("/api/settings/test/sonarr").json()
    assert r["ok"] and r["tags"] == [{"id": 1, "label": "fanscut"}]
