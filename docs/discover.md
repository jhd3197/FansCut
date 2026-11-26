# Discover and community cuts

## Discover

Discover (the home page) lists what your library is one click away from:

- community timeline cuts dropped into `<data_dir>/recipes`;
- a merged film and a watch-order playlist for any Plex collection you own two
  or more films of;
- a canon cut for every matched show with known filler.

Each one is *ready*, *partly ready* or *not available*, with the missing
sources (or a missing audio language) named. Install queues sync + render;
watch orders become a Plex playlist with nothing rendered. Renders get a poster
from your own footage and join a "FansCut" collection in Plex.

## Templates

"New cut from template" (trilogy merge, watch order, chronological, canon)
writes `README.md`, `fanscut.json` and `outline.md` for a new cut repo and can
push it to a private GitHub repo (`community.github_token` or
`FANSCUT_GITHUB_TOKEN`).

## Community cuts

Discover reads the community index (`community.index_url`, an `index.json`
that only links to repos) and rates each cut ready / partly ready / not
available against your library, naming what's missing. Cuts install by repo
URL at a pinned commit SHA; My cuts checks for and pulls updates (the
`cut_updates` schedule does the check daily). A cut's poster is downloaded
once and used on render.
