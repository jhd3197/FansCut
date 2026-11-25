"""Shared fixtures. Everything is offline; HTTP is mocked with `responses`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import responses
from fastapi.testclient import TestClient

from fanscut.config import Config
from fanscut.db import Database
from fanscut.server import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SONARR = "http://sonarr.test:8989"
PLEX = "http://plex.test:32400"
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _has_prompture_video() -> bool:
    try:
        from prompture.media.understand import frames  # noqa: F401
    except ImportError:
        return False
    return True


# Scene indexing needs Prompture's frames module (contact sheets, shot cuts),
# which is newer than Prompture 1.19.1.
needs_prompture_video = pytest.mark.skipif(not _has_prompture_video(),
                                           reason="Prompture without media.understand.frames")


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.paths.data_dir = str(tmp_path / "data")
    c.paths.output_dir = str(tmp_path / "output")
    c.fillers.dir = str(tmp_path / "fillers")
    shutil.copytree(FIXTURES / "fillers", c.fillers.dir)
    return c


@pytest.fixture
def db() -> Database:
    return Database(":memory:")


def sonarr_series() -> list[dict]:
    return [{"id": 7, "title": "Harbor Lights", "year": 2020, "tvdbId": 111, "tmdbId": 222,
             "seriesType": "anime", "alternateTitles": [{"title": "Minato no Akari"}],
             "statistics": {"episodeFileCount": 6}}]


def sonarr_episodes(with_files: int = 6) -> list[dict]:
    return [{"id": 100 + n, "seriesId": 7, "seasonNumber": 1, "episodeNumber": n,
             "absoluteEpisodeNumber": n, "title": f"Episode {n}", "airDate": f"2020-01-{n:02d}",
             "episodeFileId": 200 + n if n <= with_files else 0} for n in range(1, 7)]


def sonarr_files() -> list[dict]:
    return [{"id": 200 + n, "seriesId": 7, "path": f"/tv/Harbor Lights/S01E{n:02d}.mkv",
             "mediaInfo": {"runTime": "00:24:00"}} for n in range(1, 7)]


@pytest.fixture
def sonarr_mock():
    """Sonarr answering for series 7 (Harbor Lights, six 24-minute episodes)."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{SONARR}/api/v3/series", json=sonarr_series())
        rsps.add(responses.GET, f"{SONARR}/api/v3/episode", json=sonarr_episodes())
        rsps.add(responses.GET, f"{SONARR}/api/v3/episodefile", json=sonarr_files())
        rsps.add(responses.GET, f"{SONARR}/api/v3/system/status", json={"version": "4.0.0"})
        yield rsps


@pytest.fixture
def app_cfg(cfg: Config) -> Config:
    cfg.library.sonarr.url = SONARR
    cfg.library.sonarr.api_key = "sonarr-key"
    cfg.catalogues.offline = True  # no catalogue traffic unless a test turns it on
    cfg.ffmpeg.container = "mkv"
    return cfg


@pytest.fixture
def client(app_cfg: Config, tmp_path: Path, sonarr_mock):
    app = create_app(app_cfg, tmp_path / "config.yaml", start_worker=False)
    with TestClient(app) as c:
        c.app_ref = app  # type: ignore[attr-defined]
        yield c


SERIES = "sonarr:7"


def make_video(path: Path, seconds: float = 6, size: str = "160x120", wav: Path | None = None,
               vcodec: str = "libx264") -> Path:
    """Tiny synthetic episode: testsrc video + sine (or the given wav) audio, a keyframe a second."""
    import subprocess

    audio = ["-i", str(wav)] if wav else ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc=duration={seconds}:size={size}:rate=12", *audio, "-c:v", vcodec, "-g", "12",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True)
    return path


@pytest.fixture(autouse=True)
def test_host(monkeypatch):
    """Starlette's TestClient sends Host: testserver; let the Host guard accept it."""
    from fanscut import security

    monkeypatch.setattr(security, "ALWAYS_ALLOWED", {"testserver"})
    security.reset_rate_limits()


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr("fanscut.clients.base.time.sleep", lambda _s: None)


class FakeLLM:
    """Stands in for the Prompture driver: replies with the queued text."""

    def __init__(self) -> None:
        self.reply = ""
        self.models: list[str] = []
        self.messages: list[list[dict]] = []

    def answering(self, reply):
        import contextlib
        import json as _json

        self.reply = reply if isinstance(reply, str) else _json.dumps(reply)
        return contextlib.nullcontext(self)

    def generate_messages(self, messages, options):
        self.messages.append(messages)
        return {"text": self.reply}


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    from fanscut import ai

    fake = FakeLLM()

    def driver_for(cfg, *, vision=False):
        model, _ = ai.model_spec(cfg, vision=vision)
        fake.models.append(model)
        return fake

    monkeypatch.setattr(ai, "driver_for", driver_for)
    return fake
