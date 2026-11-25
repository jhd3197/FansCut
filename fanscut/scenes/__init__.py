"""Scene tagging: an index of each film, tags on time ranges, cast and X-Ray.

* ``index``  — camera cuts (Prompture ``detect_shots``), timed subtitle lines
  and automatic scenes for one film. Media-free: times and text only.
* ``tags``   — labelled ranges ("christmas", "quidditch") with the people in
  them. Anyone can propose one (a person, the AI job, an MCP client); a
  person accepts or rejects it. Tags live apart from the index, so
  re-indexing a film never loses them.
* ``cast``   — the film's cast (Plex roles, or TMDB credits with a key).
* ``tagger`` — the AI pass: subtitles first, then contact sheets to check.
"""
