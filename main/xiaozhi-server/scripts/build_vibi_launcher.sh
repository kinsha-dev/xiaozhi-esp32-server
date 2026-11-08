#!/bin/sh
# Rebuild ~/Applications/Vibi.app's launcher so it runs this checkout. PY defaults to the main checkout's venv.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${MAC_APP_PYTHON:-$HOME/esp/xiaozhi-server/main/xiaozhi-server/.venv/bin/python}"
APP="$HOME/Applications/Vibi.app"
[ -f "$APP/Contents/MacOS/Vibi.orig" ] || cp "$APP/Contents/MacOS/Vibi" "$APP/Contents/MacOS/Vibi.orig"
clang -O2 -DSERVER_DIR="\"$DIR\"" -DPY="\"$PY\"" -o "$APP/Contents/MacOS/Vibi" "$DIR/scripts/vibi_launcher.c"
codesign --force --sign - "$APP"
echo "Vibi.app now runs $DIR"
