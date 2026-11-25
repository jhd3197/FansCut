"""Small helpers for outbound HTTP shared by the clients."""

from __future__ import annotations

import requests

CHUNK = 64 * 1024


def read_capped(resp: requests.Response, limit: int) -> bytes | None:
    """The body of a streamed response, or None once it passes `limit` bytes.

    Stops reading at the cap, so a huge or endless reply costs at most `limit`
    bytes of memory and bandwidth.
    """
    declared = resp.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    buf = bytearray()
    for chunk in resp.iter_content(CHUNK):
        buf += chunk
        if len(buf) > limit:
            return None
    return bytes(buf)
