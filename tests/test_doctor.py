"""Doctor rows for the optional services and tools Health lists."""

from __future__ import annotations

import sys

import responses

from fanscut import config, doctor


def _cfg(tmp_path, **raw):
    base = {"paths": {"data_dir": str(tmp_path / "d"), "output_dir": str(tmp_path / "o")}}
    return config.parse({**base, **raw})


def _row(rep, name):
    return next(c for c in rep["checks"] if c["name"] == name)


def test_optional_rows_are_off_without_failing(tmp_path):
    rep = doctor.run(_cfg(tmp_path))
    for name in ("plex", "effectcraft"):
        row = _row(rep, name)
        assert row["ok"] and row["off"]
    assert _row(rep, "ffprobe")["detail"]


@responses.activate
def test_plex_row_names_the_movie_and_show_libraries(tmp_path):
    responses.get("http://plex.test:32400/library/sections", json={"MediaContainer": {"Directory": [
        {"key": "1", "type": "movie", "title": "Movies"},
        {"key": "2", "type": "show", "title": "TV Shows"},
        {"key": "3", "type": "artist", "title": "Music"}]}})
    cfg = _cfg(tmp_path, library={"plex": {"url": "http://plex.test:32400", "token": "t"}})
    row = _row(doctor.run(cfg), "plex")
    assert row == {"name": "plex", "ok": True, "detail": "connected · Movies, TV Shows"}


@responses.activate
def test_plex_row_fails_on_rejected_token_without_retrying(tmp_path):
    responses.get("http://plex.test:32400/library/sections", status=401)
    cfg = _cfg(tmp_path, library={"plex": {"url": "http://plex.test:32400", "token": "bad"}})
    rep = doctor.run(cfg)
    row = _row(rep, "plex")
    assert not row["ok"] and "credentials" in row["detail"] and not rep["ok"]
    assert len(responses.calls) == 1


def test_effectcraft_row_checks_the_configured_path(tmp_path):
    missing = _row(doctor.run(_cfg(tmp_path, render={"effectcraft_cli": str(tmp_path / "nope")})),
                   "effectcraft")
    assert not missing["ok"] and "not found" in missing["detail"]
    # any executable on this machine stands in for the CLI
    found = _row(doctor.run(_cfg(tmp_path, render={"effectcraft_cli": sys.executable})),
                 "effectcraft")
    assert found["ok"] and not found.get("off") and found["detail"].startswith("found")
