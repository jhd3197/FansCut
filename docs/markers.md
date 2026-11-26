# Markers: shared scene tags (spec, draft 2)

Status: proposal, nothing built yet. Tag list: [`tags-v1.json`](tags-v1.json).

## The idea

Analysing a film once should be enough for everyone who owns it. When someone
tags *Prisoner of Azkaban*, the result is a **marker file**: time ranges and
tags, plus fingerprints that line the ranges up with anyone's copy. People
share marker files the way they share cuts. FansCut never ships or links to a
film, a frame, a subtitle line, a summary or a face.

With enough marker files the community index becomes a knowledge base: "every
Christmas scene", "every snowy scene", "the happy moments" work instantly on a
library nobody has analysed, and the AI can build and stretch cuts from them.

## Words

- **Content tags**: what is in a stretch (`christmas`, `snow`, `kiss`). A
  fixed list of 128 in version 1.
- **Cut tags**: FansCut's own, about how a stretch can be used in a cut
  (`fc:filler`, `fc:ambient`). Filled by a second pass.
- **Moment**: a range of one film with one or more tags.
- **Sound map**: which sound leads each part of a moment (dialogue, ambient,
  music, silence).
- **Marker file**: every moment of one film, plus anchors.
- **Set**: a named theme defined by rules over tags (Christmas = `christmas`).
- **Knowledge base (KB)**: the marker files in `fanscut-community`.

## 1. Tag ids

A tag's id is its name: `christmas`, `snow`, `fc:filler`. There are no
numbers on top. If a widely used shared list appears later (TMDB keywords are
the closest today), content tags can map to it without renumbering anything.

- Content tags have plain ids. FansCut's cut tags always carry `fc:`, so the
  two never collide.
- An id never changes meaning and is never reused. A tag can be retired
  (`"retired": true`), never deleted or renamed.
- New tags only arrive in a new list version. A marker file names the version
  it was made with.
- Tags are for things in the scene, not for the franchise: no `hogwarts` or
  `quidditch`. Franchise words stay local (section 8).

## 2. Content tags (first pass)

| Family | Count | Tags |
|---|---|---|
| occasion | 14 | christmas, halloween, thanksgiving, new-year, easter, valentines-day, birthday, wedding, funeral, graduation, school-dance, party, festival, summer-holiday |
| weather | 10 | snow, rain, thunderstorm, fog, sunrise, sunset, night, full-moon, heat, wind |
| place | 24 | castle, school, hospital, prison, church, courtroom, office, bar, restaurant, kitchen, bedroom, forest, beach, ocean, desert, mountains, city-streets, village, train, airplane, spaceship, underwater, cave, graveyard |
| action | 22 | fight, sword-fight, gunfight, magic-duel, chase, car-chase, battle, explosion, escape, heist, rescue, sports-match, race, flying, swimming, training, dancing, singing, sneaking, transformation, kidnapping, interrogation |
| beat | 20 | arrival, goodbye, reunion, first-meeting, confession, proposal, kiss, breakup, death, sacrifice, betrayal, plan, flashback, dream, montage, speech, revelation, celebration, meal, journey |
| people | 10 | family, friendship, romance, rivalry, mentor, siblings, parent-child, children, crowd, alone |
| mood | 16 | happy, funny, heartwarming, romantic, sad, tense, scary, creepy, epic, triumphant, peaceful, nostalgic, angry, awkward, mysterious, bittersweet |
| creature | 8 | animal, dog, cat, horse, monster, ghost, robot, magic |
| content | 4 | gore, nudity, drugs, strong-language |

- `mood` tags are `subjective`: they need more agreement before they enter
  the KB (section 6).
- `content` tags exist so a set or cut can leave scenes out (a family
  version), not to find them.

## 3. Cut tags and the sound map (second pass)

After the content pass, a second pass marks how each stretch can be used in a
cut. These are **smart tags**: most are measured, not guessed. The `fc:`
prefix exists only in files; the app shows plain names ("No face").

