# AI recipes

Fan editors often publish their cut lists and reasoning but not the video.
With `ai.base_url` and `ai.model` set (any OpenAI-compatible endpoint; Ollama
works fine, no key needed), paste those notes into "AI recipe…" on the series
page, or run:

```bash
fanscut recipe ai --series KEY --notes notes.txt
```

The LLM turns them into a schema-2 [cut recipe](cut-recipes.md); it's validated
before anything is applied, and every range stays editable afterwards.

If your endpoint needs a key, prefer the `FANSCUT_AI_API_KEY` environment
variable over `ai.api_key` in the file (see
[Secrets](configuration.md#secrets)).

The timeline editor uses the same endpoint for an outline from pasted
chronology notes (see [Timeline recipes](timelines.md)).
