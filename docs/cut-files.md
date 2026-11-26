# Cut files

A cut file holds your choices for a series and no video data. Export from the
series page; import onto a series in another library and it's applied by
season + episode number (episodes it can't find are reported and skipped).

```json
{
  "schema": 2,
  "series": { "title": "Harbor Lights", "mal_id": 900001, "anilist_id": 800001 },
  "preset": "canon_only",
  "options": { "pad": 0.5, "merge_gap": 1.0, "min_segment": 5.0, "keep_intro_first_episode": true },
  "episodes": {
    "S01E02": { "label": "canon" },
    "S01E03": { "include": false },
    "S01E04": { "segments": [[620.0, 845.0], [1100.0, 1180.0]],
                "trims": { "intro": [62.0, 151.5], "outro": [1330.0, 1440.0], "recap": null } }
  }
}
```

- `trims` ranges are `[start, end]` in seconds; `null` means "no such range
  here".
- `label` is one of `canon | mixed | filler | unknown`.
- `segments` (schema 2) are the kept parts of the episode, used exactly as
  given: the "my own cut" mode. They beat the preset and the trims.
- Schema 1 files (no `segments`) still import.

To write a cut file from published cut notes instead of by hand, see
[AI cuts from notes](ai-cuts.md). To reorder a show rather than cut it, see
[Timeline cuts](timelines.md).
