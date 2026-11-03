# PyInstaller spec for BLELocate.exe (one folder: dist\BLELocate\).
# Build with build_exe.bat, which also runs the packaged self-test.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path[:0] = [SPECPATH, str(ROOT)]

from pywinrt_collect import check_complete, collect_pywinrt  # noqa: E402
from blelocate.selftest import REQUIRED_WINRT_MODULES  # noqa: E402

winrt_hidden, winrt_binaries, winrt_datas = collect_pywinrt()
missing = check_complete(winrt_hidden, winrt_datas, REQUIRED_WINRT_MODULES)
if missing:
    raise SystemExit("These PyWinRT modules are not installed in the build environment: "
                     + ", ".join(missing) + "\nRun setup.bat, then build again.")
print(f"PyWinRT: {len(winrt_hidden)} modules, {len(winrt_binaries)} DLLs, {len(winrt_datas)} tables")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=winrt_binaries,
    datas=winrt_datas + [(str(ROOT / "blelocate" / "web"), "blelocate/web")],
    hiddenimports=winrt_hidden + [
        "bleak.backends.winrt.scanner",
        "bleak.backends.winrt.util",
        "blelocate.selftest",
        "blelocate.hub",
        "blelocate.anchor",
    ],
    excludes=[
        "tkinter",
        "pytest",
        "dbus_fast",
        "bleak.backends.bluezdbus",
        "bleak.backends.corebluetooth",
        "bleak.backends.p4android",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BLELocate",
    console=True,  # the console window shows status and errors
    upx=False,     # UPX can corrupt WinRT .pyd files and triggers antivirus alerts
)
coll = COLLECT(exe, a.binaries, a.datas, name="BLELocate", upx=False)