**Measured (no AI, run after indexing a film):**

| Tag | Found by |
|---|---|
| No face | face detector finds nobody (section 4) |
| Close-up | face detector: one large face |
| Group | face detector: 3+ faces |
| Dialogue | subtitle lines plus a speech detector |
| Silence | loudness |
| Music only | sound classifier says music, speech detector says nobody talks |
| Ambient sound | sound classifier: not speech, not music (wind, rain, crowd, fire) |
| Loud | loudness peaks |
| Fast cutting | shots per minute from the shot index |
| Long take | one shot of 20 s or more |
| Still | frame difference between samples |
| Dark | brightness |
| Fade | black frames at a shot edge |
| Recap | audio fingerprint matches earlier in the film or series |
| Theme music | audio fingerprint of a clip someone tagged once |
| Titles or credits | position in the film plus text on screen |

**Checked by AI:** Establishing shot, Transition, Montage, Extendable. Each
starts from measured tags (no face + still, fast cutting + music only) and the
vision model confirms it on a contact sheet.

**Needs a person:** Filler, Spoiler. The text model can propose filler from
the dialogue, but whether a scene matters to the story is a person's call.

How the sound tags work: a small speech detector (Silero VAD, CPU) says when
someone talks; a small sound classifier (YAMNet or PANNs, CPU, labels such as
Music, Speech, Wind, Rain, Crowd) labels the rest. A specific theme ("Hedwig's
Theme") is not something a classifier knows: someone tags it once, and
FansCut's existing audio fingerprint search ("find similar", today used for
flashbacks) finds every other place it plays, in every film of the series.

Nothing here runs until the tagger branch lands; the list is a design, not
data.

It also writes the **sound map**: inside a moment, which sound leads each
part, so a cut can keep a dialogue line whole or borrow a stretch of ambience.

```json
"sound": [["ambient", 3524.6, 3541.0], ["dialogue", 3541.0, 3566.2], ["music", 3566.2, 3598.9]]
```

The pass uses what the index already has: shot cuts, subtitle timing (gaps
between lines mean no dialogue), audio loudness and the face count from
section 4. Only the model call that names `fc:filler` and `fc:extendable`
costs tokens.

### Extending a cut

Cut tags let FansCut stretch a cut toward a target length (say a 26 minute
"Christmas at Hogwarts" to an hour) without inventing story:

1. Widen each moment into neighbouring stretches with the same content tags
   (more of the same snowy courtyard).
2. Add `fc:establishing` and `fc:no-face` stretches from the same films with
   the set's tags (wide shots of the snowy castle) between scenes.
3. Lay `fc:ambient` sound under the joins, so the snow keeps falling across a
   cut instead of going silent.
4. Only if the user asks: add `fc:filler` scenes from the same franchise.

Each added piece is marked in the editor and can be removed. Extending never
crosses franchises.

## 4. Faces

Who is on screen comes from faces, not only from dialogue:

1. **Detect**: YuNet finds every face in the frames FansCut already samples
   (a few per shot).
2. **Recognise**: SFace compares each face with the cast photos Plex keeps for
   each actor. The cast of the moment (from dialogue and the vision check) is
   tried first with a looser threshold; anyone else needs a stricter one.
3. **Track**: faces with matching features across neighbouring frames of one
   shot become one track, which gives real screen time per person.
4. **Confirm**: low-scoring names are shown as "Harry Potter?" for a person to
   confirm or rename once; the answer applies to the whole film.

Measured on the 44 stills of "Christmas at Hogwarts": 66 faces found, 25
named. Names at 0.40 or higher were right in every case checked by eye; below
that, roughly one in three was wrong, which is why those stay unconfirmed.

Face boxes and features stay on the server. Marker files carry only TMDB
person ids per moment (section 5), never boxes, crops or features.

## 5. The marker file

One file per film and edition, JSON, at most 1 MB.

