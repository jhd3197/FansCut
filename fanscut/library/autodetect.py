"""Find Plex, Radarr and Sonarr running on this machine, with their keys.

Radarr and Sonarr keep their port, URL base and API key in config.xml;
Plex keeps the signed-in token in its preferences (the registry on
Windows, Preferences.xml on Linux, a plist on macOS). Reading those means a
local setup needs no copying of keys by hand.

`fill` only touches empty settings, and only fills a key when the configured
URL is this machine's app (or no URL is set yet), so a local key is never
sent to some other server. Values are never logged.
"""

from __future__ import annotations

import os
import plistlib
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


@dataclass
class Found:
    service: str  # radarr | sonarr | plex
    url: str
    secret: str
    source: str  # where it was read from, for the UI (a path or "registry")


def _arr_dirs(app: str) -> list[Path]:
    home = Path.home()
    dirs = [home / ".config" / app, Path("/var/lib") / app.lower(),
            home / "Library/Application Support" / app]
    if sys.platform == "win32":
        dirs = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / app,
                Path(os.environ.get("LOCALAPPDATA", "")) / app,
                Path(os.environ.get("APPDATA", "")) / app, *dirs]
    return dirs


def _arr(app: str, default_port: int) -> Found | None:
    for d in _arr_dirs(app):
        f = d / "config.xml"
        if not f.is_file():
            continue
        try:
            root = ET.parse(f).getroot()
        except (ET.ParseError, OSError):
            continue
        key = (root.findtext("ApiKey") or "").strip()
        if not key:
            continue
        ssl = (root.findtext("EnableSsl") or "").strip().lower() == "true"
        port = (root.findtext("SslPort") if ssl else root.findtext("Port")) or str(default_port)
        base = (root.findtext("UrlBase") or "").strip().strip("/")
        scheme = "https" if ssl else "http"
        url = f"{scheme}://127.0.0.1:{port.strip()}" + (f"/{base}" if base else "")
        return Found(app.lower(), url, key, str(f))
    return None


def _plex_token() -> tuple[str, str, str] | None:
    """(token, port, source) from the local Plex Media Server's preferences."""
    if sys.platform == "win32":
        try:
            import winreg
        except ImportError:  # pragma: no cover
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Plex, Inc.\Plex Media Server") as k:
                token = str(winreg.QueryValueEx(k, "PlexOnlineToken")[0])
                try:
                    port = str(winreg.QueryValueEx(k, "ManualPortMappingPort")[0] or "")
                except OSError:
                    port = ""
        except OSError:
            return None
        return (token, port, "registry") if token else None
    if sys.platform == "darwin":
        f = Path.home() / "Library/Preferences/com.plexapp.plexmediaserver.plist"
        try:
            prefs = plistlib.loads(f.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            return None
        token = str(prefs.get("PlexOnlineToken", ""))
        return (token, "", str(f)) if token else None
    prefs = "Library/Application Support/Plex Media Server/Preferences.xml"
    for f in (Path("/var/lib/plexmediaserver") / prefs, Path.home() / prefs):
        try:
            token = ET.parse(f).getroot().get("PlexOnlineToken", "")
        except (ET.ParseError, OSError):
            continue
        if token:
            return token, "", str(f)
    return None


def detect() -> list[Found]:
    out = [f for f in (_arr("Radarr", 7878), _arr("Sonarr", 8989)) if f]
    plex = _plex_token()
    if plex:
        token, _port, source = plex
        # the manual port is the router mapping; the server itself listens on 32400
        out.append(Found("plex", "http://127.0.0.1:32400", token, source))
    return out


def _same_app(configured: str, found: str) -> bool:
    """True when no URL is set yet, or it is this machine's app on the found port."""
    if not configured:
        return True
    a, b = urlparse(configured), urlparse(found)
    return (a.hostname or "") in LOCAL_HOSTS and (a.port or 80) == (b.port or 80)


def fill(cfg, locked: list[str] | None = None, found: list[Found] | None = None) -> list[str]:
    """Fill empty Plex/Radarr/Sonarr settings from local apps; returns what was filled."""
    locked = locked or []
    filled = []
    for f in detect() if found is None else found:
        section = getattr(cfg.library, f.service)
        secret_field = "token" if f.service == "plex" else "api_key"
        if getattr(section, secret_field) or f"library.{f.service}.{secret_field}" in locked:
            continue
        if not _same_app(section.url, f.url):
            continue
        if not section.url and f"library.{f.service}.url" not in locked:
            section.url = f.url
        setattr(section, secret_field, f.secret)
        filled.append(f.service)
    return filled
