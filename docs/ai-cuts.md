# AI cuts from notes

Fan editors often publish their cut lists and reasoning but not the video.
With `ai.model` set (a [Prompture](https://github.com/jhd3197/prompture) model
such as `claude/claude-opus-5-5` or `ollama/qwen3:8b`, or a bare name plus
`ai.base_url` for any OpenAI-compatible endpoint), paste those notes into "AI cut…" on the series
page, or run:

```bash
fanscut recipe ai --series KEY --notes notes.txt
```

The LLM turns them into a schema-2 [cut file](cut-files.md); it's validated
before anything is applied, and every range stays editable afterwards.

If your endpoint needs a key, prefer the `FANSCUT_AI_API_KEY` environment
variable over `ai.api_key` in the file (see
[Secrets](configuration.md#secrets)).

The timeline editor uses the same endpoint for an outline from pasted
chronology notes (see [Timeline cuts](timelines.md)).
