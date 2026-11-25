"""Small helpers for outbound HTTP shared by the clients."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

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


def is_local(url: str) -> bool:
    """True for loopback, private (LAN) and link-local hosts, and bare names like 'sonarr'."""
    host = urlsplit(url).hostname or ""
    if not host:
        return False
    if host == "localhost" or "." not in host:  # docker service names, NAS hostnames
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # no DNS lookup here: it would cost a round trip per client
        return host.endswith((".local", ".lan", ".home", ".internal", ".localdomain"))
    return ip.is_private or ip.is_loopback or ip.is_link_local


def apply_proxy(session: requests.Session, network: Any, url: str = "") -> None:
    """Point a session at network.proxy, unless the target is local and bypassed."""
    if network.proxy_bypass_local and url and is_local(url):
        session.trust_env = False  # no HTTP(S)_PROXY from the environment either
        session.proxies = {}
        return
    if network.proxy:
        session.proxies = {"http": network.proxy, "https": network.proxy}
