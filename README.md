# FansCut

[![ci](https://github.com/jhd3197/FansCut/actions/workflows/ci.yml/badge.svg)](https://github.com/jhd3197/FansCut/actions/workflows/ci.yml)

FansCut makes "fan cuts" of shows you already own: skip the filler, keep the
canon, trim recaps, openings and endings, and render what's left as one file
with a chapter per episode. It's a small self-hosted web app that sits next to
Sonarr and Plex (or just a folder of files). No accounts, no cloud, one user.

It's a side project I built for myself, so expect rough edges. It works on my
library; yours may find things I didn't think of.

FansCut is a sibling of [Doblarr](https://github.com/jhd3197/Doblarr): same
stack, same config style, same port convention. Doblarr dubs, FansCut cuts.
They don't depend on each other.

## What it does

- **Library**: reads series from Sonarr, Plex, or a plain media folder (any mix
  of the three). Path mapping handles containers that see different paths.
- **Matching**: links a series to its MAL and AniList ids (AniList and Jikan
  search, cached, rate limited) or lets you type the ids in yourself.
- **Filler labels**: every episode is `canon`, `mixed`, `filler` or `unknown`.
  You can override any of them.
- **Presets**: canon only, canon + mixed, everything, or custom (tick episodes
  one by one). Per-episode include/exclude beats the preset.
- **OP / ED / recap trimming**: fingerprints the audio across a season to find
  the shared opening and ending, recaps at the top, and "next time" previews at
  the end. Every range is editable in the UI.
- **Cut planner**: turns all of that into an ordered cut list (with padding,
  merging and a minimum segment length) and tells you how much time you saved.
- **Renderer**: ffmpeg concat. Stream copy when the files match, re-encode when
  they don't (or when you ask). Runs as a background job with progress and
  cancel. A dry run shows the exact ffmpeg command without running it.
- **Recipes**: export your choices for a series as a small JSON file and import
  them somewhere else.
- **REST API** under `/api`, docs at `/docs`, health at `/healthz`.

## Quickstart

### Docker

```bash
mkdir -p config data output && chown 1000:1000 config data output
cp config.docker.example.yaml config/config.yaml   # edit it
# edit docker-compose.yml: point the /media volume at your library
docker compose up -d --build
```

Open <http://localhost:6464>. The container runs as uid 1000, so `config`,
`data` and `output` need to be writable by it. The library is mounted read-only.
FansCut never writes next to your originals; cuts go to the output folder.

### Local dev

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

There's also a small CLI: `fanscut serve | scan | doctor | fillers refresh`.

## Config

Copy `config.example.yaml` to `config.yaml` (or set `FANSCUT_CONFIG`). Every key
is optional. Most of it can also be changed on the Settings page, which writes
the same file. Secrets (Sonarr key, Plex token) can live in that file or come
from the environment, see [Secrets](#secrets).

| Section | What it's for |
| --- | --- |
| `server` | host and port (default `127.0.0.1:6464`; the container uses `0.0.0.0`) |
| `paths` | `data_dir` (SQLite, filler snapshot, scratch) and `output_dir` |
| `library` | `sonarr`, `plex`, `folder`, and `path_mappings` (remote prefix to local prefix) |
| `catalogues` | `offline: true` means never call AniList, Jikan or GitHub |
| `fillers` | where the snapshot lives, where to refresh it from, whether to use Jikan as a fallback |
| `fingerprint` | sample rate, how many minutes of head/tail to analyse, min/max segment length, match threshold |
| `ffmpeg` | binaries, `nvenc`, CRF, x264 preset, audio bitrate, `mkv` or `mp4` |
| `cut` | default preset, unknown policy, padding, merge gap, minimum segment, transition |

Folder mode expects one subfolder per series, with files named like
`Harbor Lights - S01E03.mkv` (or `1x03`, or an absolute number).

## Secrets

Only four values are secret or environment-specific: the Sonarr URL and API
key, and the Plex URL and token. You have three ways to provide them:

1. **`config.yaml`**: simplest. Keep it private; FansCut saves it with mode 0600.
2. **Environment variables**: `FANSCUT_SONARR_URL`, `FANSCUT_SONARR_API_KEY`,
   `FANSCUT_PLEX_URL`, `FANSCUT_PLEX_TOKEN`. A non-empty variable beats
   `config.yaml`, and is never written back to it, even when you save on the
   Settings page (those fields show "set by environment"). See `.env.example`.
3. **`*_FILE` variables**: the same names with a `_FILE` suffix
   (`FANSCUT_PLEX_TOKEN_FILE=/run/secrets/plex_token`) point at a file whose
   trimmed contents are the value. This is the Docker/Compose secrets style. If
   both `NAME` and `NAME_FILE` are set, `NAME` wins.

### Doppler

Any secret manager that can inject environment variables works; with
[Doppler](https://www.doppler.com/) create the four `FANSCUT_*` secrets above
in a project and config of your choosing, then:

```bash
doppler setup
doppler run -- ./dev.sh serve
doppler run -- docker compose up -d
```

`docker-compose.yml` passes the variables through to the container.

The settings API masks secrets (`***`), and FansCut never logs them or puts
them in error messages.

## How filler data works

For each episode FansCut picks the first answer it finds:

1. **Your override** (set in the episode table).
2. **A filler snapshot** in AniFiller's JSON format, loaded from every `*.json`
   in the filler directory (default `<data_dir>/fillers`).
3. **Jikan's episode flags** (filler / recap) for the matched MAL id, as a
   fallback when the snapshot has nothing for that show.
4. **`unknown`**. Whether unknown episodes are kept is a setting.

The labels come from the [AniFiller](https://github.com/AniraTeam/AniFiller)
project, and the data behind it comes from community-maintained filler lists
such as Anime Filler List. They did the actual work of watching and classifying
episodes; FansCut only reads the result. Thank you to them.

**Licensing caveat:** no filler dataset is bundled in this repo. The app
downloads `anifiller.min.json` from the AniFiller repository when you press
"Refresh filler data" (or run `fanscut fillers refresh`), and keeps a local
copy. I'm not a lawyer, so check the upstream terms yourself before you rely on
that data, redistribute it, or run this anywhere other than your own machine. If
you'd rather not fetch it, turn on `catalogues.offline`, or drop your own
AniFiller-format file into the filler directory.

AniFiller's numbers are absolute (episode 1 to N across the whole show). For
Sonarr libraries FansCut uses Sonarr's absolute episode number when it has one,
and otherwise counts regular-season episodes in order. If a show was matched per
season, it counts within the season. Specials (season 0) get no automatic label.

Labels are only as good as the source. If something is wrong, override it.

## How trimming works

For a season, FansCut decodes the first and last few minutes of each episode's
audio (mono, 8 kHz by default), turns it into a compact spectral fingerprint,
and compares episodes against a few of their neighbours. Audio that repeats
shows up as a long diagonal in a frame-by-frame similarity matrix:

- the stretch near the start that most episodes share is the **opening**;
- the stretch near the end they share is the **ending**;
- the head of episode N that repeats the tail of episode N-1 is a **recap**;
- the tail of episode N that repeats the head of episode N+1 is a **preview**.

Run it from the series page ("Detect OP/ED"). It's a background job. Results
show up as ranges on each episode, and you can nudge, retype, clear or reset
every one. A range you've edited is never overwritten by a later detection run.

Rough edges: it needs at least two episodes with files in the season; it finds
repeated audio, not "an opening" as such, so a cold open that repeats every
episode is also found; and shows whose OP changes mid-season may need hand
fixes. Detection is a starting point, not an oracle.

When cutting, each kept episode has its trimmed ranges removed, the pieces that
remain are padded a little (so a slightly early guess doesn't clip dialogue),
close pieces in the same file are merged, and anything shorter than the minimum
is dropped. By default the first kept episode keeps its opening.

Stream copy can only cut on keyframes, so cut points may land a moment off.
Use "re-encode" mode when you want frame-accurate cuts.

## Cut recipes

A recipe holds your choices for a series and no video data. Export from the
series page; import onto a series in another library and it's applied by
season + episode number (episodes it can't find are reported and skipped).

```json
{
  "schema": 1,
  "series": { "title": "Harbor Lights", "mal_id": 900001, "anilist_id": 800001 },
  "preset": "canon_only",
  "options": { "pad": 0.5, "merge_gap": 1.0, "min_segment": 5.0, "keep_intro_first_episode": true },
  "episodes": {
    "S01E02": { "label": "canon" },
    "S01E03": { "include": false },
    "S01E04": { "trims": { "intro": [62.0, 151.5], "outro": [1330.0, 1440.0], "recap": null } }
  }
}
```

`trims` ranges are `[start, end]` in seconds; `null` means "no such range here".
`label` is one of `canon | mixed | filler | unknown`.

## Limits

- Output has the first video and first audio track of each file. Extra audio
  tracks and subtitles are not carried over.
- Stream copy needs all files to match (codec, size, frame rate, audio format);
  otherwise FansCut re-encodes, which is slower. Cuts happen between
  episodes, so mixed sources mean a re-encode.
- No auth, by design. Don't expose it to the internet; put it behind your own
  reverse proxy if you need remote access.
- One worker, one job at a time.
- Plex support is lighter-tested than Sonarr and folder mode.
- Filler labels depend on a third-party dataset and on matching the right
  catalogue entry. Check the match, especially for shows with several seasons
  in one catalogue entry.
- The Docker image is written but I haven't built it on every platform.

## Security

FansCut has no login, so keep it on a trusted network. How to report a problem
is in [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT, see `LICENSE`.
