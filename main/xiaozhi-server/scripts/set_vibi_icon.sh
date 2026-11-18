#!/bin/sh
# Give ~/Applications/Vibi.app its custom icon. Uses macOS's custom-icon mechanism, so the signed
# bundle contents stay untouched. Run it AFTER codesign: codesign rejects a bundle that already
# carries the custom-icon file, so build scripts call remove first when re-signing.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Applications/Vibi.app}"
osascript -l JavaScript -e "ObjC.import('AppKit'); \$.NSWorkspace.sharedWorkspace.setIconForFileOptions(\$.NSImage.alloc.initWithContentsOfFile('$DIR/vibi_icon.png'), '$APP', 0)" >/dev/null
echo "Vibi.app icon set"
