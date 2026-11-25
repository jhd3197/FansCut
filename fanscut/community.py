"""Community catalogue: an index of cut repos, install by repo URL, pull updates.

The index (index.json in the fanscut-community repo) only links to repos:

    {"schema": 1, "cuts": [{
        "name": "Took", "repo": "https://github.com/jhd3197/took",
        "kind": "merge", "category": "Fantasy", "tagline": "The Hobbit, as one film",
        "sources": [{"tmdb": 49051, "title": "An Unexpected Journey", "edition": "Extended"},
                    {"tmdb": 120, "title": "The Fellowship of the Ring", "optional": true}],
        "variants": ["Book cut", "Middle-earth cut"],
        "poster": "https://…/poster.jpg"}]}

It stores no media. A cut repo holds fanscut.json (a schema-2 timeline) at
its root; installing fetches it at a pinned commit and records the SHA so a
later update can tell whether anything changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit

import requests

from .errors import FansCutError
from .net import read_capped as _read_capped

REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)"
                     r"(?:\.git)?/?$")
MAX_INDEX = 2_000_000
MAX_RECIPE = 2_000_000
MAX_POSTER = 10_000_000
MAX_CUTS = 2_000
# Hosts that may see the GitHub token. Anything else (a cut's poster URL) gets no auth.
TOKEN_HOSTS = {"api.github.com", "raw.githubusercontent.com"}
KINDS = {"merge", "supercut", "order", "canon", "character", "chronological", "cut"}


class CommunityError(FansCutError):
    pass


@dataclass
class Repo:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.slug}"


def parse_repo(url: str) -> Repo:
    m = REPO_RE.match((url or "").strip())
    if not m:
        raise CommunityError("want a GitHub repo URL like https://github.com/owner/repo")
    return Repo(m.group(1), m.group(2))


def _text(v: Any, limit: int) -> str:
    if not isinstance(v, str) or any(ord(c) < 32 for c in v) or len(v) > limit:
        raise CommunityError("index: bad text field")
    return v.strip()


def _https(v: Any) -> str:
    url = _text(v, 500)
    if urlparse(url).scheme != "https":
        raise CommunityError("index: only https URLs")
    return url


def local_index_path(url: str) -> Path | None:
    """The file behind a local index_url ("C:\\x\\index.json", "/x/index.json" or
    "file:///x/index.json"); None for a web address."""
    if url.startswith("file://"):
        from urllib.parse import unquote, urlsplit
        from urllib.request import url2pathname

        return Path(url2pathname(unquote(urlsplit(url).path)))
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
        return None
    return Path(url).expanduser()


def validate_index(doc: Any) -> list[dict[str, Any]]:
    """Clean index entries; a bad entry is dropped, not fatal (one typo can't hide the rest)."""
    if not isinstance(doc, dict) or doc.get("schema") != 1 or not isinstance(doc.get("cuts"), list):
        raise CommunityError("index.json: want {\"schema\": 1, \"cuts\": [...]}")
    out = []
    for raw in doc["cuts"][:MAX_CUTS]:
        try:
            out.append(_entry(raw))
        except (CommunityError, TypeError, ValueError):
            continue
    return out


def _entry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CommunityError("entry")
    repo = parse_repo(raw.get("repo", ""))
    sources = []
    for s in raw.get("sources", [])[:200]:
        if not isinstance(s, dict):
            raise CommunityError("source")
        ids = {k: s[k] for k in ("tmdb", "tvdb") if isinstance(s.get(k), int)
               and not isinstance(s.get(k), bool) and s[k] > 0}
        if len(ids) != 1:
            raise CommunityError("source needs exactly one of tmdb / tvdb")
        clean = {**ids, "title": _text(s.get("title", ""), 200),
                 "optional": bool(s.get("optional", False))}
        if s.get("edition"):
            clean["edition"] = _text(s["edition"], 50)
        if isinstance(s.get("episodes"), int) and s["episodes"] > 0:
            clean["episodes"] = s["episodes"]
        sources.append(clean)
    kind = raw.get("kind", "cut")
    return {"name": _text(raw.get("name"), 120), "repo": repo.url, "slug": repo.slug,
            "kind": kind if kind in KINDS else "cut",
            "category": _text(raw.get("category", "Community cuts"), 40),
            "tagline": _text(raw.get("tagline", ""), 80),
            "sources": sources,
            "variants": [_text(v, 60) for v in raw.get("variants", [])[:20]],
            "poster": _https(raw["poster"]) if raw.get("poster") else "",
            "season": raw["season"] if raw.get("season") in ("christmas", "halloween") else None}


class GitHub:
    """Just enough of GitHub: default branch, branch head SHA, raw file at a SHA."""

    def __init__(self, token: str = "", session: requests.Session | None = None,
                 timeout: float = 20):
        self.s = session or requests.Session()
        self.token = token
        self.timeout = timeout

    def _get(self, url: str, accept: str = "application/vnd.github+json",
             limit: int = MAX_RECIPE) -> requests.Response:
        headers = {"Accept": accept, "User-Agent": "FansCut"}
        # The token only ever goes to GitHub itself: poster URLs come from third-party
        # repos and must not receive it.
        if self.token and urlsplit(url).hostname in TOKEN_HOSTS:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = self.s.get(url, headers=headers, timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise CommunityError(f"GitHub unreachable: {exc.__class__.__name__}") from exc
        with r:
            if r.status_code == 404:
                raise CommunityError("not found on GitHub (private repo? set a GitHub token "
                                     "in Settings)")
            if r.status_code >= 400:
                raise CommunityError(f"GitHub returned HTTP {r.status_code}")
            body = _read_capped(r, limit)  # stop reading at the cap, not after it
            if body is None:
                raise CommunityError("file too large")
            r._content = body
        return r

    def head(self, repo: Repo, ref: str | None = None) -> tuple[str, str]:
        """(branch, commit sha) of `ref`, or of the default branch."""
        api = f"https://api.github.com/repos/{repo.slug}"
        branch = ref or self._get(api).json().get("default_branch") or "main"
        sha = self._get(f"{api}/commits/{branch}").json().get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise CommunityError("GitHub sent no commit SHA")
        return branch, sha

    def raw(self, repo: Repo, sha: str, path: str) -> bytes:
        url = f"https://api.github.com/repos/{repo.slug}/contents/{path}?ref={sha}"
        return self._get(url, accept="application/vnd.github.raw").content

    def _send(self, method: str, url: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.token:
            raise CommunityError("pushing to GitHub needs a token (Settings, Community)")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "FansCut",
                   "Authorization": f"Bearer {self.token}"}
        try:
            r = self.s.request(method, url, json=body, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise CommunityError(f"GitHub unreachable: {exc.__class__.__name__}") from exc
        if r.status_code == 422:
            raise CommunityError("GitHub refused it (does that repo already exist?)")
        if r.status_code >= 400:
            raise CommunityError(f"GitHub returned HTTP {r.status_code}")
        data: dict[str, Any] = r.json() if r.content else {}
        return data

    def create_repo(self, name: str, private: bool = True, description: str = "") -> Repo:
        data = self._send("POST", "https://api.github.com/user/repos",
                          {"name": name, "private": private, "description": description[:300],
                           "auto_init": False})
        owner, _, repo = str(data.get("full_name", "")).partition("/")
        if not owner or not repo:
            raise CommunityError("GitHub did not return the new repo")
        return Repo(owner, repo)

    def put_file(self, repo: Repo, path: str, content: str, message: str) -> str:
        """Create a file; returns the commit SHA."""
        import base64

        data = self._send("PUT", f"https://api.github.com/repos/{repo.slug}/contents/{path}",
                          {"message": message,
                           "content": base64.b64encode(content.encode("utf-8")).decode()})
        return str((data.get("commit") or {}).get("sha", ""))

    def poster(self, url: str) -> tuple[bytes, str]:
        r = self._get(url, accept="image/*", limit=MAX_POSTER)
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip()
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(ctype)
        if not ext:
            raise CommunityError(f"poster is not an image ({ctype or 'no type'})")
        return r.content, ext


def availability(entry: dict[str, Any], tmdb_owned: set[int], tvdb_owned: set[int]
                 ) -> tuple[str, list[dict[str, str]]]:
    """ready | partly | missing against the library, with the missing sources named."""
    missing = []
    for s in entry["sources"]:
        have = (s.get("tmdb") in tmdb_owned) or (s.get("tvdb") in tvdb_owned)
        if not have:
            what = s.get("title") or (f"TMDB {s['tmdb']}" if "tmdb" in s else f"TVDB {s['tvdb']}")
            missing.append({"src": what, "reason": "optional — enables a variant"
                            if s["optional"] else "not in your library"})
    required = [s for s in entry["sources"] if not s["optional"]]
    gone = [m for m in missing if not m["reason"].startswith("optional")]
    if not gone:
        status = "ready"
    elif len(gone) >= len(required):
        status = "missing"
    else:
        status = "partly"
    return status, missing


def save_poster(data: bytes, ext: str, folder: Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}{ext}"
    path.write_bytes(data)
    return str(path)


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def change_summary(old: dict[str, Any], new: dict[str, Any]) -> str:
    """What an update changes, in a few words: "2 episodes re-timed, 1 scene added".

    Segments are compared by position (part, index); a segment with the same source
    whose times moved counts its source as re-timed."""
    def segs(doc: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
        return {(pi, si): s for pi, p in enumerate(doc.get("parts", []))
                for si, s in enumerate(p.get("segments", []))}

    a, b = segs(old), segs(new)
    retimed: set[str] = set()
    for k in a.keys() & b.keys():
        x, y = a[k], b[k]
        if x.get("src") == y.get("src") and (
                abs(x.get("start", 0) - y.get("start", 0)) >= 0.01
                or abs(x.get("end", 0) - y.get("end", 0)) >= 0.01):
            retimed.add(str(y.get("src")))
    swapped = sum(1 for k in a.keys() & b.keys() if a[k].get("src") != b[k].get("src"))
    added, removed = len(b.keys() - a.keys()), len(a.keys() - b.keys())
    out = []
    if retimed:
        movies = all((new.get("sources", {}).get(s) or {}).get("kind") == "movie"
                     for s in retimed)
        out.append(f"{_plural(len(retimed), 'film' if movies else 'episode')} re-timed")
    if added:
        out.append(f"{_plural(added, 'scene')} added")
    if removed:
        out.append(f"{_plural(removed, 'scene')} removed")
    if swapped:
        out.append(f"{_plural(swapped, 'scene')} replaced")
    dp = len(new.get("parts", [])) - len(old.get("parts", []))
    if dp:
        out.append(f"{_plural(abs(dp), 'part')} {'added' if dp > 0 else 'removed'}")
    had = {v.get("id") for v in old.get("variants", [])}
    nv = {v.get("id") for v in new.get("variants", [])} - had
    if nv:
        out.append(_plural(len(nv), "new variant"))
    if new.get("title") != old.get("title"):
        out.append(f"renamed to “{new.get('title')}”")
    return ", ".join(out) or "details changed (notes, credits or anchors)"