```json
{
  "schema": 1,
  "kind": "fanscut-markers",
  "tags_version": 1,
  "film": {"tmdb": 673, "title": "Harry Potter and the Prisoner of Azkaban",
           "year": 2004, "edition": null, "duration": 8502.2},
  "version": 3,
  "updated": "2026-11-25",
  "authors": ["jhd3197"],
  "moments": [
    {"start": 3524.55, "end": 3598.93,
     "tags": ["christmas", "snow", "castle", "fc:no-face", "fc:ambient"],
     "sound": [["ambient", 3524.6, 3541.0], ["dialogue", 3541.0, 3566.2]],
     "people": [10980, 10990],
     "source": "ai", "model": "claude-sonnet-5-5", "confidence": 0.78,
     "reviewed": true,
     "anchors": {"start": "<fingerprint>", "end": "<fingerprint>"}}
  ]
}
```

- `start` and `end` are seconds in the author's copy. `anchors` are the same
  lossy fingerprints cut recipes use, so sync maps each moment onto the
  reader's copy (BluRay vs streaming, PAL speed-up, extended vs theatrical). A
  moment whose anchors aren't found is "not in your edition".
- `tags`: content and cut tag ids, at most 12 per moment.
- `sound`: optional sound map, ranges inside the moment.
- `people`: TMDB person ids.
- `source` is `ai` or `human`; `reviewed: true` means a person accepted it.
- A moment is at least 3 seconds and at most 15 minutes. Moments may overlap.

Never in a marker file: video, frames, audio, subtitle text, AI summaries,
faces or face features, file paths, file names or user names (only the
authors' public handles). `fanscut markers validate --file` checks a file
offline.

## 6. Versions, updates and the KB

- `version` goes up each time the author publishes. FansCut stores the
  version it installed, and the cut update check grows a markers branch:
  "Prisoner of Azkaban: markers v3, 14 new moments. Update?"
- Your own edits are an overlay kept across updates. Rejecting a community
  moment hides it locally.
- Two files for the same film merge: same tag and at least 50% overlap is the
  same moment. Reviewed beats unreviewed, human beats AI.

Files live in `fanscut-community/markers/<tmdb>[-<edition>].json`, listed in
`index.json` with version, moment count and tag counts. CI checks every pull
request: schema, known tag ids for its `tags_version`, moments inside the
film, both anchors present, no field longer than 200 characters, `mood` tags
reviewed or at least 0.8 confidence, and a higher version than the index.

## 7. Sets and exports

A set is saved rules over tags, plus optional film rules:

```json
{"id": "christmas", "name": "Christmas",
 "scene_tags": {"any": ["christmas"], "none": ["gore"]},
 "film_rules": [{"tmdb_keyword": "christmas"}, {"plex_genre": "Holiday"}]}
```

A set exports as a cut (one per franchise, optionally extended as in
section 3), a Plex collection of its films, or a marker file with only its
moments.

## 8. What stays local

- Your own tags (`great-hall`) are saved as `custom:great-hall` and never
  exported.
- AI summaries, dialogue snippets, frames, face boxes and face features stay
  in your database.
- Existing tags migrate by name: `christmas` stays `christmas`.

## 9. Build order

1. Ship `tags-v1.json`; tagger UI picks from the list.
2. Multi-label content pass (one read of the film for all tags).
3. Faces: YuNet + SFace against Plex cast photos, tracks, confirm UI.
4. Second pass: cut tags and the sound map.
5. `fanscut markers export/import/validate`, moment anchors, sync.
6. KB in `fanscut-community`: schema, CI, index, update check.
7. Sets with rules and exports; extending cuts.

## Open decisions

- Who curates the tag list after v1, and whether content tags map to TMDB
  keywords.
- Whether AI-only, unreviewed moments may enter the KB.
- Whether `people` is shared (it makes X-Ray work for everyone).
- How far extending may go by default (the plan above stops before filler).
