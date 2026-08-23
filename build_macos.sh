#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
VERSION=2.4.0
TARGET_ARCH=${TARGET_ARCH:-$(uname -m)}

case "$TARGET_ARCH" in
  x64|x86_64|amd64)
    ASSET_ARCH=x64
    PYI_ARCH=x86_64
    PYTHON_BIN=${PYTHON_BIN:-python3}
    ;;
  arm64|aarch64)
    ASSET_ARCH=arm64
    PYI_ARCH=arm64
    if [ "$(uname -m)" = "x86_64" ] && [ -x /usr/bin/python3 ]; then
      PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}
    else
      PYTHON_BIN=${PYTHON_BIN:-python3}
    fi
    ;;
  *)
    echo "Unsupported macOS architecture: $TARGET_ARCH" >&2
    exit 2
    ;;
esac

VENV="$ROOT/.macos-$ASSET_ARCH-venv"
DIST="$ROOT/dist/macos-$ASSET_ARCH"
WORK="$ROOT/build/macos-$ASSET_ARCH"
SPECS="$ROOT/build/spec-$ASSET_ARCH"
if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$ROOT/requirements-webview.txt"

"$VENV/bin/python" -m PyInstaller --noconfirm --clean --windowed \
  --target-arch "$PYI_ARCH" \
  --name "百度指数查询工具" \
  --distpath "$DIST" \
  --workpath "$WORK" \
  --specpath "$SPECS" \
  --paths "$ROOT/src" \
  --hidden-import webview.platforms.cocoa \
  --icon "$ROOT/assets/econ-index-icon.icns" \
  --add-data "$ROOT/src/baidu_index_tool/webview_ui:baidu_index_tool/webview_ui" \
  --exclude-module cefpython3 \
  "$ROOT/run_webview_gui.py"

APP="$DIST/百度指数查询工具.app"
EXECUTABLE="$APP/Contents/MacOS/百度指数查询工具"
test -x "$EXECUTABLE"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.crossplatformtools.baiduindex" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP/Contents/Info.plist"
# Do not carry download/quarantine attributes from a local build environment
# into the distributable bundle. Sign only after the attributes are cleared.
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
plutil -lint "$APP/Contents/Info.plist"
codesign --verify --deep --strict "$APP"
ACTUAL_ARCHS=$(lipo -archs "$EXECUTABLE")
[ "$ACTUAL_ARCHS" = "$PYI_ARCH" ] || {
  echo "Unexpected architecture: $ACTUAL_ARCHS (expected $PYI_ARCH)" >&2
  exit 3
}
printf 'macOS %s: %s\n' "$ASSET_ARCH" "$APP"
