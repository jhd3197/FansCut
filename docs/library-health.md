# Library health

`GET /api/library/check` (the Health page) runs before suggestions and
renders. It looks for:

- the edition, from the runtime;
- audio and forced-subtitle languages;
- unfinished files: temp names, short runtimes, decode errors at the end;
- missing episodes and extras;
- codec, frame size, black bars and HDR mismatches within a cut;
- Plex-style renames, applied only after you confirm each one.

Every finding names the file and the fix.

The `library_check` schedule runs the check over the whole library once a week
(see [Automation](automation.md#scheduled-tasks)). The endpoint is rate
limited.
