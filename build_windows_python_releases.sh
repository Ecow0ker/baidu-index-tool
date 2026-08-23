#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CACHE="$ROOT/.windows-portable-cache"
WEB_CACHE="$ROOT/.windows-webview-cache"
BUILD="$ROOT/.windows-python-build"
LAUNCHER="$ROOT/windows_python_launcher"
PYTHON=${PYTHON:-python3}
PYTHON_VERSION=3.12.10
VERSION=2.4.0
OUTPUT="$ROOT/dist/百度指数查询工具-v${VERSION}-Windows-x64.exe"
mkdir -p "$CACHE" "$WEB_CACHE" "$BUILD" "$ROOT/dist"

fetch() {
  url=$1
  output=$2
  [ -f "$output" ] || curl --fail --location --retry 10 --retry-all-errors "$url" --output "$output"
}
fetch "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-embed-amd64.zip" "$CACHE/python-$PYTHON_VERSION-embed-amd64.zip"

if [ ! -f "$WEB_CACHE/pywebview-6.1-py3-none-any.whl" ]; then
  "$PYTHON" -m pip download --disable-pip-version-check --no-deps --dest "$WEB_CACHE" "pywebview==6.1"
  "$PYTHON" -m pip download --disable-pip-version-check --no-deps --dest "$WEB_CACHE" "proxy_tools==0.1.0"
  "$PYTHON" -m pip wheel --no-deps --wheel-dir "$WEB_CACHE" "$WEB_CACHE/proxy_tools-0.1.0.tar.gz"
  "$PYTHON" -m pip download --disable-pip-version-check --no-deps --dest "$WEB_CACHE" \
    "bottle==0.13.4" "typing_extensions==4.16.0" "certifi==2026.7.22"
fi
if ! find "$WEB_CACHE" -maxdepth 1 -name 'pythonnet-3.1.0-*-win32.win_amd64.whl' | grep -q .; then
  "$PYTHON" -m pip download --disable-pip-version-check --dest "$WEB_CACHE" \
    --platform win_amd64 --python-version 3.12 --implementation cp --abi cp312 --only-binary=:all: \
    "pythonnet==3.1.0" "bottle==0.13.4" "typing_extensions==4.16.0" "certifi==2026.7.22"
fi

PAYLOAD="$BUILD/payload"
"$PYTHON" - "$PAYLOAD" <<'PY'
from pathlib import Path
import shutil, sys
path=Path(sys.argv[1])
if path.exists(): shutil.rmtree(path)
(path/'Lib/site-packages').mkdir(parents=True)
(path/'app').mkdir(parents=True)
PY
unzip -q "$CACHE/python-$PYTHON_VERSION-embed-amd64.zip" -d "$PAYLOAD"
cp -R "$ROOT/src" "$PAYLOAD/app/src"
"$PYTHON" - "$PAYLOAD/app/src" <<'PY'
from pathlib import Path
import shutil, sys
root=Path(sys.argv[1])
for path in root.rglob('__pycache__'): shutil.rmtree(path, ignore_errors=True)
for suffix in ('*.pyc','*.pyo'):
    for path in root.rglob(suffix): path.unlink(missing_ok=True)
PY
cp "$ROOT/README.md" "$ROOT/REVERSE_ENGINEERING.md" "$ROOT/reverse_evidence.json" "$PAYLOAD/app/"
cat > "$PAYLOAD/python312._pth" <<'EOF_PTH'
python312.zip
.
Lib\site-packages
app\src
import site
EOF_PTH
for wheel in "$WEB_CACHE"/*.whl; do unzip -q -o "$wheel" -d "$PAYLOAD/Lib/site-packages"; done
cp "$ROOT/run_webview_gui.py" "$PAYLOAD/app/run_webview_gui.py"

PAYLOAD_ZIP="$BUILD/payload.zip"
"$PYTHON" - "$PAYLOAD" "$PAYLOAD_ZIP" <<'PY'
from pathlib import Path
import hashlib, sys, zipfile
root, target = Path(sys.argv[1]), Path(sys.argv[2])
if target.exists(): target.unlink()
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
    for path in sorted(root.rglob('*')):
        if path.is_file(): archive.write(path, path.relative_to(root).as_posix())
print('payload_zip_bytes=%d' % target.stat().st_size)
print('payload_sha256=%s' % hashlib.sha256(target.read_bytes()).hexdigest())
PY

WORK="$BUILD/launcher"
"$PYTHON" - "$WORK" <<'PY'
from pathlib import Path
import shutil, sys
path=Path(sys.argv[1])
if path.exists(): shutil.rmtree(path)
path.mkdir(parents=True)
PY
cp "$LAUNCHER/launcher.c" "$LAUNCHER/resource.rc" "$WORK/"
cp "$ROOT/assets/econ-index-icon.ico" "$WORK/econ-index-icon.ico"
cp "$PAYLOAD_ZIP" "$WORK/payload.zip"
PAYLOAD_ID=$(shasum -a 256 "$WORK/payload.zip" | awk '{print $1}')
cat > "$WORK/config.h" <<EOF_CONFIG
#define ENTRY_SCRIPT L"run_webview_gui.py"
#define EDITION_NAME L"desktop"
#define PAYLOAD_ID L"$PAYLOAD_ID"
EOF_CONFIG
(cd "$WORK" && x86_64-w64-mingw32-windres resource.rc -O coff -o resource.o)
x86_64-w64-mingw32-gcc -Os -s -municode -mwindows \
  -include "$WORK/config.h" \
  "$WORK/launcher.c" "$WORK/resource.o" -lshell32 -luser32 -o "$OUTPUT"
file "$OUTPUT"
shasum -a 256 "$OUTPUT"
