param(
  [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Venv = Join-Path $Root ".win-build-venv"

if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
  $BasePython = $null
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $Candidate = & py -3.12 -c "import struct,sys; assert struct.calcsize('P') == 8; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $Candidate) {
      $BasePython = ($Candidate | Select-Object -Last 1).Trim()
    }
  }
  if (-not $BasePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $Candidate = & python -c "import struct,sys; assert sys.version_info >= (3, 9) and struct.calcsize('P') == 8; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $Candidate) {
      $BasePython = ($Candidate | Select-Object -Last 1).Trim()
    }
  }
  if (-not $BasePython) { throw "需要 64 位 Python 3.9 或更高版本来构建 Windows EXE" }
  & $BasePython -m venv $Venv
  if ($LASTEXITCODE -ne 0) { throw "创建 Windows 构建环境失败: $BasePython" }
}

$Python = Join-Path $Venv "Scripts\python.exe"
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip 升级失败" }
& $Python -m pip install -r (Join-Path $Root "requirements-webview.txt")
if ($LASTEXITCODE -ne 0) { throw "Windows 构建依赖安装失败" }

& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name "百度指数查询工具-v2.4.0-Windows-x64" `
  --paths (Join-Path $Root "src") `
  --hidden-import webview.platforms.edgechromium `
  --icon (Join-Path $Root "assets\econ-index-icon.ico") `
  --add-data "$(Join-Path $Root 'src\baidu_index_tool\webview_ui');baidu_index_tool\webview_ui" `
  --exclude-module cefpython3 `
  (Join-Path $Root "run_webview_gui.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$Exe = Join-Path $Root "dist\百度指数查询工具-v2.4.0-Windows-x64.exe"
if (-not (Test-Path $Exe)) { throw "EXE output missing: $Exe" }

if (-not $SkipSmokeTest) {
  $Process = Start-Process -FilePath $Exe -ArgumentList "--demo", "--auto-close-ms", "3000" -Wait -PassThru
  if ($Process.ExitCode -ne 0) { throw "GUI smoke test failed: $Exe, exit $($Process.ExitCode)" }
}

Get-FileHash -Algorithm SHA256 $Exe | Format-List
Write-Host "Windows executable: $Exe"
