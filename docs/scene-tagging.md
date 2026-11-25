# Scene tagging and X-Ray

Tag the scenes of your films ("christmas", "quidditch", "hagrid") and turn the
tagged scenes of a collection into a cut, such as every Christmas scene of the
Harry Potter films in film order. Tagging can be done by hand, by FansCut's
own AI job, or by an AI client such as Claude Code through the MCP server.
Every AI suggestion arrives as a proposal that you accept or reject.

## Index a film

Open a film and choose **Scenes and tags**, then **Index scenes**. Indexing:

- reads the subtitles: a sidecar `.srt` next to the file (`Film.en.srt`), or a
  text track inside it (SRT, ASS, mov_text). Image subtitles (PGS, VobSub)
  can't be read; for those films choose **transcribed** to transcribe the
  audio instead (needs a Prompture speech-to-text key, such as `GROQ_API_KEY`);
- finds every camera cut with the ffmpeg scene score (PySceneDetect when it
  is installed). This reads the whole file once: a few minutes for a 4K film.
  It runs in the heavy job lane, next to renders;
- groups shots into scenes: a scene ends at a cut nobody is talking across.

The index holds times and text only, never images or video, and indexing again
keeps your tags.

## The tagger

The scenes page shows a frame (or **Play range** for a 480p clip), a strip of
the whole film with lanes for scenes, tags and dialogue, a grid of frames
across the selected range, and its dialogue. Click the strip to pick a scene.

The side panel has:

- **Proposals**: what the AI suggested. Keys: `J`/`K` next and previous, `A`
  accept, `R` reject, `[` and `]` widen the start or end to the neighbouring
  camera cut, `Shift+[` and `Shift+]` narrow them, `Space` play the range.
- **Tags**: accepted and rejected tags.
- **Add**: tag the selected range yourself (your tags are accepted straight
  away), or run the AI on this film.
- **X-Ray**: who is on screen at the playhead, from the tags covering it,
  with photos from the cast, and the full cast list.

Tags are short slugs (`great-hall`); people are matched to the film's cast,
so "Harry" becomes Harry Potter (Daniel Radcliffe).

## The AI job

**Add → Find with AI** asks for a tag and what it means ("Christmas at
Hogwarts: snow, presents, the feast"). The job:

1. sends the dialogue, a few minutes at a time, to `ai.model`, which names the
   stretches where the tag applies (a whole film is about 25k tokens);
2. shows each stretch to `ai.vision_model` (or `ai.model`) as a labelled grid
   of frames, about one every 10-15 seconds (3x2 under a minute, 3x3 up to
   three minutes, 4x3 beyond), which confirms it, narrows it to the matching
   tiles and names who is on screen;
3. checks each edge on a second grid of frames 5 seconds apart around it, so
   the start and end land within a few seconds;
4. optionally (**Also look at scenes with no dialogue**) shows every
   near-silent scene too, one grid each, for scenes the dialogue can't reveal;
5. puts each edge on a camera cut nobody is talking across (an end may move up
   to 20 seconds later, a start 20 seconds earlier) so no line is cut off; when
   people talk straight through every cut, it stops in the first pause between
   lines instead. Then it leaves proposals. A stretch already tagged with the same tag is skipped, so
   running it again is safe.

Any [Prompture](https://github.com/jhd3197/prompture) model works:
`claude/...`, `google/...`, `openai/...`, `ollama/...`. A local vision model
such as `ollama/qwen3-vl:8b` is a good, free `vision_model`.

## Collections and cuts

**Tagged scenes** on a collection page lists every tagged scene of its films,
with a filter per tag. Pick a tag and **Make the cut**: the accepted scenes
become a [timeline](timelines.md) with one segment per scene, in film order,
ready to sync and render. Proposals don't count until you accept them.

## Cast

Films in Plex use the cast Plex already has. Other films use TMDB credits when
`catalogues.tmdb_api_key` (or `FANSCUT_TMDB_API_KEY`) is set; a v3 key or a v4
read token both work.

## MCP server: let Claude Code tag films

```bash
pip install "prompture[mcp]"
fanscut mcp                      # stdio; talks to http://127.0.0.1:6464
fanscut mcp --url http://nas:6464
```

Add it to Claude Code (or any MCP client), for example in `.mcp.json`:

```json
{"mcpServers": {"fanscut": {"command": "fanscut", "args": ["mcp"]}}}
```

The tools go through the running FansCut's API: `list_films`,
`film_overview`, `index_film`, `job_status`, `read_dialogue`, `look` (a
labelled frame grid as an image), `frame`, `camera_cuts`, `cast`,
`propose_scene`, `list_tags`, `update_scene`, `run_ai_tagging`,
`collection_tags` and `make_timeline`. A typical session: index the films of a
collection, read the dialogue in 10-20 minute chunks, `look` at the stretches
that matter, `propose_scene` for each hit, then accept them in the tagger and
`make_timeline`.

## API

Everything is under `/api/scenes` (see `/docs`): `films`, `film`, `index`,
`transcript`, `shots`, `sheet`, `frame`, `clip`, `cast`, `xray`, `tags`
(`GET`/`POST`, `PATCH`/`DELETE /tags/{id}`), `tag-counts`, `ai`,
`collection` and `to-timeline`. Film keys contain `:` and spaces, so they
travel as a `key` query parameter.

## Limits

- Face recognition is not done yet. "Who is on screen" comes from the
  dialogue and the vision model's reading of the frame grids, so it is only
  as good as the tags.
- HDR frames come out without tone mapping: flat colours, still readable.
- Clips for **Play range** are capped at 5 minutes and cached under
  `<data_dir>/scenes`.
