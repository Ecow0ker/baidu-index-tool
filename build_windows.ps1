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
  --name "baidu-index-tool-v2.4.3-windows-x64" `
  --paths (Join-Path $Root "src") `
  --hidden-import webview.platforms.edgechromium `
  --collect-submodules truststore `
  --icon (Join-Path $Root "assets\econ-index-icon.ico") `
  --add-data "$(Join-Path $Root 'src\baidu_index_tool\webview_ui');baidu_index_tool\webview_ui" `
  --exclude-module cefpython3 `
  (Join-Path $Root "run_webview_gui.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$Exe = Join-Path $Root "dist\baidu-index-tool-v2.4.3-windows-x64.exe"
if (-not (Test-Path $Exe)) { throw "EXE output missing: $Exe" }

if (-not $SkipSmokeTest) {
  $TlsResult = Join-Path $env:TEMP "baidu-index-tls-self-test.json"
  $TlsProcess = Start-Process -FilePath $Exe -ArgumentList "--tls-self-test-output", $TlsResult -Wait -PassThru
  if ($TlsProcess.ExitCode -ne 0) { throw "TLS smoke test failed: exit $($TlsProcess.ExitCode)" }
  $Tls = Get-Content -Raw -Encoding UTF8 $TlsResult | ConvertFrom-Json
  if (-not ($Tls.success -and $Tls.certificateRequired -and $Tls.hostnameVerified -and $Tls.frozen)) { throw "TLS verification failed" }
  Write-Host "Windows frozen HTTPS check: verified, HTTP $($Tls.httpStatus)"
  $UiResult = Join-Path $env:TEMP "baidu-index-ui-self-test.json"
  if (Test-Path $UiResult) { Remove-Item -Force $UiResult }
  $Process = Start-Process -FilePath $Exe `
    -ArgumentList "--demo", "--ui-self-test-output", $UiResult -Wait -PassThru
  if ($Process.ExitCode -ne 0) { throw "GUI smoke test failed: $Exe, exit $($Process.ExitCode)" }
  if (-not (Test-Path $UiResult)) { throw "GUI self-test result missing: $UiResult" }
  $Ui = Get-Content -Raw -Encoding UTF8 $UiResult | ConvertFrom-Json
  if (-not $Ui.allPass) { throw "GUI self-test failed: $(Get-Content -Raw -Encoding UTF8 $UiResult)" }
  $CheckCount = ($Ui.checks.PSObject.Properties | Measure-Object).Count
  Write-Host "Windows GUI self-test: $CheckCount/$CheckCount"
}

Get-FileHash -Algorithm SHA256 $Exe | Format-List
Write-Host "Windows executable: $Exe"
