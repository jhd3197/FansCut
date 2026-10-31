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
        print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}")
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
