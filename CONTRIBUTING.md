# Contributing

## Before every commit

Run what CI runs, from the repo root:

```bash
./dev.sh validate
```

It runs `ruff check .`, `mypy fanscut/`, `pytest`, and the UI lint, unit tests
(Vitest) and build. Commit when it ends with `all good`. Checking
only the files you touched is not enough: CI checks the whole package, and mypy
catches what ruff does not.

Set up with `./dev.sh setup`, or by hand: `pip install -e ".[dev]"`. A venv
outside the repo works too: `FANSCUT_VENV=/path/to/venv ./dev.sh validate`.

## Conventions

- Tests, fixtures, comments, docs examples and commit messages use invented
  names: the show "Harbor Lights", characters Kaito, Mina, Ren, Sora, Tomoe.
  Never real titles, characters or catalogue ids. Real names may appear only in
  git-ignored places (`work/`, `docs/plans/`).
- No third-party filler dataset is committed. Tests use the tiny invented
  snapshot in `tests/fixtures/fillers/`.
- Tests are offline. HTTP is mocked with `responses`; the few tests that need a
  real ffmpeg generate tiny lavfi videos and skip when ffmpeg is missing.
- The repo does not run `ruff format`; run `ruff check` (safe fixes only).
- Plain imperative commit messages ("Add cut planner"), small logical commits.
- No auth, accounts or cloud services: FansCut is single-user and self-hosted.

## Layout

```
fanscut/        the Python package
  clients/      Sonarr and Plex
  library/      one view over Sonarr, Plex and a media folder; path mapping
  catalogues/   AniList and Jikan lookups, cached in SQLite, rate limited
  fillers/      AniFiller snapshot loader, Jikan flags, label resolution
  fingerprint/  audio features, matching, OP/ED/recap detection (numpy)
  planner.py    pure cut planner
  render/       ffmpeg wrappers, render plans, runner
  jobs.py       SQLite job queue and worker thread
  core.py       ties it together; routes/ stay thin
  server.py     FastAPI app, docs at /docs
ui/             React + Vite web UI (npm run dev / lint / test / build)
tests/
```
