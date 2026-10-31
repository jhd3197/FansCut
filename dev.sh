#!/usr/bin/env bash
# FansCut local dev helper (Linux / macOS / Git Bash)
#
#   ./dev.sh setup          install Python deps (and UI deps if ui/ exists)
#   ./dev.sh serve          run the app (http://127.0.0.1:6464)
#   ./dev.sh test           pytest, in parallel
#   ./dev.sh lint           ruff + mypy
#   ./dev.sh ui             UI dev server with live reload (http://127.0.0.1:5464,
#                           proxies /api to a running `./dev.sh serve`)
#   ./dev.sh validate       everything CI runs: ruff, mypy, pytest, UI lint,
#                           UI tests and the UI build
#   ./dev.sh cli <args...>  pass through to the fanscut CLI
#
# Set FANSCUT_VENV to use a venv outside the repo.

set -euo pipefail
cd "$(dirname "$0")"

if [ -n "${FANSCUT_VENV:-}" ] && [ -x "$FANSCUT_VENV/bin/python" ]; then
    PYTHON="$FANSCUT_VENV/bin/python"
elif [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then
    PYTHON=.venv/Scripts/python.exe
else
    PYTHON=python
fi

step() {
    printf '\n==> %s\n' "$1"
    shift
    "$@"
}

ui() {
    if [ -f ui/package.json ]; then
        (cd ui && "$@")
    else
        printf '(no ui/ yet, skipping: %s)\n' "$*"
    fi
}

cmd="${1:-help}"
[ $# -gt 0 ] && shift

case "$cmd" in
    setup)
        step "pip install -e .[dev]" "$PYTHON" -m pip install -e ".[dev]"
        ui npm ci
        ;;
    serve)
        exec "$PYTHON" -m fanscut serve "$@"
        ;;
    test)
        exec "$PYTHON" -m pytest -q -n auto "$@"
        ;;
    lint)
        step "ruff" "$PYTHON" -m ruff check .
        step "mypy" "$PYTHON" -m mypy fanscut/
        ;;
    ui)
        [ -d ui/node_modules ] || ui npm ci
        ui npm run dev -- "$@"
        ;;
    validate)
        step "ruff" "$PYTHON" -m ruff check .
        step "mypy" "$PYTHON" -m mypy fanscut/
        step "pytest" "$PYTHON" -m pytest -q -n auto
        [ -d ui/node_modules ] || step "npm ci" ui npm ci
        step "ui lint" ui npm run lint
        step "ui test" ui npm test
        step "ui build" ui npm run build
        printf '\nall good\n'
        ;;
    cli)
        exec "$PYTHON" -m fanscut "$@"
        ;;
    *)
        sed -n '2,15p' "$0"
        ;;
esac
