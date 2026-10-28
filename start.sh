#!/bin/bash
# Starts the Lead List Builder in your browser. The first run sets everything
# up, which takes a minute or two; after that it opens in seconds.
cd "$(dirname "$0")" || exit 1

# The one-line installer brings its own Python inside .venv; only a copy set
# up by hand needs one on the computer.
PY=""
[ -x .venv/bin/python ] && PY=.venv/bin/python
for c in python3.13 python3.12 python3.11 python3.10 python3 python3.9; do
  [ -n "$PY" ] && break
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python isn't installed yet. Opening the download page..."
  (open https://www.python.org/downloads/ || xdg-open https://www.python.org/downloads/) >/dev/null 2>&1
  read -r -p "Install it, then start the Lead List Builder again. Press Enter to close. "
  exit 1
fi

# Set up on the first run, and again whenever requirements.txt changes.
STAMP=".venv/.requirements"
if [ ! -x .venv/bin/python ] || ! cmp -s requirements.txt "$STAMP"; then
  echo "Setting things up (about a minute, only the first time)..."
  [ -x .venv/bin/python ] || "$PY" -m venv .venv || { read -r -p "Setup failed (see above). Press Enter to close. "; exit 1; }
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt \
    || { read -r -p "Setup failed (see above). Check your internet and try again. Press Enter to close. "; exit 1; }
  cp requirements.txt "$STAMP"
fi

export PYTHONUTF8=1
exec .venv/bin/python leads.py app
