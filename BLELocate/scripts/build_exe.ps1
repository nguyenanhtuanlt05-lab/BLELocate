<#
  Builds dist\BLELocate\ (BLELocate.exe plus its _internal folder) with PyInstaller,
  checks that the packaged exe loads the Windows BLE scanner (winrt._winrt and the
  Bluetooth namespaces), adds the launchers and docs, and zips the folder.
  Run through build_exe.bat. Option: -NoZip
#>
param([switch]$NoZip)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$Dist = Join-Path $Root "dist"
$Work = Join-Path $Root "build"
$App = Join-Path $Dist "BLELocate"
$Exe = Join-Path $App "BLELocate.exe"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text, $next) {
    Write-Host ""
    Write-Host "BUILD FAILED: $text" -ForegroundColor Red
    if ($next) { Write-Host "Next step: $next" -ForegroundColor Yellow }
    exit 1
}

if (-not (Test-Path $VenvPy)) {
    Fail "The Python environment (.venv) does not exist." "Run setup.bat first, then build_exe.bat again."
}
Push-Location $Root
try {
    Step "Installing the build tools (PyInstaller)"
    & $VenvPy -m pip install --disable-pip-version-check -r requirements-build.txt | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "Could not install PyInstaller." "Check the internet connection, then build again." }

    Step "Checking the scanner loads from .venv before packaging"
    & $VenvPy -m blelocate selftest --require-windows-scanner --no-adapter | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "Bluetooth support files in .venv are broken." "Run setup.bat again, then build again." }

    Step "Running PyInstaller"
    if (Test-Path $App) {
        try { Remove-Item -Recurse -Force $App }
        catch { Fail "Could not delete the old $App (is BLELocate.exe still running?)." "Close every BLE Locate window, then build again." }
    }
    & $VenvPy -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Work packaging\blelocate.spec | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Exe)) { Fail "PyInstaller did not produce $Exe (see the messages above)." "Fix the first error shown, then build again." }

    Step "Checking the bundled PyWinRT files"
    $winrtDir = Join-Path $App "_internal\winrt"
    $core = @(Get-ChildItem -Path $winrtDir -Filter "_winrt.*.pyd" -ErrorAction SilentlyContinue)
    if ($core.Count -eq 0) { Fail "winrt._winrt (_winrt.*.pyd) is missing from $winrtDir." "Run setup.bat again so PyWinRT is installed, then build again." }
    $natives = @(Get-ChildItem -Path $winrtDir -Recurse -Include "*.pyd", "*.dll", "_table.pywinrt")
    Write-Host "  Bundled: $($natives.Count) PyWinRT files, including $($core[0].Name)"

    Step "Verifying the packaged app loads the Windows scanner"
    & $Exe selftest --require-windows-scanner | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "The packaged BLELocate.exe cannot load the Windows scanner (see [FAIL] above)." "Report the [FAIL] lines; the .venv itself passed, so the spec is missing a file." }

    Step "Adding launchers and documentation"
    Copy-Item -Path (Join-Path $Root "packaging\dist-files\*") -Destination $App -Force
    Copy-Item -Path (Join-Path $Root "scripts\firewall.ps1"), (Join-Path $Root "README.md"), (Join-Path $Root "TROUBLESHOOTING.md") -Destination $App -Force

    if (-not $NoZip) {
        $version = (& $VenvPy -c "import blelocate; print(blelocate.__version__)").Trim()
        $arch = (& $VenvPy -c "import platform; print(platform.machine().lower())").Trim()
        $zip = Join-Path $Dist "BLELocate-$version-win-$arch.zip"
        Step "Creating $zip"
        if (Test-Path $zip) { Remove-Item -Force $zip }
        Compress-Archive -Path $App -DestinationPath $zip
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Build complete: $App" -ForegroundColor Green
Write-Host "Copy the zip (or the BLELocate folder) to P1, P2 and P3. See README.md, 'Deploy'."
exit 0
