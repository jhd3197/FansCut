# Automation

## Sonarr webhook

Set `automation.sonarr_webhook: true` and add a Webhook connection in Sonarr
pointing at:

```text
http://<fanscut>:6464/api/webhooks/sonarr?apikey=<key>
```

The key is generated on first start (`server.api_key`). Settings, Automation
shows the full URL and can make a new key. Instead of `?apikey=` on the URL,
the key can also go in an `X-Api-Key` header, or as the password in Sonarr's
Username/Password fields. Webhooks always need the key, even with
`server.auth: none`.

A Download or Upgrade event then queues OP/ED detection for the touched
seasons (when `automation.auto_detect` is on) and a render, with no button
pressing. `automation.keep` sets the keep flag for those renders.

## The render cache

It never re-renders the same cut twice: outputs are named after a hash of
everything that shapes them, and an unchanged cut is served from the cache.
Renders you don't mark "keep" are deleted after `render.ephemeral_days`, which
suits a watch-and-clean-up flow. Manage the files on the Jobs page or via
`/api/renders`.

## Scheduled tasks

Background tasks run on cron schedules from the `schedule` section of
`config.yaml` (standard 5-field cron, local time; `""` turns a task off). A
task never overlaps itself. Settings, Automation lists them under "Scheduled
tasks"; each can be run now, stopped or given a new cron there, or through
`/api/schedules`.

| Task | Default | What it does |
| --- | --- | --- |
| `prune` | `0 5 * * *` | trims the art cache, finished jobs older than 30 days and old catalogue replies |
| `sweep_renders` | `30 4 * * *` | deletes renders not marked keep, past `render.ephemeral_days` |
| `library_refresh` | `*/10 * * * *` | re-lists Sonarr, Radarr, Plex and folders in the background |
| `fillers_refresh` | `0 6 * * 1` | downloads the latest AniFiller data |
| `cut_updates` | `0 7 * * *` | looks for updates to installed community cuts |
| `library_check` | `0 3 * * 0` | checks the whole library for problems (the Health page) |

## Notifications

FansCut can tell something when a job is done or failed:

```yaml
notify:
  targets:
    - url: https://ntfy.sh/my-fanscut
      format: ntfy         # json | discord | ntfy
      events: [done, failed]
```

- `json` posts a JSON body with the event (`job.done` or `job.failed`), job
  id, kind, title, status, message, series, seasons and output path.
- `discord` posts a Discord webhook message (`username` FansCut, one line of
  text).
- `ntfy` posts one line of text with a title, a tag, and high priority for
  failures.

Each send runs on its own thread with a timeout, so a slow or dead endpoint
never holds up the job queue. Failures are logged, never raised.
