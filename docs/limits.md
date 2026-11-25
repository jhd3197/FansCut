# Limits

- Output has the first video and first audio track of each file
  (`render.language` picks a preferred audio language). Extra audio tracks and
  subtitles are not carried over. Timeline renders are the exception for text
  subtitles, which follow the cut as Plex sidecars (see
  [Timeline recipes](timelines.md#edit-operations)).
- Stream copy needs all files to match (codec, size, frame rate, audio format);
  otherwise FansCut re-encodes, which is slower. Cuts happen between
  episodes, so mixed sources mean a re-encode.
- No accounts. Set `server.auth: api_key` to make every API call (and the UI)
  ask for the API key; `server.viewer_key` adds a read-only key. Don't expose
  it to the internet without a reverse proxy in front. See
  [Security](security.md).
- One worker, one job at a time.
- Plex support is lighter-tested than Sonarr and folder mode.
- Filler labels depend on a third-party dataset and on matching the right
  catalogue entry. Check the match, especially for shows with several seasons
  in one catalogue entry.
