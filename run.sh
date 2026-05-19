#!/usr/bin/env bash
# Launcher for macOS and Linux. Creates a local venv on first run, installs Pillow,
# then starts the GUI.
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 3 is required. Install it from https://www.python.org/downloads/ and try again."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    "$PY" -m venv .venv
    # shellcheck disable=SC1091
    . .venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
else
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

exec python ribbonengine.py "$@"
