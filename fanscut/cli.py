"""fanscut command line.

    fanscut serve [--host H] [--port P] [--config FILE]
    fanscut doctor
    fanscut fillers refresh
    fanscut scan                  # list what the configured sources see
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, config, doctor, logs
from .core import Core
from .db import Database
from .errors import FansCutError


def _serve(args: argparse.Namespace, cfg: config.Config) -> int:
    import uvicorn

    from .server import create_app, ensure_api_key

    # first start: make the API key and save it, so webhooks keep working across restarts
    ensure_api_key(cfg, Path(args.config) if args.config else config.default_path())

    host, port = args.host or cfg.server.host, args.port or cfg.server.port
    # X-Forwarded-For/-Proto are believed only from these (a reverse proxy in front)
    proxies = ",".join(cfg.server.trusted_proxies) or "127.0.0.1"
    if args.reload:
        # Restart on every code change under fanscut/. The reloaded process
        # builds its own app from FANSCUT_CONFIG and inherits this environment
        # (secrets included), so nothing has to be passed by hand.
        os.environ["FANSCUT_CONFIG"] = str(Path(args.config or config.default_path()).resolve())
        uvicorn.run("fanscut.server:create_app", factory=True, reload=True,
                    reload_dirs=[str(Path(__file__).parent)], host=host, port=port,
                    log_level="info", timeout_graceful_shutdown=5,
                    proxy_headers=True, forwarded_allow_ips=proxies)
        return 0
    uvicorn.run(create_app(cfg, args.config), host=host, port=port, log_level="info",
                timeout_graceful_shutdown=5, proxy_headers=True, forwarded_allow_ips=proxies)
    return 0


def _doctor(_args: argparse.Namespace, cfg: config.Config) -> int:
    rep = doctor.run(cfg)
    for c in rep["checks"]:
        mark = "off " if c.get("off") else "ok  " if c["ok"] else "FAIL"
        print(f"{mark} {c['name']}: {c['detail']}")
    return 0 if rep["ok"] else 1


def _fillers(_args: argparse.Namespace, cfg: config.Config) -> int:
    res = Core(cfg, Database(cfg.db_path)).refresh_fillers()
    print(res["message"])
    return 0 if res["ok"] else 1


def _scan(_args: argparse.Namespace, cfg: config.Config) -> int:
    core = Core(cfg, Database(cfg.db_path))
    series, errors = core.library.list_series()
    for s in series:
        print(f"{s.key}\t{s.title}\t{s.episode_count} episodes")
    for src, msg in errors.items():
        print(f"{src}: {msg}", file=sys.stderr)
    print(json.dumps({"sources": core.library.sources()}), file=sys.stderr)
    return 1 if errors else 0


def _recipe_ai(args: argparse.Namespace, cfg: config.Config) -> int:
    """fanscut recipe ai --series KEY --notes notes.txt [--apply]"""
    from pathlib import Path

    core = Core(cfg, Database(cfg.db_path))
    notes = Path(args.notes).read_text(encoding="utf-8")
    out = core.ai_recipe(args.series, notes, apply=args.apply)
    print(json.dumps(out["recipe"], indent=2))
    if "import" in out:
        print(f"applied: {out['import']['applied']}, skipped: {out['import']['skipped']}",
              file=sys.stderr)
    return 0


def _recipe_validate(args: argparse.Namespace, _cfg: config.Config) -> int:
    """fanscut recipe validate --file recipe.json (either recipe kind, no library needed)"""
    from pathlib import Path

    from . import recipe as recipe_mod
    from .timeline import recipe as tl_recipe

    raw = Path(args.file).read_bytes()
    if len(raw) > tl_recipe.MAX_BYTES:
        raise FansCutError("recipe is over the 2 MB limit")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FansCutError(f"{args.file}: invalid JSON: {exc}") from exc
    if isinstance(doc, dict) and doc.get("kind") == "timeline":
        clean = tl_recipe.validate(doc)
        print(f"ok: timeline recipe “{clean['title']}” — {len(clean['parts'])} part(s), "
              f"{sum(len(p['segments']) for p in clean['parts'])} segment(s), "
              f"{len(clean['sources'])} source(s)")
    else:
        clean = recipe_mod.validate(doc)
        print(f"ok: series recipe “{clean['series'].get('title', '')}” — "
              f"{len(clean['episodes'])} episode(s) touched")
    return 0


def _recipe_dispatch(args: argparse.Namespace, cfg: config.Config) -> int:
    if args.action == "validate":
        if not args.file:
            raise FansCutError("recipe validate needs --file")
        return _recipe_validate(args, cfg)
    if not args.series or not args.notes:
        raise FansCutError("recipe ai needs --series and --notes")
    return _recipe_ai(args, cfg)


def _mcp(args: argparse.Namespace, cfg: config.Config) -> int:
    import os

    from . import mcp_server

    host = "127.0.0.1" if cfg.server.host in ("0.0.0.0", "::") else cfg.server.host  # noqa: S104
    url = args.url or os.environ.get("FANSCUT_URL") or f"http://{host}:{cfg.server.port}"
    mcp_server.serve(url)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fanscut", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"fanscut {__version__}")
    ap.add_argument("--config", help="path to config.yaml (default: FANSCUT_CONFIG, ./config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--reload", action="store_true",
                   help="restart when the code changes (local development)")
    s.set_defaults(fn=_serve)
    sub.add_parser("doctor", help="check ffmpeg and directories").set_defaults(fn=_doctor)
    f = sub.add_parser("fillers", help="filler data")
    f.add_argument("action", choices=["refresh"])
    f.set_defaults(fn=_fillers)
    sub.add_parser("scan", help="list series from the configured sources").set_defaults(fn=_scan)
    r = sub.add_parser("recipe", help="cut recipes")
    r.add_argument("action", choices=["ai", "validate"])
    r.add_argument("--series", help="series key, e.g. sonarr:12 (for ai)")
    r.add_argument("--notes", help="text file with community cut notes (for ai)")
    r.add_argument("--apply", action="store_true", help="import the result, not just print it")
    r.add_argument("--file", help="recipe JSON to check (for validate)")
    r.set_defaults(fn=_recipe_dispatch)
    m = sub.add_parser("mcp", help="MCP server (stdio) so an AI client can tag scenes")
    m.add_argument("--url",
                   help="FansCut to talk to (default: FANSCUT_URL or this config's server)")
    m.set_defaults(fn=_mcp)
    args = ap.parse_args(argv)
    logs.setup(None)
    try:
        cfg = config.load(args.config)
        if args.fn is _serve:  # the server also writes the rotating log file
            logs.setup(cfg.data_dir)
        return int(args.fn(args, cfg))
    except FansCutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
