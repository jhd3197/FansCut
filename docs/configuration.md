# Configuration

Copy `config.example.yaml` to `config.yaml` (or set `FANSCUT_CONFIG` to the
file's path). Every key is optional; the example lists the defaults. Most of it
can also be changed on the Settings page, which writes the same file. Secrets
(Sonarr key, Plex token) can live in that file or come from the environment,
see [Secrets](#secrets) below.

The Docker image reads `/config/config.yaml`; start from
`config.docker.example.yaml` there.

## Sections at a glance

| Section | What it's for |
| --- | --- |
| `server` | host and port (default `127.0.0.1:6464`; the container uses `0.0.0.0`), authentication, allowed host names, trusted proxies |
| `network` | timeout for Sonarr/Radarr/Plex calls, outbound proxy |
| `art` | whether poster and fan art is fetched through FansCut, and the art cache size |
| `paths` | `data_dir` (SQLite, filler snapshot, scratch) and `output_dir` |
| `library` | `sonarr`, `radarr`, `plex`, `folder`, `movies`, and `path_mappings` (remote prefix to local prefix) |
| `catalogues` | `offline: true` means never call AniList, Jikan or GitHub |
| `fillers` | where the snapshot lives, where to refresh it from, whether to use Jikan as a fallback |
| `fingerprint` | sample rate, how many minutes of head/tail to analyse, min/max segment length, match threshold |
| `ffmpeg` | binaries, `nvenc`, CRF, x264 preset, audio bitrate, `mkv` or `mp4` |
| `render` | output cache: default keep flag, auto-delete days for non-kept renders, preferred audio language |
| `automation` | Sonarr webhook: accept it, run detection first, keep flag for its renders |
| `ai` | the Prompture model (and vision model) for AI cuts from notes and scene tagging |
| `cut` | default preset, unknown policy, padding, merge gap, minimum segment, transition |
| `schedule` | cron schedules for the background tasks |
| `notify` | where to send a message when a job is done or failed |

## server

```yaml
server:
  host: 127.0.0.1        # use 0.0.0.0 inside a container
  port: 6464
  auth: none             # api_key = every API call (and the UI) needs the key
  api_key: ""            # generated on first start; webhooks always need it
  viewer_key: ""         # optional read-only key
  allowed_hosts: []      # extra Host names to answer to (e.g. fanscut.lan); "*" = any
  trusted_proxies: []    # reverse proxy IPs whose X-Forwarded-For is believed
```

- `auth`: `none` (the default) or `api_key`. With `api_key`, every `/api` call
  and the UI ask for the key.
- `api_key`: generated on first start if empty. Webhooks always need it, even
  with `auth: none`. Settings, Automation shows it and can make a new one.
- `viewer_key`: a second, optional key that may only read (`GET`, `HEAD`,
  `OPTIONS`). Writes with it are refused.
- `allowed_hosts`: host names FansCut answers to besides loopback, for example
  `fanscut.lan`. `"*"` accepts any.
- `trusted_proxies`: reverse proxy addresses whose `X-Forwarded-For` header is
  believed, so client addresses (and rate limits) are per real client.

See [Security](security.md) for how these fit together.

## network

```yaml
network:
  timeout: 10            # seconds to wait for Sonarr/Radarr/Plex
  proxy: ""              # outbound proxy for internet calls, e.g. http://proxy:3128
  proxy_bypass_local: true  # LAN servers skip the proxy
```

`proxy` applies to internet calls (catalogues, filler data, art, community
cuts). With `proxy_bypass_local: true`, servers on your LAN are reached
directly.

## art

```yaml
art:
  cache_remote: false    # fetch TMDB/TVDB art through FansCut (browsers never contact them)
  cache_mb: 1024         # art cache size cap; oldest images go first
```

With `cache_remote: true`, FansCut downloads remote poster art itself and
serves it from its cache, so your browser never contacts TMDB or TVDB. The
cache is capped at `cache_mb`; the oldest images are removed first.

## paths

```yaml
paths:
  data_dir: ./data       # SQLite db, filler snapshot, scratch space
  output_dir: ./output   # finished cuts land here
```

`data_dir` also holds the logs (`<data_dir>/logs`), the filler snapshot
(`<data_dir>/fillers`) and community cuts (`<data_dir>/recipes`).

## library

```yaml
library:
  sonarr:
    url: ""              # e.g. http://sonarr:8989
    api_key: ""
  radarr:
    url: ""              # e.g. http://radarr:7878 (movies as timeline sources)
    api_key: ""
  plex:
    url: ""              # e.g. http://plex:32400
    token: ""
    sections: []         # section keys or titles; empty = every show library
    movie_sections: []   # empty = every movie library
  folder:
    path: ""             # plain mode: one subfolder per series, no Sonarr needed
  movies:
    path: ""             # one subfolder per movie; "{tmdb-555}" in the name sets the id
  path_mappings: []
  #  - remote: /tv
  #    local: /media/tv
```

Any of these can be on at once; a source is on when its `url` or `path` is set.

Folder mode expects one subfolder per series, with files named like
`Harbor Lights - S01E03.mkv` (or `1x03`, or an absolute number).

Sonarr and Plex report paths as they see them. If FansCut sees the same files
somewhere else, map the prefix in `path_mappings` (the longest match wins).
[Docker](docker.md#sonarr-radarr-and-plex-paths) has a worked example.

## catalogues and fillers

```yaml
catalogues:
  offline: false         # true = never call AniList/Jikan/GitHub, use caches only
  cache_days: 14
  tmdb_api_key: ""       # cast for films outside Plex; prefer FANSCUT_TMDB_API_KEY

fillers:
  dir: ""                # default <data_dir>/fillers; every *.json in it is read
  url: https://raw.githubusercontent.com/AniraTeam/AniFiller/main/anifiller.min.json
  use_jikan: true        # fall back to Jikan filler/recap flags when AniFiller has no entry
```

[Filler data](filler-data.md) explains where labels come from.

## fingerprint

```yaml
fingerprint:             # OP/ED/recap detection
  sample_rate: 8000
  head_minutes: 5        # audio analysed from the start of each episode
  tail_minutes: 5        # ...and from the end
  min_segment_seconds: 15
  max_segment_seconds: 180
  threshold: 0.75        # frame similarity counted as "the same audio"
```

See [OP/ED and recap trimming](trimming.md).

## ffmpeg and render

```yaml
ffmpeg:
  binary: ffmpeg
  probe_binary: ffprobe
  nvenc: false           # true = h264_nvenc instead of libx264 when re-encoding
  crf: 20
  preset: medium         # x264 preset
  audio_bitrate: 192k
  container: mkv         # mkv | mp4

render:                  # output cache: an unchanged cut replays the existing file
  keep: true             # default keep flag for new renders
  ephemeral_days: 7      # auto-delete non-kept renders older than this; 0 = never
  language: ""           # preferred audio language tag (jpn, eng…); "" = first track
```

`ffmpeg.binary` and `render.effectcraft_cli` can only be set in the file, never
through the settings API (see [Security](security.md)). With
`render.effectcraft_cli` set, segment grades go through
[EffectCraft](https://github.com/storytold/effectcraft) instead of ffmpeg.

## automation

```yaml
automation:              # POST /api/webhooks/sonarr from a Sonarr Webhook connection
  sonarr_webhook: false  # true = a Download/Upgrade event queues detect + render
  auto_detect: true      # run OP/ED detection on the affected seasons first
  keep: true             # keep flag for webhook-triggered renders
```

See [Automation](automation.md).

## ai

```yaml
ai:                      # cuts from notes and scene tagging, through Prompture
  model: ""              # e.g. claude/claude-opus-5-5, ollama/qwen3:8b, or a bare name with base_url
  vision_model: ""       # looks at frame grids when tagging; empty = model (e.g. ollama/qwen3-vl:8b)
  base_url: ""           # OpenAI-compatible endpoint for a bare model name, e.g. http://localhost:11434/v1
  api_key: ""            # prefer the FANSCUT_AI_API_KEY env var over this file
  timeout: 120
```

`model` is a [Prompture](https://github.com/jhd3197/prompture) model string;
provider keys (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, ...) come from the
environment as Prompture reads them. A bare name such as `qwen2.5:7b` with
`base_url` set keeps the old OpenAI-compatible setup working. See
[AI cuts from notes](ai-cuts.md) and [Scene tagging](scene-tagging.md).

## cut

```yaml
cut:                     # defaults for every series; each series can override
  preset: canon_only     # canon_only | canon_mixed | everything | custom
  unknown_policy: keep   # episodes with no label: keep | skip
  keep_intro_first_episode: true
  pad: 0.5               # seconds kept around every trimmed edge
  merge_gap: 1.0         # pieces closer than this (same file) are joined
  min_segment: 5.0       # shorter pieces are dropped
  transition: none       # none | fade (fade re-encodes)
```

## schedule

```yaml
schedule:                # cron (minute hour day month weekday, local time); "" = off
  prune: "0 5 * * *"     # art cache, finished jobs older than 30 days, old catalogue replies
  sweep_renders: "30 4 * * *"
  library_refresh: "*/10 * * * *"   # re-list sources in the background
  fillers_refresh: "0 6 * * 1"
  cut_updates: "0 7 * * *"
  library_check: "0 3 * * 0"
```

Standard 5-field cron in local time. An empty string turns a task off. The
schedules can also be edited under Settings, Automation. See
[Automation](automation.md#scheduled-tasks) for what each task does.

## notify

```yaml
notify:                  # tell something when a job is done or failed
  targets: []
  # - url: https://ntfy.sh/my-fanscut
  #   format: ntfy         # json | discord | ntfy
  #   events: [done, failed]
```

See [Automation](automation.md#notifications).

## community

`community.index_url` is the community index Discover reads, and
`community.github_token` (or `FANSCUT_GITHUB_TOKEN`) lets "New cut from
template" push to a private GitHub repo. See
[Discover and community cuts](discover.md).

## Secrets

Only five values are secret or environment-specific: the Sonarr URL and API
key, the Plex URL and token, and the AI endpoint key. You have three ways to
provide them:

1. **`config.yaml`**: simplest. Keep it private; FansCut saves it with mode 0600.
2. **Environment variables**: `FANSCUT_SONARR_URL`, `FANSCUT_SONARR_API_KEY`,
   `FANSCUT_RADARR_URL`, `FANSCUT_RADARR_API_KEY`,
   `FANSCUT_PLEX_URL`, `FANSCUT_PLEX_TOKEN`, `FANSCUT_AI_API_KEY`,
   `FANSCUT_TMDB_API_KEY`. A non-empty
   variable beats `config.yaml`, and is never written back to it, even when you
   save on the Settings page (those fields show "set by environment"). See
   `.env.example`.
3. **`*_FILE` variables**: the same names with a `_FILE` suffix
   (`FANSCUT_PLEX_TOKEN_FILE=/run/secrets/plex_token`) point at a file whose
   trimmed contents are the value. This is the Docker/Compose secrets style. If
   both `NAME` and `NAME_FILE` are set, `NAME` wins.

The settings API masks secrets (`***`), and FansCut never logs them or puts
them in error messages.

### Doppler

Any secret manager that can inject environment variables works; with
[Doppler](https://www.doppler.com/) create the `FANSCUT_*` secrets above
in a project and config of your choosing, then:

```bash
doppler setup
doppler run -- ./dev.sh serve
doppler run -- docker compose up -d
```

`docker-compose.yml` passes the variables through to the container.
