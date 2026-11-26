# OP/ED and recap trimming

## How detection works

For a season, FansCut decodes the first and last few minutes of each episode's
audio (mono, 8 kHz by default), turns it into a compact spectral fingerprint,
and compares episodes against a few of their neighbours. Audio that repeats
shows up as a long diagonal in a frame-by-frame similarity matrix:

- the stretch near the start that most episodes share is the **opening**;
- the stretch near the end they share is the **ending**;
- the head of episode N that repeats the tail of episode N-1 is a **recap**;
- the tail of episode N that repeats the head of episode N+1 is a **preview**.

The `fingerprint` section of the [configuration](configuration.md#fingerprint)
sets the sample rate, how much of each end is analysed, the segment length
limits and the match threshold.

## Running it

Run it from the series page ("Detect OP/ED"). It's a background job. Results
show up as ranges on each episode, and you can nudge, retype, clear or reset
every one. A range you've edited is never overwritten by a later detection run.

Rough edges: it needs at least two episodes with files in the season; it finds
repeated audio, not "an opening" as such, so a cold open that repeats every
episode is also found; and shows whose OP changes mid-season may need hand
fixes. Detection is a starting point, not an oracle.

## How the cut is built

When cutting, each kept episode has its trimmed ranges removed, the pieces that
remain are padded a little (so a slightly early guess doesn't clip dialogue),
close pieces in the same file are merged, and anything shorter than the minimum
is dropped. By default the first kept episode keeps its opening. The amounts
are `cut.pad`, `cut.merge_gap`, `cut.min_segment` and
`cut.keep_intro_first_episode`.

Stream copy can only cut on keyframes, so cut points may land a moment off.
Use "re-encode" mode when you want frame-accurate cuts.
