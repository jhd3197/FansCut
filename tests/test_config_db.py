import os
import stat

import pytest

from fanscut import config
from fanscut.db import Database
from fanscut.errors import ConfigError


def test_missing_file_gives_defaults(tmp_path):
    cfg = config.load(tmp_path / "nope.yaml")
    assert cfg.server.port == 6464
    assert cfg.cut.preset == "canon_only"


def test_load_partial_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("server:\n  port: 7000\nlibrary:\n  folder:\n    path: /media\n")
    cfg = config.load(p)
    assert cfg.server.port == 7000 and cfg.library.folder.path == "/media"
    assert cfg.ffmpeg.binary == "ffmpeg"


@pytest.mark.parametrize("text", ["server: [1, 2", "- just\n- a list\n", "server:\n  port: many\n"])
def test_bad_config_raises(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    with pytest.raises(ConfigError):
        config.load(p)


def test_env_path(tmp_path, monkeypatch):
    p = tmp_path / "env.yaml"
    p.write_text("server:\n  port: 1234\n")
    monkeypatch.setenv("FANSCUT_CONFIG", str(p))
    assert config.load().server.port == 1234


def test_secrets_masked_and_kept_on_update():
    cfg = config.parse({"library": {"sonarr": {"url": "http://s", "api_key": "secret"}}})
    shown = config.masked(cfg)
    assert shown["library"]["sonarr"]["api_key"] == config.MASK
    updated = config.merge_update(cfg, shown | {"server": {"port": 9000}})
    assert updated.library.sonarr.api_key == "secret"
    assert updated.server.port == 9000
    changed = config.merge_update(cfg, {"library": {"sonarr": {"api_key": "new"}}})
    assert changed.library.sonarr.api_key == "new"


def test_save_round_trip(tmp_path):
    cfg = config.parse({"cut": {"pad": 2.0}})
    config.save(cfg, tmp_path / "sub" / "c.yaml")
    assert config.load(tmp_path / "sub" / "c.yaml").cut.pad == 2.0


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "x.db"
    d = Database(path)
    d.execute("INSERT INTO matches (series_key, season) VALUES ('a', -1)")
    d.close()
    d2 = Database(path)  # reopening must not re-run the schema
    assert d2.one("SELECT COUNT(*) AS n FROM matches")["n"] == 1
    assert d2.one("PRAGMA user_version")[0] >= 1


# -- secrets from the environment ------------------------------------------------
ENV_NAMES = [n for k in config.ENV_FIELDS for n in (k, f"{k}_FILE")]


@pytest.fixture
def clean_env(monkeypatch):
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


def test_env_overrides_file(tmp_path, clean_env):
    p = tmp_path / "c.yaml"
    p.write_text("library:\n  sonarr:\n    url: http://file\n    api_key: from-file\n")
    clean_env.setenv("FANSCUT_SONARR_API_KEY", "from-env")
    clean_env.setenv("FANSCUT_PLEX_URL", "")  # empty = no override
    cfg = config.load(p)
    assert cfg.library.sonarr.api_key == "from-env"
    assert cfg.library.sonarr.url == "http://file"
    assert cfg.library.plex.url == ""


def test_env_file_variant_and_precedence(tmp_path, clean_env):
    f = tmp_path / "token"
    f.write_text("  plex-from-file\n")
    clean_env.setenv("FANSCUT_PLEX_TOKEN_FILE", str(f))
    assert config.load(tmp_path / "none.yaml").library.plex.token == "plex-from-file"
    clean_env.setenv("FANSCUT_PLEX_TOKEN", "plain")
    assert config.load(tmp_path / "none.yaml").library.plex.token == "plain"


def test_env_file_unreadable(tmp_path, clean_env):
    clean_env.setenv("FANSCUT_PLEX_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(ConfigError):
        config.load(tmp_path / "none.yaml")


def test_save_never_writes_env_values(tmp_path, clean_env):
    p = tmp_path / "c.yaml"
    p.write_text("library:\n  sonarr:\n    api_key: from-file\n")
    clean_env.setenv("FANSCUT_SONARR_API_KEY", "from-env")
    clean_env.setenv("FANSCUT_PLEX_TOKEN", "plex-env")
    config.save(config.load(p), p)
    text = p.read_text()
    assert "from-env" not in text and "plex-env" not in text
    assert "from-file" in text  # the file keeps its own value
    clean_env.delenv("FANSCUT_SONARR_API_KEY")
    assert config.load(p).library.sonarr.api_key == "from-file"


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_save_mode_0600(tmp_path):
    p = tmp_path / "c.yaml"
    config.save(config.Config(), p)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_yaml_error_does_not_quote_file(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("library:\n  sonarr:\n    api_key: [topsecret\n")
    with pytest.raises(ConfigError) as ei:
        config.load(p)
    assert "topsecret" not in str(ei.value)


def test_settings_api_env_round_trip(tmp_path, clean_env):
    from fastapi.testclient import TestClient

    from fanscut.server import create_app

    p = tmp_path / "config.yaml"
    p.write_text("library:\n  sonarr:\n    api_key: from-file\n")
    clean_env.setenv("FANSCUT_SONARR_API_KEY", "from-env")
    clean_env.setenv("FANSCUT_PLEX_TOKEN", "plex-env")
    with TestClient(create_app(config.load(p), p, start_worker=False)) as c:
        got = c.get("/api/settings").json()
        assert got["library"]["sonarr"]["api_key"] == config.MASK
        assert got["library"]["plex"]["token"] == config.MASK
        assert "from-env" not in c.get("/api/settings").text
        assert got["env_locked"] == ["library.plex.token", "library.sonarr.api_key"]
        got["server"]["port"] = 7001
        assert c.put("/api/settings", json=got).status_code == 200
        assert c.app.state.core.cfg.library.sonarr.api_key == "from-env"  # type: ignore[attr-defined]
    text = p.read_text()
    assert "from-env" not in text and "plex-env" not in text and "from-file" in text
    assert "7001" in text
