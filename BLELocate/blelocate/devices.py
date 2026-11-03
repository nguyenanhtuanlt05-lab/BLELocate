"""Every BLE device the laptop anchors hear, merged by address, for the dashboard's device list.

Raw BLE is jumpy: a device misses a few advertisements, RSSI swings by 10 dB,
names arrive in some packets and not others. The directory evens that out:
- a device is listed only after it has been heard a few times (one-off blips stay hidden),
- per-anchor RSSI is an exponential moving average of each anchor's 1 s medians,
- names keep the longest version seen; UUIDs and makers accumulate,
- a device stays listed for ``forget_after_s`` after it was last heard.
Row order and display hysteresis are handled by the dashboard (web/devices.js).
"""

from __future__ import annotations

import time

from .matching import is_valid_rssi, normalize_uuid

try:  # Bleak's Bluetooth SIG company table (a private module, so be tolerant)
    from bleak.backends._manufacturers import MANUFACTURERS
except ImportError:  # pragma: no cover
    MANUFACTURERS = {}

MAX_DEVICES = 500
MAX_REPORT_AGE_MS = 5000
HEARD_RECENTLY_S = 15.0  # per-anchor values older than this are not shown as current
EMA_ALPHA = 0.3


class DeviceDirectory:
    def __init__(self, clock=time.monotonic, forget_after_s: float = 120.0, min_hits: int = 3):
        self.clock = clock
        self.forget_after_s = forget_after_s
        self.min_hits = min_hits
        self.devices: dict[str, dict] = {}

    def ingest(self, anchor_id: str, reports) -> int:
        """Merge one anchor's 1 s summary. Returns how many entries were accepted."""
        if not isinstance(reports, list):
            return 0
        now = self.clock()
        accepted = 0
        for r in reports[:MAX_DEVICES]:
            if not isinstance(r, dict):
                continue
            address, rssi, age_ms, hits = r.get("addr"), r.get("rssi"), r.get("age_ms"), r.get("n")
            if not (isinstance(address, str) and 0 < len(address) <= 64 and is_valid_rssi(rssi)):
                continue
            if isinstance(age_ms, bool) or not isinstance(age_ms, (int, float)) or not 0 <= age_ms <= MAX_REPORT_AGE_MS:
                continue  # delayed reports are not new readings
            if isinstance(hits, bool) or not isinstance(hits, int) or not 1 <= hits <= 1000:
                continue
            address = address.upper()
            device = self.devices.get(address)
            if device is None:
                if len(self.devices) >= MAX_DEVICES:
                    continue  # shortcut: crowded places drop new devices until old ones are forgotten
                device = self.devices[address] = {"name": None, "uuids": [], "companies": [], "hits": 0,
                                                  "first_seen": now, "last_seen": now, "anchors": {}}
            t = now - age_ms / 1000.0
            name = r.get("name")
            if isinstance(name, str) and name.strip() and len(name) <= 64 and len(name) >= len(device["name"] or ""):
                device["name"] = name.strip()
            for u in r.get("uuids") or []:
                try:
                    u = normalize_uuid(u)
                except (ValueError, TypeError):
                    continue
                if u not in device["uuids"] and len(device["uuids"]) < 8:
                    device["uuids"].append(u)
            for c in r.get("companies") or []:
                if isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 0xFFFF \
                        and c not in device["companies"] and len(device["companies"]) < 4:
                    device["companies"].append(c)
            seen = device["anchors"].get(anchor_id)
            if seen is None or t - seen["t"] > HEARD_RECENTLY_S:
                seen = device["anchors"][anchor_id] = {"rssi": float(rssi), "t": t}
            elif t >= seen["t"]:
                seen["rssi"] += EMA_ALPHA * (rssi - seen["rssi"])
                seen["t"] = t
            device["hits"] += hits
            device["last_seen"] = max(device["last_seen"], t)
            accepted += 1
        return accepted

    def forget_anchor(self, anchor_id: str) -> None:
        """A reconnecting device may be a different laptop, so its old averages don't apply."""
        for device in self.devices.values():
            device["anchors"].pop(anchor_id, None)

    def snapshot(self, target: dict, anchor_ids) -> list[dict]:
        now = self.clock()
        for address in [a for a, d in self.devices.items() if now - d["last_seen"] > self.forget_after_s]:
            del self.devices[address]
        out = []
        for address, d in self.devices.items():
            if d["hits"] < self.min_hits:
                continue
            per_anchor = {}
            for aid in anchor_ids:
                seen = d["anchors"].get(aid)
                current = seen is not None and now - seen["t"] <= HEARD_RECENTLY_S
                per_anchor[aid] = round(seen["rssi"]) if current else None
            heard = [v for v in per_anchor.values() if v is not None]
            out.append({
                "address": address,
                "name": d["name"],
                "maker": short_maker(next((MANUFACTURERS[c] for c in d["companies"] if c in MANUFACTURERS), None)),
                "uuids": d["uuids"],
                "rssi": per_anchor,
                "best_rssi": max(heard) if heard else None,
                "last_seen_age_s": round(now - d["last_seen"], 1),
                "listed_for_s": round(now - d["first_seen"]),
                "is_target": device_matches(target, address, d),
            })
        return out


_MAKER_SUFFIXES = {"co", "co.", "ltd", "ltd.", "inc", "inc.", "llc", "corp", "corp.", "corporation", "limited",
                   "gmbh", "ab", "a/s", "asa", "technology", "technologies", "information", "electronics", "()", "( )"}


def short_maker(name: str | None) -> str | None:
    """'Anhui Huami Information Technology Co., Ltd.' -> 'Anhui Huami'; 'Apple, Inc.' -> 'Apple'."""
    if not name:
        return None
    words = name.split(",")[0].replace("( )", "").split()
    while len(words) > 1 and words[-1].lower() in _MAKER_SUFFIXES:
        words.pop()
    return " ".join(words) or name


def device_matches(target: dict, address: str, device: dict) -> bool:
    """Same rule as TargetMatcher, applied to what the directory knows about one device."""
    return ((not target["service_uuid"] or target["service_uuid"] in device["uuids"])
            and (not target["name"] or device["name"] == target["name"])
            and (not target.get("address") or address == target["address"]))
