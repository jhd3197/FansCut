# Running FansCut in Docker

The image is `ghcr.io/jhd3197/fanscut`, built for amd64 and arm64.

| Tag | What it is |
|---|---|
| `latest` | the newest release from `main` |
| `X.Y.Z` | one release, pinned (for example `0.1.0`) |
| `dev` | the newest build of the `dev` branch |
| `X.Y.Z.devN` | one dev build |
| `sha-<commit>` | the build of one exact commit |

Use `latest` unless you want to test what's coming.

## Quick start

```bash
mkdir -p fanscut/config fanscut/data fanscut/output && cd fanscut
curl -fsSLO https://raw.githubusercontent.com/jhd3197/FansCut/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/jhd3197/FansCut/main/config.docker.example.yaml -o config/config.yaml
chown 1000:1000 config data output
# edit docker-compose.yml: point /path/to/your/media at your library
# edit config/config.yaml: folder mode, or your Sonarr / Radarr / Plex
docker compose up -d
```

Open <http://localhost:6464>.

## Folders

| In the container | What goes there | Mode |
|---|---|---|
| `/config` | `config.yaml` | read-write |
| `/data` | the SQLite database, filler snapshot, render cache and scratch space | read-write |
| `/media` | your library | read-only |
| `/output` | finished cuts | read-write |

`/data` can get large while rendering, because it holds the scratch files of the
job in progress. Put it on a disk with room for a full-size copy of a film.

## Which user it runs as

The container runs as uid and gid `1000`, so `config`, `data` and `output` must be
writable by `1000`. If your host uses other ids (Unraid is `99:100`, Synology
is usually `1026:100`), pick one of these.

**Set `user:` (simplest, keeps every hardening option):**

```yaml
services:
  fanscut:
    user: "99:100"
```

**Use PUID / PGID**, as Unraid templates and most *arr guides do. The container
starts as root, moves its user to `PUID:PGID`, takes ownership of the top of
`/config`, `/data` and `/output`, then drops root before FansCut starts:

```yaml
services:
  fanscut:
    user: "0:0"
    environment:
      PUID: 99
      PGID: 100
      UMASK: "002"     # optional; the default is 022
    cap_add: [CHOWN, SETUID, SETGID]   # the compose file drops ALL capabilities
```

Only the top folder is re-owned, never its contents. If you're switching ids
on an existing install, run `chown -R 99:100 config data output` once on the
host.

## Plex and the output folder

Plex has to see the finished cuts for the library refresh and the "FansCut"
collection to work. Mount `/output` at a folder that one of your Plex libraries
already scans. For example, if Plex's Movies library includes
`/mnt/user/media/FansCut`:

```yaml
    volumes:
      - /mnt/user/media/FansCut:/output
```

Keep this folder separate from your originals. FansCut never writes next to
your source files.

## Sonarr, Radarr and Plex paths

Sonarr, Radarr and Plex report file paths as they see them, and FansCut reads
the files through `/media`. If the paths differ, map them in `config.yaml`:

```yaml
library:
  path_mappings:
    - remote: /tv          # what Sonarr says
      local: /media/tv     # where the same folder is in this container
    - remote: /movies
      local: /media/movies
```

To reach services running on the Docker host itself, use
`http://host.docker.internal:<port>`. The compose file sets this name up.

## Secrets

Put secrets in a `.env` file next to `docker-compose.yml` (see `.env.example`)
or use Docker secrets with the `_FILE` variables, for example
`FANSCUT_PLEX_TOKEN_FILE=/run/secrets/plex_token`. Secrets set as variables
override `config.yaml` and are never written back to it.

Set `TZ` (for example `TZ=Europe/Madrid`) so job times show your local time.

## GPU encoding

The image's ffmpeg includes NVENC (NVIDIA), VAAPI and QSV (Intel/AMD).

**NVIDIA:** install the NVIDIA driver and the NVIDIA Container Toolkit on the
host, then start with the GPU override:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
docker exec fanscut ffmpeg -hide_banner -encoders | grep nvenc
```

Then turn on NVENC in Settings > Rendering.

FansCut's settings only offer NVENC so far. VAAPI and QSV are in the image but
not yet selectable.

## Effects through EffectCraft

Segments with grades and effects render through ffmpeg unless an EffectCraft
CLI is configured. To use it, mount a Linux build of the CLI read-only, for
example `- ./effectcraft:/opt/effectcraft:ro`, then set this in `config.yaml`:

```yaml
render:
  effectcraft_cli: /opt/effectcraft/effectcraft-cli
```

## Network and access

FansCut has no login. The compose file publishes the port on `127.0.0.1`
only. To use it from other machines, either:

- change the port line to `"6464:6464"` and keep it on a trusted network, or
- put it behind a reverse proxy that adds authentication. For example, with
  Caddy:

  ```
  fanscut.example.lan {
      basicauth { you <bcrypt-hash> }
      reverse_proxy fanscut:6464
  }
  ```

  Put both containers on the same Docker network, and keep the published port
  local.

Don't expose it to the internet.

## Upgrading

```bash
docker compose pull && docker compose up -d
```

A render in progress gets 30 seconds to stop. A job that was cut off goes
back in the queue and starts again when the new container comes up. To stay on one version, pin a tag
(`ghcr.io/jhd3197/fanscut:0.1.0`) and change it when you choose.

## Backups

Everything FansCut knows is in `/config` and `/data`. Stop the container (or
wait until no job is running) and copy both folders. The render cache in
`/data` can be left out: FansCut rebuilds it.

## Unraid and Synology

- **Unraid:** add the container from the image `ghcr.io/jhd3197/fanscut:latest`.
  - Set `PUID=99`, `PGID=100` and `UMASK=002`.
  - Under Extra Parameters, add `--user 0:0 --cap-add CHOWN --cap-add SETUID --cap-add SETGID`.
  - Map `/config`, `/data` and `/output` to appdata or share paths, and `/media` read-only.
  - Map port 6464.
- **Synology (Container Manager):** create a project from `docker-compose.yml`.
  - Set `user: "<uid>:<gid>"` to your DSM user. Run `id` over SSH to find it.
  - Point the volumes at your shared folders.

## Building from source

```bash
git clone https://github.com/jhd3197/FansCut && cd FansCut
# in docker-compose.yml: comment out `image:` and uncomment `build: .`
docker compose up -d --build
```
