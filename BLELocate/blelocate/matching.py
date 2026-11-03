"""Decides which scanner callbacks come from the configured target (T).

Advertisements arrive in pieces: the service UUID may be in the advertising
packet while the name is only in the scan response, and later callbacks for the
same address may carry neither. Evidence is therefore kept per address, and an
address keeps its match until the target configuration changes or the address
has not been heard for ``forget_after_s``.

The browser implementation in ``web/matcher.js`` mirrors this file; both are
checked against ``tests/vectors/matching.json``.
"""

from __future__ import annotations

import math
import re
import uuid as _uuid
from dataclasses import dataclass

_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"
_HEX = set("0123456789abcdef")
_MAC = re.compile(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}")


def normalize_uuid(value: str) -> str:
    """Canonical lowercase 128-bit form; expands 16/32-bit Bluetooth UUIDs."""
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) in (4, 8) and set(text) <= _HEX:
        text = text.rjust(8, "0") + _BASE_UUID_SUFFIX
    return str(_uuid.UUID(text))  # raises ValueError for anything else


def is_valid_rssi(rssi) -> bool:
    """Windows reports -127 (and some stacks 0 or +127) when RSSI is unknown."""
    return (
        isinstance(rssi, (int, float))
        and not isinstance(rssi, bool)
        and math.isfinite(rssi)
        and -120 <= rssi < 0
    )


@dataclass
class _Evidence:
    saw_uuid: bool
    name: str | None
    last_seen: float


def normalize_address(value: str) -> str:
    """Canonical 'AA:BB:CC:DD:EE:FF'; raises ValueError for anything else."""
    text = str(value).strip().upper().replace("-", ":")
    if not _MAC.fullmatch(text):
        raise ValueError(f"Not a Bluetooth address: {value!r}")
    return text


class TargetMatcher:
    """Every configured part (service UUID, exact name, address) must match; empty parts are ignored."""

    def __init__(self, service_uuid: str, name: str = "", forget_after_s: float = 30.0, address: str = ""):
        self.service_uuid = self.name = self.address = ""
        self.forget_after_s = forget_after_s
        self._evidence: dict[str, _Evidence] = {}
        self.configure(service_uuid, name, forget_after_s, address)

    def configure(self, service_uuid: str, name: str = "", forget_after_s: float = 30.0, address: str = "") -> bool:
        """Apply a target configuration. Returns True (and forgets all matches) if it changed."""
        new = (normalize_uuid(service_uuid) if service_uuid else "", name or "",
               normalize_address(address) if address else "")
        if not any(new):
            raise ValueError("The target needs a service UUID, a name or an address.")
        self.forget_after_s = float(forget_after_s)
        if new == (self.service_uuid, self.name, self.address):
            return False
        self.service_uuid, self.name, self.address = new
        self._evidence.clear()
        return True

    def observe(self, address: str, uuids, name: str | None, now: float) -> bool:
        """Record one scanner callback. Returns True if it comes from the target."""
        self._expire(now)
        has_uuid = bool(self.service_uuid) and any(self._same_uuid(u) for u in (uuids or ()))
        evidence = self._evidence.get(address)
        if evidence is None:
            # Only remember addresses that showed something of the target, so
            # busy rooms full of other devices don't grow this table.
            if not (has_uuid or (self.name and name == self.name) or self._same_address(address)):
                return False
            evidence = self._evidence[address] = _Evidence(False, None, now)
        evidence.last_seen = now
        if has_uuid:
            evidence.saw_uuid = True
        if name:
            evidence.name = name
        return self._is_match(address, evidence)

    def matched_addresses(self, now: float) -> list[str]:
        self._expire(now)
        return [a for a, e in self._evidence.items() if self._is_match(a, e)]

    def _is_match(self, address: str, evidence: _Evidence) -> bool:
        return ((not self.service_uuid or evidence.saw_uuid)
                and (not self.name or evidence.name == self.name)
                and (not self.address or self._same_address(address)))

    def _same_address(self, address) -> bool:
        # Browser anchors report opaque device ids, never addresses, so they can't match here.
        return bool(self.address) and str(address).upper() == self.address

    def _same_uuid(self, value) -> bool:
        try:
            return normalize_uuid(value) == self.service_uuid
        except (ValueError, TypeError):
            return False

    def _expire(self, now: float) -> None:
        cutoff = now - self.forget_after_s
        for address in [a for a, e in self._evidence.items() if e.last_seen < cutoff]:
            del self._evidence[address]
