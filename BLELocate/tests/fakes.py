"""Stand-ins for Bleak so the anchor pipeline can run without Bluetooth hardware."""

import asyncio
import math
import time
from types import SimpleNamespace

TARGET = "12345678-1234-5678-1234-56789abcdef0"


class FakeBackend:
    """Behaves like BleakScanner: start/stop plus detection callbacks."""

    def __init__(self, callback, fail_with=None):
        self.callback = callback
        self.fail_with = fail_with
        self.running = False

    async def start(self):
        if self.fail_with is not None:
            raise self.fail_with
        self.running = True

    async def stop(self):
        self.running = False

    def emit(self, address, uuids=(), name=None, rssi=-60, service_data=None, companies=()):
        assert self.running, "callbacks only happen while scanning"
        device = SimpleNamespace(address=address)
        adv = SimpleNamespace(service_uuids=list(uuids), local_name=name, rssi=rssi, service_data=service_data or {},
                              manufacturer_data={c: b"" for c in companies})
        self.callback(device, adv)


class BackendFactory:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.created: list[FakeBackend] = []

    def __call__(self, callback):
        backend = FakeBackend(callback, self.failures.pop(0) if self.failures else None)
        self.created.append(backend)
        return backend

    @property
    def current(self):
        return self.created[-1] if self.created else None


async def wait_for(condition, timeout=5.0, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {message}")


def simulated_rssi(anchor, truth, target_height=1.0, n=2.2):
    d = math.sqrt((anchor["x"] - truth[0]) ** 2 + (anchor["y"] - truth[1]) ** 2 + (anchor["z"] - target_height) ** 2)
    return anchor["rssi_at_1m"] - 10 * n * math.log10(d)
