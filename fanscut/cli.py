"""fanscut command line.

    fanscut serve [--host H] [--port P] [--config FILE]
    fanscut doctor
    fanscut fillers refresh
    fanscut scan                  # list what the configured sources see
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import __version__, config, doctor
from .core import Core
from .db import Database
from .errors import FansCutError


def _serve(args: argparse.Namespace, cfg: config.Config) -> int:
    import uvicorn

    from .server import create_app

    uvicorn.run(create_app(cfg, args.config), host=args.host or cfg.server.host,
                port=args.port or cfg.server.port, log_level="info")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fanscut", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"fanscut {__version__}")
    ap.add_argument("--config", help="path to config.yaml (default: FANSCUT_CONFIG, ./config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
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
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.fn(args, config.load(args.config)))
    except FansCutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
