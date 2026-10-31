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
