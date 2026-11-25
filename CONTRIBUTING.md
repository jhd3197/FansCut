# Contributing

## Before every commit

Run what CI runs, from the repo root:

```bash
./dev.sh validate
```

It runs `ruff check .`, `mypy fanscut/`, `pytest` (with an 85% coverage floor in
CI), and the UI lint, type check (`tsc` over files marked `// @ts-check`), unit
tests (Vitest) and build. Browser tests are separate: `cd ui && npm run build &&
npm run e2e` (Playwright starts its own server on a throwaway library). Commit when it ends with `all good`. Checking
only the files you touched is not enough: CI checks the whole package, and mypy
catches what ruff does not.

Set up with `./dev.sh setup`, or by hand: `pip install -e ".[dev]"`.

Dependencies: `requirements.txt` lists the runtime packages; `requirements.lock`
pins them and everything they pull in, with hashes, for the Docker image.
After changing `requirements.txt`, refresh the lock:
`uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes -o requirements.lock`. A venv
outside the repo works too: `FANSCUT_VENV=/path/to/venv ./dev.sh validate`.

Optional: `pip install pre-commit && pre-commit install` runs ruff, mypy and
gitleaks on each commit.

## Branches and releases

- Work lands on `dev`. Feature pull requests target `dev`, never `main`.
- `main` only takes pull requests from this repo's `dev`. The Main Promotion
  check fails any other source. Merge with a regular merge commit, not a
  squash.
- Every push to `dev` publishes `ghcr.io/jhd3197/fanscut:dev`. Every merge into
  `main` publishes `:latest` and creates the `vX.Y.Z` GitHub release, with
  notes generated from the merged pull requests. Both happen only after the
  backend, frontend, Docker and secret-scan checks pass (`release.yml`).
- Don't tag or bump the version by hand. The next version comes from the
  newest `vX.Y.Z` tag (patch + 1). `fanscut/__init__.py` only holds the seed
  for the first release.

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
- No accounts or cloud services: FansCut is single-user and self-hosted. The
  only auth is the optional API key (`fanscut/security.py`).

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
