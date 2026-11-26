# Install

FansCut runs either as a Docker container or from source. Both serve the web
UI and the API on port `6464`.

## Docker

```bash
mkdir -p config data output && chown 1000:1000 config data output
cp config.docker.example.yaml config/config.yaml   # edit it
# edit docker-compose.yml: point the /media volume at your library
docker compose up -d
```

Open <http://localhost:6464>. Compose pulls `ghcr.io/jhd3197/fanscut:latest`
(amd64 and arm64). `:latest` is the newest release from `main`, and `:dev`
follows the dev branch. Every release also gets an `:X.Y.Z` tag to pin to.

The container runs as uid 1000, so `config`, `data` and `output` need to be
writable by it. For other ids, set `user:` or PUID/PGID. The library is
mounted read-only. FansCut never writes next to your originals; cuts go to the
output folder. For an NVIDIA GPU (NVENC), add the override:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

[Docker](docker.md) covers PUID/PGID, Unraid and Synology, path mapping,
getting cuts into Plex, reverse proxies, upgrades and backups.

## From source

You need Python 3.11+, Node 20+ and ffmpeg on your `PATH`.

```bash
./dev.sh setup      # pip install -e .[dev] and npm ci in ui/
./dev.sh serve      # the API (and ui/dist if you've built it) on :6464
./dev.sh ui         # Vite dev server on :5464, proxies /api to :6464
./dev.sh validate   # everything CI runs
```

On Windows use `.\dev.ps1` with the same commands. Without `dev.sh`:
`pip install -e ".[dev]"`, then `python -m fanscut serve`. To get the UI from
the Python server, run `npm run build` in `ui/` first.

## CLI

There's also a small CLI:

```text
fanscut serve | scan | doctor | fillers refresh | recipe ai
```

`fanscut recipe validate --file` checks a timeline recipe offline (see
[Timeline recipes](timelines.md)).

## Next steps

- [Configuration](configuration.md): sources, paths, secrets and defaults.
- [Security](security.md): read this before you open the port to other
  machines.
