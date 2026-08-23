#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RELEASE="$ROOT/release"
VERSION=2.4.0

MAC_X64_APP="$ROOT/dist/macos-x64/百度指数查询工具.app"
MAC_ARM64_APP="$ROOT/dist/macos-arm64/百度指数查询工具.app"
WIN_EXE="$ROOT/dist/百度指数查询工具-v${VERSION}-Windows-x64.exe"
MAC_X64_ZIP="$RELEASE/百度指数查询工具-v${VERSION}-macOS-x64.zip"
MAC_ARM64_ZIP="$RELEASE/百度指数查询工具-v${VERSION}-macOS-arm64.zip"
WIN_RELEASE="$RELEASE/$(basename "$WIN_EXE")"
GITHUB_ZIP="$RELEASE/百度指数查询工具-v${VERSION}-GitHub源码.zip"

mkdir -p "$RELEASE"
for path in "$MAC_X64_APP" "$MAC_ARM64_APP" "$WIN_EXE"; do
  test -e "$path"
done
# Local copies can inherit quarantine metadata. Clear it before re-signing and
# archiving so the ZIP does not preserve a stale download origin.
for app in "$MAC_X64_APP" "$MAC_ARM64_APP"; do
  xattr -cr "$app"
  codesign --force --deep --sign - "$app"
done
codesign --verify --deep --strict "$MAC_X64_APP"
codesign --verify --deep --strict "$MAC_ARM64_APP"
[ "$(lipo -archs "$MAC_X64_APP/Contents/MacOS/百度指数查询工具")" = "x86_64" ]
[ "$(lipo -archs "$MAC_ARM64_APP/Contents/MacOS/百度指数查询工具")" = "arm64" ]

python3 - "$RELEASE" <<'PY'
from pathlib import Path
import shutil, sys
release = Path(sys.argv[1])
for path in release.iterdir():
    if path.is_dir(): shutil.rmtree(path)
    else: path.unlink()
PY

ditto -c -k --sequesterRsrc --keepParent "$MAC_X64_APP" "$MAC_X64_ZIP"
ditto -c -k --sequesterRsrc --keepParent "$MAC_ARM64_APP" "$MAC_ARM64_ZIP"
cp "$WIN_EXE" "$WIN_RELEASE"

python3 - "$ROOT" "$GITHUB_ZIP" "$VERSION" <<'PY'
from pathlib import Path
import sys, zipfile

root, target, version = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
top_files = [
    ".gitignore", ".github/workflows/build-macos.yml", ".github/workflows/build-windows.yml", "AGENTS.md",
    "Linux_命令行.sh", "README.md", "REVERSE_ENGINEERING.md",
    "Windows_启动图形界面.bat", "Windows_启动图形界面.pyw",
    "build_macos.sh", "build_windows.ps1", "build_windows_python_releases.sh",
    "macOS_启动图形界面.command", "package_python_releases.sh", "pyproject.toml",
    "requirements-webview.txt", "reverse_evidence.json", "run_webview_gui.py",
]
paths = [root / value for value in top_files]
for directory in ("assets", "src", "tests", "windows_python_launcher"):
    paths.extend(path for path in (root / directory).rglob("*") if path.is_file())

def wanted(path: Path) -> bool:
    return path.is_file() and path.name != ".DS_Store" and path.suffix not in {".pyc", ".pyo"} and "__pycache__" not in path.parts

paths = sorted({path for path in paths if wanted(path)}, key=lambda p: p.relative_to(root).as_posix())
github_root = f"baidu-index-tool-v{version}"
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in paths:
        archive.write(path, f"{github_root}/{path.relative_to(root).as_posix()}")
print(f"github_source_files={len(paths)}")
print(f"github_zip_bytes={target.stat().st_size}")
PY

cat > "$RELEASE/README.md" <<EOF_README
# 百度指数查询工具 v${VERSION}

- macOS Intel：下载 \`百度指数查询工具-v${VERSION}-macOS-x64.zip\`。
- macOS M 系列：下载 \`百度指数查询工具-v${VERSION}-macOS-arm64.zip\`。
- Windows 64 位：下载 \`百度指数查询工具-v${VERSION}-Windows-x64.exe\`。

macOS 首次启动若提示来源限制，可右键 APP 选择“打开”；也可执行：

\`\`\`bash
xattr -dr com.apple.quarantine /Applications/百度指数查询工具.app
\`\`\`

大体积二进制文件请作为 GitHub Release assets 上传。
EOF_README

python3 - "$RELEASE" "$VERSION" \
  "$(basename "$MAC_X64_ZIP")" \
  "$(basename "$MAC_ARM64_ZIP")" \
  "$(basename "$WIN_RELEASE")" \
  "$(basename "$GITHUB_ZIP")" <<'PY'
from pathlib import Path
import hashlib, sys

release, version, *names = sys.argv[1:]
root = Path(release)
lines = []
for name in names:
    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
    lines.append(f"{digest}  {name}")
(root / f"SHA256SUMS-v{version}.txt").write_text(
    "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
)
PY

printf 'release=%s\n' "$RELEASE"
