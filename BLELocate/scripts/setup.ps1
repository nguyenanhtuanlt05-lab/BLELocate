<#
  BLE Locate setup: creates .venv in the project folder and installs Bleak,
  PyWinRT and the other dependencies, then checks that the Windows BLE
  scanner loads. Run through setup.bat (double-click) or:
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Python C:\path\to\python.exe]
#>
param([string]$Python = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text, $next) {
    Write-Host ""
    Write-Host "SETUP FAILED: $text" -ForegroundColor Red
    if ($next) { Write-Host "Next step: $next" -ForegroundColor Yellow }
    Write-Host "More help: TROUBLESHOOTING.md (section 'Setup')."
    exit 1
}

# Returns "major minor bits" for a Python command, or $null if it does not run.
function Get-PythonInfo([string[]]$Command) {
    $exe = $Command[0]
    $rest = @($Command | Select-Object -Skip 1)
    try {
        $out = & $exe @rest -c "import sys,struct;print(sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -Last 1).Trim() }
    } catch { }
    return $null
}

function Find-Python {
    if ($Python) { return ,@($Python) }
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.13", "3.12", "3.14", "3.11", "3.10") { $candidates += ,@("py", "-$v") }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    # The WindowsApps "python.exe" is a Microsoft Store shortcut, not Python.
    if ($onPath -and $onPath.Source -notlike "*\WindowsApps\*") { $candidates += ,@($onPath.Source) }
    foreach ($c in $candidates) {
        $info = Get-PythonInfo $c
        if (-not $info) { continue }
        $major, $minor, $bits = $info -split " "
        if ([int]$major -eq 3 -and [int]$minor -ge 10 -and [int]$bits -eq 64) { return ,$c }
        Write-Host "  Skipping $($c -join ' '): Python $major.$minor, $bits-bit (need 3.10+ 64-bit)"
    }
    return $null
}

Write-Host "BLE Locate setup in $Root"

Step "Finding Python 3.10 or newer (64-bit)"
$py = Find-Python
if (-not $py) {
    Fail "No suitable Python found." "Install Python 3.13 (64-bit) from https://www.python.org/downloads/windows/ with 'py launcher' ticked, then run setup.bat again."
}
Write-Host "  Using: $($py -join ' ')"

if (Test-Path $VenvPy) {
    if (-not (Get-PythonInfo @($VenvPy))) {
        Step "The existing .venv is broken (Python moved or uninstalled); recreating it"
        Remove-Item -Recurse -Force $Venv
    } else {
        Write-Host "  Reusing the existing environment: $Venv"
    }
}
if (-not (Test-Path $VenvPy)) {
    Step "Creating the virtual environment (.venv)"
    $exe = $py[0]; $rest = @($py | Select-Object -Skip 1)
    & $exe @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) {
        Fail "Could not create $Venv." "Check that the folder is writable (not inside a protected or synced folder), then run setup.bat again."
    }
}

Step "Installing BLE Locate's dependencies (Bleak, PyWinRT, aiohttp, ...)"
& $VenvPy -m pip install --disable-pip-version-check --upgrade pip | Out-Host
& $VenvPy -m pip install --disable-pip-version-check -r (Join-Path $Root "requirements.txt") | Out-Host
if ($LASTEXITCODE -ne 0) {
    Fail "pip could not install the dependencies." "Check the internet connection (and proxy settings, if any), then run setup.bat again."
}

Step "Checking that the Windows Bluetooth scanner loads"
Push-Location $Root
try { & $VenvPy -m blelocate selftest --require-windows-scanner | Out-Host } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) {
    Fail "The self-test failed (see the [FAIL] lines above)." "Delete the .venv folder and run setup.bat again. If it fails again, see TROUBLESHOOTING.md."
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  On P1 (dashboard laptop): run firewall_setup.bat once, then start_hub.bat"
Write-Host "  On P2 and P3:            run start_anchor.bat"
exit 0
