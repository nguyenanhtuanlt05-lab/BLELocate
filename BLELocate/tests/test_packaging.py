import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

from pywinrt_collect import check_complete, collect_pywinrt  # noqa: E402

from blelocate.selftest import REQUIRED_WINRT_MODULES  # noqa: E402

NAMESPACES = [m.split("winrt.", 1)[1] for m in REQUIRED_WINRT_MODULES if m != "winrt._winrt"]


def make_dist(site: Path, name: str, files: list[str]) -> None:
    info = site / f"{name}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name.split('-')[0]}\nVersion: 0\n")
    (info / "RECORD").write_text("".join(f"{f},,\n" for f in files))
    for f in files:
        (site / f).parent.mkdir(parents=True, exist_ok=True)
        (site / f).write_bytes(b"")


def test_pywinrt_3x_layout_collects_native_modules_and_runtime_dll(tmp_path):
    files = ["winrt/_winrt.cp312-win_amd64.pyd", "winrt/msvcp140.dll", "winrt/runtime/__init__.py",
             "winrt/system/__init__.py", "winrt/_winrt.pyi"]
    for ns in NAMESPACES:
        files += [f"winrt/_winrt_{ns.replace('.', '_')}.cp312-win_amd64.pyd", f"winrt/{ns.replace('.', '/')}/__init__.py"]
    make_dist(tmp_path, "winrt_runtime-3.2.1", files)
    hidden, binaries, datas = collect_pywinrt([str(tmp_path)])
    assert "winrt._winrt" in hidden
    assert "winrt._winrt_windows_devices_bluetooth_advertisement" in hidden
    assert binaries == [(str(tmp_path / "winrt" / "msvcp140.dll"), "winrt")]
    assert datas == [] and check_complete(hidden, datas, REQUIRED_WINRT_MODULES) == []


def test_pywinrt_4x_layout_collects_projection_tables(tmp_path):
    make_dist(tmp_path, "winrt_runtime-4.0.0", ["winrt/_winrt.cp313-win_amd64.pyd", "winrt/runtime/_internals.py",
                                                "winrt/__pyinstaller/__init__.py", "winrt/__pyinstaller/hook-winrt.py"])
    for ns in NAMESPACES:
        path = ns.replace(".", "/")
        make_dist(tmp_path, f"winrt_{ns.replace('.', '_')}-4", [f"winrt/{path}/__init__.py", f"winrt/{path}/_table.pywinrt"])
    hidden, binaries, datas = collect_pywinrt([str(tmp_path)])
    assert "winrt._winrt" in hidden and not any("__pyinstaller" in h for h in hidden)
    assert (str(tmp_path / "winrt/windows/devices/bluetooth/_table.pywinrt"), "winrt/windows/devices/bluetooth") in datas
    assert check_complete(hidden, datas, REQUIRED_WINRT_MODULES) == []


def test_missing_namespace_is_reported(tmp_path):
    make_dist(tmp_path, "winrt_runtime-3.2.1", ["winrt/_winrt.cp312-win_amd64.pyd", "winrt/windows/foundation/__init__.py"])
    hidden, _, datas = collect_pywinrt([str(tmp_path)])
    missing = check_complete(hidden, datas, REQUIRED_WINRT_MODULES)
    assert "winrt.windows.devices.bluetooth.advertisement" in missing and "winrt._winrt" not in missing


def test_launchers_use_the_intended_interpreter():
    source = ["start_hub.bat", "start_anchor.bat", "selftest.bat"]
    for name in source:
        text = (ROOT / name).read_text()
        assert r'set "PY=%~dp0.venv\Scripts\python.exe"' in text, name
        assert 'if not exist "%PY%"' in text and "setup.bat" in text, name
        assert not re.search(r"^\s*(py|python)(\.exe)?\s", text, re.M | re.I), f"{name} calls a PATH Python"
    packaged = list((ROOT / "packaging" / "dist-files").glob("*.bat"))
    assert len(packaged) == 4
    for path in packaged:
        text = path.read_text()
        if "firewall.ps1" in text:
            continue
        assert r'set "APP=%~dp0BLELocate.exe"' in text, path.name
        assert ".venv" not in text and "python" not in text.lower(), path.name


def test_spec_bundles_dashboard_and_collector():
    spec = (ROOT / "packaging" / "blelocate.spec").read_text()
    assert "collect_pywinrt()" in spec and "check_complete" in spec
    assert '"blelocate" / "web"' in spec and "upx=False" in spec
    assert (ROOT / "packaging" / "launcher.py").is_file()
