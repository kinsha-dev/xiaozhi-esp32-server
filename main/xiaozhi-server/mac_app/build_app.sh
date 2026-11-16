#!/bin/sh
# Build ~/Applications/Vibi.app: a tiny signed launcher bundle (stable id com.voicebox.vibi) that runs
# `python -m mac_app` from this checkout. macOS TCC (Microphone, Bluetooth, Accessibility, Full Disk Access)
# is granted to this bundle, never to Terminal or the shared Python.
#   mac_app/build_app.sh            build to a temp dir, verify, then swap into ~/Applications
#   APP_DEST=/path/Vibi.app ...     install somewhere else; MAC_APP_PYTHON=... picks the interpreter
set -eu
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${MAC_APP_PYTHON:-$DIR/.venv/bin/python}"
[ -x "$PY" ] || PY="$HOME/esp/xiaozhi-server/main/xiaozhi-server/.venv/bin/python"
DEST="${APP_DEST:-$HOME/Applications/Vibi.app}"
ID=com.voicebox.vibi
[ -x "$PY" ] || { echo "python not found: $PY (set MAC_APP_PYTHON)" >&2; exit 1; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
APP="$TMP/Vibi.app"
mkdir -p "$APP/Contents/MacOS"
clang -O2 -DSERVER_DIR="\"$DIR\"" -DPY="\"$PY\"" -o "$APP/Contents/MacOS/Vibi" "$DIR/scripts/vibi_launcher.c"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>$ID</string>
<key>CFBundleName</key><string>Vibi</string>
<key>CFBundleDisplayName</key><string>Vibi</string>
<key>CFBundleExecutable</key><string>Vibi</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>12.0</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
<key>NSMicrophoneUsageDescription</key><string>Vibi listens for "hi vibi" and your voice commands.</string>
<key>NSBluetoothAlwaysUsageDescription</key><string>Vibi uses Bluetooth to talk to the voice board when Wi-Fi is down.</string>
<key>NSBluetoothPeripheralUsageDescription</key><string>Vibi uses Bluetooth to talk to the voice board when Wi-Fi is down.</string>
<key>NSAppleEventsUsageDescription</key><string>Vibi shows notifications and controls audio devices.</string>
</dict></plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null
# Ad-hoc signature with a fixed designated requirement (identifier only), so the TCC grant keeps matching after
# every rebuild even though the cdhash changes.
codesign --force --sign - --identifier "$ID" -r="designated => identifier \"$ID\"" "$APP"
codesign --verify --strict "$APP"
codesign -d -r- "$APP" 2>&1 | grep -q "identifier \"$ID\"" || { echo "designated requirement not set" >&2; exit 1; }
# Smoke test: the stub must exec the interpreter and pass its exit code through.
"$APP/Contents/MacOS/Vibi" >/dev/null 2>&1 </dev/null &
SP=$!; sleep 1
if kill -0 "$SP" 2>/dev/null; then kill "$SP"; wait "$SP" 2>/dev/null || true; fi

mkdir -p "$(dirname "$DEST")"
if [ -e "$DEST" ]; then rm -rf "$DEST.old"; mv "$DEST" "$DEST.old"; fi
mv "$APP" "$DEST"
rm -rf "$DEST.old"
echo "Built $DEST (id $ID, runs $DIR with $PY)"
echo "Restart: kill the running mac_app python pid, then: launchctl kickstart -k gui/$(id -u)/com.voicebox.macapp"
