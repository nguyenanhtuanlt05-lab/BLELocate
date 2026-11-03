"""Continuous active BLE scanning on laptops (Bleak; WinRT backend on Windows)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

log = logging.getLogger(__name__)


def bleak_backend(callback):
    from bleak import BleakScanner

    return BleakScanner(detection_callback=callback, scanning_mode="active")


class ScannerRunner:
    """Keeps one scanner running: restarts it after errors or a long silence.

    ``on_advert(address, uuids, name, rssi, company_ids)`` is called for every scanner
    callback and nothing else, so readings only ever come from real callbacks.
    """

    def __init__(self, on_advert, backend_factory=bleak_backend, retry_delay_s: float = 5.0,
                 silence_restart_s: float = 20.0, clock=time.monotonic):
        self.on_advert = on_advert
        self.backend_factory = backend_factory
        self.retry_delay_s = retry_delay_s
        self.silence_restart_s = silence_restart_s
        self.clock = clock
        self.state = "starting"
        self.error: str | None = None
        self.detail: str | None = None
        self._last_advert = clock()

    async def run(self) -> None:
        try:
            while True:
                backend = None
                failed = False
                try:
                    self.state = "starting"
                    backend = self.backend_factory(self._callback)
                    await backend.start()
                    self.state, self.error = "running", None
                    self._last_advert = self.clock()
                    while self.clock() - self._last_advert < self.silence_restart_s:
                        await asyncio.sleep(0.5)
                    # WinRT watchers can stop silently (e.g. radio toggled); a restart
                    # either resumes scanning or surfaces the real error.
                    self.detail = f"No advertisements from any device for {self.silence_restart_s:.0f} s; restarted the scanner."
                    log.info(self.detail)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - every failure must reach the dashboard
                    self.state, self.error, failed = "stopped", explain_scanner_error(exc), True
                    log.warning("Scanner stopped: %s", self.error)
                finally:
                    if backend is not None:
                        with contextlib.suppress(Exception):
                            await backend.stop()
                if failed:
                    await asyncio.sleep(self.retry_delay_s)
        finally:
            self.state = "stopped"

    def _callback(self, device, adv) -> None:
        self._last_advert = self.clock()
        self.detail = None
        uuids = list(adv.service_uuids or []) + list((adv.service_data or {}).keys())
        self.on_advert(device.address, uuids, adv.local_name, adv.rssi, list((adv.manufacturer_data or {}).keys()))


def explain_scanner_error(exc: BaseException) -> str:
    """Turn scanner exceptions into a message that says what to do next."""
    reason = getattr(getattr(exc, "reason", None), "name", "")
    text = str(exc) or type(exc).__name__
    if isinstance(exc, ImportError):
        return (f"Bluetooth support files are missing ({text}). Run setup.bat again, "
                "or re-copy the complete BLELocate folder.")
    messages = {
        "NO_BLUETOOTH": "No Bluetooth adapter was found. Check Device Manager > Bluetooth, or plug in a USB Bluetooth adapter.",
        "POWERED_OFF": "Bluetooth is turned off. Turn it on in Settings > Bluetooth & devices.",
        "NO_BLE_CENTRAL_ROLE": "This Bluetooth adapter cannot scan for BLE devices. Use a Bluetooth 4.0+ (LE) adapter.",
        "DENIED_BY_USER": "Bluetooth access was denied. Allow it in Settings > Privacy & security > Radios.",
        "DENIED_BY_SYSTEM": "Bluetooth access is blocked by system policy. Ask the computer's administrator.",
    }
    if reason in messages:
        return messages[reason]
    winerror = getattr(exc, "winerror", None)
    if winerror in (-2147020577, 1167):  # 0x8007048F device not connected
        return "The Bluetooth adapter is not ready (switched off, disabled or unplugged). Turn Bluetooth on and wait a few seconds."
    if "apartment" in text.lower() or "MTA" in text:
        return f"Windows Bluetooth threading error ({text}). Restart BLE Locate; if it repeats, report it with the log file."
    return f"{type(exc).__name__}: {text}"
