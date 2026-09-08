#!/usr/bin/env bash
# Build ~/Applications/rtk-api.app (the TCC "responsible process" launcher).
# Run ON rtk (needs Apple clang from Command Line Tools). Build once; rebuilding
# changes the ad-hoc signature and invalidates existing TCC grants, so only
# rerun if main.c changes -- and then re-grant permissions.
set -euo pipefail
cd "$(dirname "$0")"
APP="$HOME/Applications/rtk-api.app"
BIN="$APP/Contents/MacOS/rtk-api"
if [[ -x "$BIN" && "${1:-}" != "--force" ]]; then
  echo "already built: $BIN (pass --force to rebuild; this invalidates TCC grants)"
  exit 0
fi
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>rtk-api</string>
  <key>CFBundleIdentifier</key><string>com.noahbres.rtk-api</string>
  <key>CFBundleName</key><string>rtk-api</string>
  <key>CFBundleDisplayName</key><string>rtk-api</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>Personal launcher for rtk-api (see ararat repo).</string>
</dict>
</plist>
PLIST
clang -O2 -Wall -Wextra -o "$BIN" main.c
codesign --force --sign - --identifier com.noahbres.rtk-api "$APP"
codesign --verify --verbose=2 "$APP"
echo "built: $BIN"
"$BIN" /bin/echo "launcher smoke test ok"
