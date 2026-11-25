# Timeline recipes

Beyond per-episode cuts, a **timeline recipe** rebuilds a show in any order:
any range of any episode, grouped into parts, rendered as a new show Plex can
serve (`<Series> (<Edit>)/Season 01/…`, with .nfo files and chapters). The
recipe is media-free JSON: source keys in Plex/TVDB numbering, times in
seconds, ordering, and **anchors**: lossy audio/video fingerprints of each
cut point (8 s of binarised audio features and/or 4 fps dHash frames; neither
can be turned back into content).

## Import, sync, render

1. Import a timeline on the series page. Missing sources are reported with the
   parts that need them.
2. Run **Sync**. Each anchor is matched against your own files, so BluRay vs
   streaming vs TV offsets, mid-episode ad cuts and even PAL speedups are
   corrected.
3. Review the sync report, with per-cut previews.
4. Render.

## The editor

The editor (Timelines, then Open) authors recipes from scratch: parts and
segments with drag-to-reorder, preview playback, an AI outline from pasted
chronology notes, and "Export + anchors" to share the result.
`fanscut recipe validate --file` checks a recipe offline; the companion
fanscut-recipes repo runs it in CI.

## Movies as sources

Timeline sources can also be **movies** (Radarr, Plex movie sections, or a
movies folder with one subfolder per movie): a source keyed `M<tmdb id>` with
`{"kind": "movie", "tmdb": 27205, "edition": "extended"}`. If a scene's
anchors don't exist in your edition (theatrical vs extended), sync marks the
segment "not in your edition" and the render skips it with a warning.

Segments accept `xfade_ms` for an audio crossfade that keeps sync by letting
the outgoing audio run past the video cut. A single-part timeline renders as
a Plex *movie* folder; multi-part renders as a show.

## Toggles and variants

Sources can be `"optional": true`. A **toggle** (`id`, `label`, `default`,
`needs`) switches segments whose `when` names it on (or off with `"!id"`); a
**variant** is a named set of toggle values. A toggle whose films aren't in
your library is disabled and says which film it needs, never an error. Each
variant shows its runtime, and its name goes into the output name.

## Edit operations

Segments take optional edit operations, all applied to your own footage:

- `speed` (0.5–2, pitch kept)
- `hold`
- `video`: `fade_in`, `fade_out`, `crop`, `mirror`, `grade`
- `transition`: `dissolve`, `dip`, `fade`
- `j_cut` / `l_cut`
- a music bridge (`audio.src` from another source)
- `title` cards (bundled Inter)

Every field has tight bounds; no raw ffmpeg filters or paths are accepted.
Plain parts keep the stream-copy path. HDR sources re-encode to HEVC 10-bit
with BT.2020, PQ/HLG and their mastering metadata, and text subtitle tracks
follow the cut as Plex sidecars (forced tracks stay forced). With
`render.effectcraft_cli` set, segment grades go through
[EffectCraft](https://github.com/storytold/effectcraft) instead.
