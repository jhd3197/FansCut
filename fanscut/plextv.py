"""Sign in with Plex: the plex.tv PIN flow, so Docker and remote setups need no
token copied out of Preferences.xml.

1. start() asks plex.tv for a PIN and returns the URL the user opens to approve it.
2. check(pin_id) polls; once approved it returns the account's token.
3. servers(token) lists the Plex servers that account can reach, with their URLs.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from . import __version__
from .errors import ClientError

API = "https://plex.tv/api/v2"
PRODUCT = "FansCut"


def client_id(data_dir: Path) -> str:
    """A stable id for this install; plex.tv lists it as a device."""
    f = Path(data_dir) / "plex-client-id"
    try:
        cid = f.read_text(encoding="utf-8").strip()
        if cid:
            return cid
    except OSError:
        pass
    cid = uuid.uuid4().hex
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(cid, encoding="utf-8")
    except OSError:
        pass
    return cid


def _headers(cid: str, token: str = "") -> dict[str, str]:
    h = {"Accept": "application/json", "X-Plex-Product": PRODUCT,
         "X-Plex-Version": __version__, "X-Plex-Client-Identifier": cid}
    if token:
        h["X-Plex-Token"] = token
    return h


def _call(method: str, url: str, headers: dict[str, str],
          session: requests.Session | None = None, **kw: Any) -> Any:
    s = session or requests.Session()
    try:
        r = s.request(method, url, headers=headers, timeout=15, **kw)
    except requests.RequestException as exc:
        raise ClientError(f"plex.tv unreachable: {exc.__class__.__name__}") from exc
    if r.status_code == 404:
        raise ClientError("that sign-in has expired; start again", 404)
    if r.status_code >= 400:
        raise ClientError(f"plex.tv returned HTTP {r.status_code}", r.status_code)
    try:
        return r.json()
    except ValueError as exc:
        raise ClientError("plex.tv sent a non-JSON reply") from exc


def start(cid: str, session: requests.Session | None = None) -> dict[str, Any]:
    pin = _call("POST", f"{API}/pins", _headers(cid), session, params={"strong": "true"})
    q = urlencode({"clientID": cid, "code": pin["code"],
                   "context[device][product]": PRODUCT})
    return {"id": pin["id"], "code": pin["code"], "auth_url": f"https://app.plex.tv/auth#?{q}"}


def check(cid: str, pin_id: int, session: requests.Session | None = None) -> str | None:
    """The token once the user approved the PIN, else None."""
    pin = _call("GET", f"{API}/pins/{int(pin_id)}", _headers(cid), session)
    return pin.get("authToken") or None


def servers(cid: str, token: str, session: requests.Session | None = None
            ) -> list[dict[str, Any]]:
    """Plex Media Servers on the account: name and URLs, local ones first."""
    out = []
    for r in _call("GET", f"{API}/resources", _headers(cid, token), session,
                   params={"includeHttps": 1, "includeRelay": 0}) or []:
        if "server" not in str(r.get("provides", "")):
            continue
        conns = sorted(r.get("connections") or [], key=lambda c: (not c.get("local"),
                                                                  c.get("relay", False)))
        out.append({"name": r.get("name", ""), "owned": bool(r.get("owned")),
                    "urls": [c["uri"] for c in conns if c.get("uri")]})
    return out
