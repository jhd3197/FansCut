"""Outbound notifications when a job finishes: a plain JSON webhook, Discord or ntfy.

    notify:
      targets:
        - url: https://ntfy.sh/my-fanscut
          format: ntfy            # json | discord | ntfy
          events: [done, failed]

Each send runs on its own short-lived thread with a timeout, so a slow or dead
endpoint never holds up the job queue. Failures are logged, never raised.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import requests

log = logging.getLogger("fanscut.notify")
TIMEOUT = 10


def payload(job: dict[str, Any]) -> dict[str, Any]:
    """The fields every format is built from."""
    return {"event": f"job.{job['status']}", "job_id": job["id"], "kind": job["kind"],
            "title": job["title"], "status": job["status"], "message": job.get("message", ""),
            "series": (job.get("payload") or {}).get("series_key"),
            "seasons": (job.get("payload") or {}).get("seasons"),
            "output": (job.get("result") or {}).get("output")}


def build(fmt: str, data: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None,
                                                     dict[str, str]]:
    """(json body, text body, headers) for a target format."""
    ok = data["status"] == "done"
    line = f"{data['title']}: {'done' if ok else 'failed'}"
    if data["message"] and data["message"] != "done":
        line += f" ({data['message']})"
    if fmt == "discord":
        return {"username": "FansCut", "content": line}, None, {}
    if fmt == "ntfy":
        return None, line, {"Title": "FansCut", "Tags": "white_check_mark" if ok else "warning",
                            "Priority": "default" if ok else "high"}
    return data, None, {}


def send(target: Any, data: dict[str, Any], session: requests.Session | None = None) -> bool:
    body, text, headers = build(target.format, data)
    s = session or requests
    try:
        if text is not None:
            r = s.post(target.url, data=text.encode("utf-8"), headers=headers, timeout=TIMEOUT)
        else:
            r = s.post(target.url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        log.warning("notification to %s failed: %s", target.url.split("?")[0],
                    exc.__class__.__name__)
        return False
    if r.status_code >= 400:
        log.warning("notification to %s got HTTP %s", target.url.split("?")[0], r.status_code)
        return False
    return True


def job_finished(cfg: Any, job: dict[str, Any], background: bool = True) -> int:
    """Send to every target that wants this job's outcome; returns how many were tried."""
    status = job.get("status")
    if status not in ("done", "failed"):
        return 0
    targets = [t for t in cfg.targets if t.url and status in t.events]
    data = payload(job)
    for t in targets:
        if background:
            threading.Thread(target=send, args=(t, data), name="fanscut-notify",
                             daemon=True).start()
        else:
            send(t, data)
    return len(targets)
