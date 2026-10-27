#!/bin/bash
# Lead List Builder, one-line install for Mac. Paste this into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/topkdev/lead-builder/main/install.sh | bash
#
# It puts everything in a "Lead Builder" folder in your home folder: the app,
# its own copy of Python (so you don't need to install one), and a "Lead
# Builder" icon on your Desktop. Running the same line again updates the
# app; your key and your lists are kept. To remove it, delete the folder and
# the icon.
set -euo pipefail

# Where the app is downloaded from. Override for testing.
ZIP_URL="${LEAD_BUILDER_ZIP:-https://github.com/topkdev/lead-builder/archive/refs/heads/main.zip}"
DEST="${LEAD_BUILDER_DIR:-$HOME/Lead Builder}"
DESKTOP="${LEAD_BUILDER_DESKTOP:-$HOME/Desktop}"

say() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nSomething went wrong: %s\nCheck your internet connection and paste the line again.\n' "$1"; exit 1; }

say "Getting the setup helper"
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  # uv (astral.sh) installs Python for the app without touching the rest of
  # the computer. It goes in ~/.local/bin; the shell setup is left alone.
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 \
    || fail "couldn't download the setup helper"
  UV="${UV_INSTALL_DIR:-$HOME/.local/bin}/uv"
fi
[ -x "$UV" ] || fail "the setup helper didn't install"

say "Downloading the Lead List Builder"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$ZIP_URL" -o "$TMP/app.zip" || fail "couldn't download the app"
unzip -q "$TMP/app.zip" -d "$TMP/x" || fail "the download was damaged"
SRC="$(dirname "$(find "$TMP/x" -maxdepth 3 -name leads.py | head -1)")"
[ -f "$SRC/leads.py" ] || fail "the download didn't contain the app"
mkdir -p "$DEST"
# The code is replaced; the person's key (.env), lists (jobs) and
# spreadsheets (exports) are not touched.
rsync -a --delete --exclude .env --exclude jobs --exclude exports --exclude .venv "$SRC/" "$DEST/"

say "Setting up (about a minute the first time)"
cd "$DEST"
[ -x .venv/bin/python ] || "$UV" venv --quiet --seed --python 3.12 .venv || fail "couldn't set up Python"
"$UV" pip install --quiet --python .venv/bin/python -r requirements.txt || fail "couldn't install the app's parts"
cp requirements.txt .venv/.requirements
chmod +x start.sh "Start Lead Builder.command"

# A launcher made on this computer opens without macOS's "unidentified
# developer" warning, unlike one downloaded inside a zip.
mkdir -p "$DESKTOP"
LAUNCHER="$DESKTOP/Lead Builder.command"
cat > "$LAUNCHER" <<EOF
#!/bin/bash
# Opens the Lead List Builder. Keep the window that appears open while you use it.
exec bash "$DEST/start.sh"
EOF
chmod +x "$LAUNCHER"

say "Done. Double-click \"Lead Builder\" on your Desktop to open it any time."
if [ -z "${LEAD_BUILDER_NO_START:-}" ]; then
  if command -v open >/dev/null 2>&1; then open "$LAUNCHER"; else exec bash "$DEST/start.sh"; fi
fi
