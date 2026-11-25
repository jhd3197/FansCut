# Filler data

Every episode gets one label: `canon`, `mixed`, `filler` or `unknown`. Presets
(canon only, canon + mixed, everything, custom) decide which labels are kept.

## Where a label comes from

For each episode FansCut picks the first answer it finds:

1. **Your override** (set in the episode table).
2. **A filler snapshot** in AniFiller's JSON format, loaded from every `*.json`
   in the filler directory (default `<data_dir>/fillers`).
3. **Jikan's episode flags** (filler / recap) for the matched MAL id, as a
   fallback when the snapshot has nothing for that show.
4. **`unknown`**. Whether unknown episodes are kept is a setting
   (`cut.unknown_policy`).

The labels come from the [AniFiller](https://github.com/AniraTeam/AniFiller)
project, and the data behind it comes from community-maintained filler lists
such as Anime Filler List. They did the actual work of watching and classifying
episodes; FansCut only reads the result. Thank you to them.

## Licensing caveat

No filler dataset is bundled in this repo. The app downloads
`anifiller.min.json` from the AniFiller repository when you press "Refresh
filler data" (or run `fanscut fillers refresh`), and keeps a local copy. The
`fillers_refresh` schedule does the same weekly. I'm not a lawyer, so check the
upstream terms yourself before you rely on that data, redistribute it, or run
this anywhere other than your own machine. If you'd rather not fetch it, turn
on `catalogues.offline`, or drop your own AniFiller-format file into the filler
directory.

## Episode numbering

AniFiller's numbers are absolute (episode 1 to N across the whole show). For
Sonarr libraries FansCut uses Sonarr's absolute episode number when it has one,
and otherwise counts regular-season episodes in order. If a show was matched per
season, it counts within the season. Specials (season 0) get no automatic label.

Labels are only as good as the source, and depend on matching the right
catalogue entry. Check the match, especially for shows with several seasons in
one catalogue entry. If something is wrong, override it.
