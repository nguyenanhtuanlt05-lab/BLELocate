"""Checks that this installation (venv or packaged exe) can run the Windows BLE scanner."""

from __future__ import annotations

import asyncio
import importlib
import os
import platform
import sys
import time

REQUIRED_WINRT_MODULES = (
    "winrt._winrt",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.storage.streams",
    "winrt.windows.devices.enumeration",
    "winrt.windows.devices.radios",
    "winrt.windows.devices.bluetooth",
    "winrt.windows.devices.bluetooth.advertisement",
    "winrt.windows.devices.bluetooth.genericattributeprofile",
)


class Report:
    def __init__(self):
        self.failed = False

    def ok(self, text):
        print(f"  [ok]   {text}")

    def warn(self, text):
        print(f"  [warn] {text}")

    def fail(self, text, next_step=None):
        self.failed = True
        print(f"  [FAIL] {text}")
        if next_step:
            print(f"         Next step: {next_step}")


def run_selftest(require_windows_scanner: bool = False, check_adapter: bool = True,
                 scan_seconds: float = 0, target_uuid: str | None = None) -> int:
    from . import __version__
    from .hub import STATIC_FILES, WEB_DIR

    report = Report()
    frozen = getattr(sys, "frozen", False)
    fix = ("rebuild with build_exe.bat and copy the whole dist\\BLELocate folder" if frozen
           else "run setup.bat again")
    print(f"BLE Locate {__version__} self-test")
    print(f"  Python {platform.python_version()} ({platform.architecture()[0]}) on {platform.platform()}")
    print(f"  Running from: {sys.executable}{' (packaged)' if frozen else ''}")

    for name in ("aiohttp", "segno", "ifaddr"):
        try:
            importlib.import_module(name)
            report.ok(f"{name} imports")
        except ImportError as exc:
            report.fail(f"{name} is missing: {exc}", fix)
    missing = [n for n in STATIC_FILES if not (WEB_DIR / n).is_file()]
    if missing:
        report.fail(f"Dashboard files missing from {WEB_DIR}: {', '.join(missing)}", fix)
    else:
        report.ok("dashboard files present")

    if sys.platform != "win32":
        if require_windows_scanner:
            report.fail("The Windows BLE scanner can only be checked on Windows.")
        else:
            report.warn("Not Windows: WinRT scanner checks skipped.")
        return _finish(report)

    bundle = os.path.normcase(getattr(sys, "_MEIPASS", ""))
    for name in REQUIRED_WINRT_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - DLL load errors are OSError/ImportError
            report.fail(f"{name} failed to import: {type(exc).__name__}: {exc}", fix)
            continue
        where = getattr(module, "__file__", "") or ""
        if frozen and bundle and not os.path.normcase(where).startswith(bundle):
            report.fail(f"{name} loaded from outside the package: {where}", fix)
        else:
            report.ok(f"{name}" + (f"  ({where})" if name == "winrt._winrt" else ""))

    try:
        from bleak.backends.winrt.scanner import BleakScannerWinRT  # noqa: F401
        from bleak import BleakScanner

        BleakScanner(scanning_mode="active")
        report.ok("Bleak WinRT scanner loads and can be created (active scanning)")
    except Exception as exc:  # noqa: BLE001
        report.fail(f"Bleak WinRT scanner could not be loaded: {type(exc).__name__}: {exc}", fix)

    if check_adapter and not report.failed:
        asyncio.run(_adapter_report(report))
    if scan_seconds and not report.failed:
        asyncio.run(_scan_report(report, scan_seconds, target_uuid))
    return _finish(report)


async def _adapter_report(report: Report) -> None:
    from winrt.windows.devices.bluetooth import BluetoothAdapter
    from winrt.windows.devices.radios import RadioState

    try:
        adapter = await BluetoothAdapter.get_default_async()
    except Exception as exc:  # noqa: BLE001
        report.warn(f"Could not query the Bluetooth adapter: {exc}")
        return
    if adapter is None:
        report.warn("No Bluetooth adapter found. Scanning will not work on this computer until one is available.")
        return
    report.ok(f"Bluetooth adapter found (LE supported: {adapter.is_low_energy_supported}, "
              f"central role: {adapter.is_central_role_supported})")
    radio = await adapter.get_radio_async()
    if radio.state != RadioState.ON:
        report.warn("Bluetooth radio is off. Turn it on in Settings > Bluetooth & devices.")
    else:
        report.ok("Bluetooth radio is on")


async def _scan_report(report: Report, seconds: float, target_uuid: str | None) -> None:
    from bleak import BleakScanner

    from .config import DEFAULT_TARGET_UUID
    from .matching import TargetMatcher, is_valid_rssi

    matcher = TargetMatcher(target_uuid or DEFAULT_TARGET_UUID)
    addresses, target_rssi, adverts = set(), [], 0

    def callback(device, adv):
        nonlocal adverts
        adverts += 1
        addresses.add(device.address)
        uuids = list(adv.service_uuids or []) + list((adv.service_data or {}).keys())
        if matcher.observe(device.address, uuids, adv.local_name, time.monotonic()) and is_valid_rssi(adv.rssi):
            target_rssi.append(adv.rssi)

    print(f"  Scanning for {seconds:.0f} s (active) ...")
    try:
        async with BleakScanner(detection_callback=callback, scanning_mode="active"):
            await asyncio.sleep(seconds)
    except Exception as exc:  # noqa: BLE001
        from .scanner import explain_scanner_error

        report.fail(f"Scan failed: {explain_scanner_error(exc)}")
        return
    report.ok(f"Heard {adverts} advertisements from {len(addresses)} devices")
    if target_rssi:
        report.ok(f"Target {matcher.service_uuid} heard {len(target_rssi)} times, strongest {max(target_rssi)} dBm")
    else:
        report.warn(f"Target {matcher.service_uuid} not heard. Check that nRF Connect is advertising it (see TROUBLESHOOTING.md).")


def _finish(report: Report) -> int:
    print("Self-test " + ("FAILED" if report.failed else "passed") + ".")
    return 1 if report.failed else 0
