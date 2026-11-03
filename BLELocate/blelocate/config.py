"""The shared configuration P1 owns, its validation and its on-disk store."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path

from .matching import normalize_address, normalize_uuid

log = logging.getLogger(__name__)

DEFAULT_TARGET_UUID = "12345678-1234-5678-1234-56789abcdef0"
ANCHOR_IDS = ("P1", "P2", "P3")

DEFAULT_CONFIG = {
    "schema": 1,
    "revision": 1,
    "target_revision": 1,
    "room": {"width_m": 6.0, "depth_m": 4.0},
    "target": {"service_uuid": DEFAULT_TARGET_UUID, "name": "", "address": "", "height_m": 1.0},
    "anchors": [
        {"id": "P1", "label": "P1 hub laptop", "x": 0.0, "y": 0.0, "z": 0.75, "rssi_at_1m": -59.0},
        {"id": "P2", "label": "P2", "x": 6.0, "y": 0.0, "z": 0.75, "rssi_at_1m": -59.0},
        {"id": "P3", "label": "P3", "x": 3.0, "y": 4.0, "z": 0.75, "rssi_at_1m": -59.0},
    ],
    "ranging": {"path_loss_exponent": 2.2},
    "smoothing": {"median_window": 5, "kalman_enabled": True, "process_noise": 2.0, "measurement_noise": 10.0},
    "freshness": {"stale_after_s": 3.0, "forget_after_s": 30.0},
}


class ConfigInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class ConfigConflict(Exception):
    def __init__(self, current: dict):
        super().__init__("Configuration was changed by someone else.")
        self.current = current


def default_data_dir() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "BLELocate"
    return Path.home() / ".blelocate"


def target_key(config: dict) -> tuple[str, str, str]:
    t = config["target"]
    return t["service_uuid"], t["name"], t["address"]


def validate_config(raw) -> dict:
    """Return a normalized copy of ``raw`` (minus revisions) or raise ConfigInvalid."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise ConfigInvalid(["Configuration must be a JSON object."])

    def section(name):
        value = raw.get(name)
        if not isinstance(value, dict):
            errors.append(f"'{name}' must be an object.")
            return {}
        return value

    def number(obj, key, label, lo, hi):
        value = obj.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            errors.append(f"{label} must be a number.")
            return lo
        if not lo <= value <= hi:
            errors.append(f"{label} must be between {lo} and {hi}.")
        return float(value)

    def text(obj, key, label, max_len):
        value = obj.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            errors.append(f"{label} must be text.")
            return ""
        value = value.strip()
        if len(value) > max_len:
            errors.append(f"{label} must be at most {max_len} characters.")
        return value

    room, target, ranging = section("room"), section("target"), section("ranging")
    smoothing, freshness = section("smoothing"), section("freshness")
    out = {
        "schema": 1,
        "room": {
            "width_m": number(room, "width_m", "Room width", 0.5, 200),
            "depth_m": number(room, "depth_m", "Room depth", 0.5, 200),
        },
        "target": {
            "service_uuid": "",
            "name": text(target, "name", "Target name", 64),
            "address": "",
            "height_m": number(target, "height_m", "Target height", 0, 10),
        },
        "anchors": [],
        "ranging": {"path_loss_exponent": number(ranging, "path_loss_exponent", "Path-loss exponent", 1.0, 6.0)},
        "smoothing": {
            "median_window": 1,
            "kalman_enabled": True,
            "process_noise": number(smoothing, "process_noise", "Kalman process noise", 0.01, 100),
            "measurement_noise": number(smoothing, "measurement_noise", "Kalman measurement noise", 0.1, 400),
        },
        "freshness": {
            "stale_after_s": number(freshness, "stale_after_s", "Stale-after time", 0.5, 30),
            "forget_after_s": number(freshness, "forget_after_s", "Forget-after time", 5, 600),
        },
    }
    for key, normalize, message in (
        ("service_uuid", normalize_uuid, "Target service UUID is not a valid UUID."),
        ("address", normalize_address, "Target address must look like AA:BB:CC:DD:EE:FF."),
    ):
        if target.get(key):
            try:
                out["target"][key] = normalize(target[key])
            except (ValueError, TypeError):
                errors.append(message)
    if not (target.get("service_uuid") or out["target"]["name"] or target.get("address")):
        errors.append("Give the target a service UUID, a name or an address.")

    window = smoothing.get("median_window")
    if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 15:
        errors.append("Median window must be a whole number between 1 and 15.")
    else:
        out["smoothing"]["median_window"] = window
    if not isinstance(smoothing.get("kalman_enabled"), bool):
        errors.append("Kalman enabled must be true or false.")
    else:
        out["smoothing"]["kalman_enabled"] = smoothing["kalman_enabled"]
    if out["freshness"]["forget_after_s"] < out["freshness"]["stale_after_s"]:
        errors.append("Forget-after time must not be shorter than stale-after time.")

    anchors = raw.get("anchors")
    if not isinstance(anchors, list):
        errors.append("'anchors' must be a list.")
        anchors = []
    seen = set()
    for item in anchors:
        if not isinstance(item, dict) or item.get("id") not in ANCHOR_IDS:
            errors.append(f"Each anchor needs an id from {', '.join(ANCHOR_IDS)}.")
            continue
        aid = item["id"]
        if aid in seen:
            errors.append(f"Anchor {aid} appears twice.")
        seen.add(aid)
        out["anchors"].append({
            "id": aid,
            "label": text(item, "label", f"{aid} label", 40) or aid,
            "x": number(item, "x", f"{aid} x", -100, 300),
            "y": number(item, "y", f"{aid} y", -100, 300),
            "z": number(item, "z", f"{aid} height", 0, 10),
            "rssi_at_1m": number(item, "rssi_at_1m", f"{aid} RSSI at 1 m", -100, -20),
        })
    missing = [a for a in ANCHOR_IDS if a not in seen]
    if missing:
        errors.append(f"Missing anchors: {', '.join(missing)}.")
    out["anchors"].sort(key=lambda a: ANCHOR_IDS.index(a["id"]))

    if errors:
        raise ConfigInvalid(errors)
    return out


class ConfigStore:
    """Holds the one authoritative configuration and persists it atomically."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._config = self._load()

    @property
    def config(self) -> dict:
        return copy.deepcopy(self._config)

    def update(self, raw: dict, base_revision) -> dict:
        """Replace the configuration if ``base_revision`` is still current."""
        new = validate_config(raw)
        with self._lock:
            current = self._config
            if base_revision != current["revision"]:
                raise ConfigConflict(copy.deepcopy(current))
            new["revision"] = current["revision"] + 1
            new["target_revision"] = (
                new["revision"] if target_key(new) != target_key(current) else current["target_revision"]
            )
            self._write(new)
            self._config = new
            return copy.deepcopy(new)

    def update_anchor(self, anchor_id: str, **fields) -> dict:
        with self._lock:
            draft = copy.deepcopy(self._config)
            base = draft["revision"]
        for anchor in draft["anchors"]:
            if anchor["id"] == anchor_id:
                anchor.update(fields)
        return self.update(draft, base)

    def _load(self) -> dict:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                config = validate_config(raw)
                config["revision"] = int(raw.get("revision", 1))
                config["target_revision"] = int(raw.get("target_revision", config["revision"]))
                return config
            except (OSError, ValueError, TypeError) as exc:
                # Keep the unreadable file for the user instead of overwriting it.
                backup = self.path.with_name(f"{self.path.name}.unreadable-{int(time.time())}")
                log.warning("Could not read %s (%s). Saved it as %s and started from defaults.", self.path, exc, backup)
                os.replace(self.path, backup)
        config = copy.deepcopy(DEFAULT_CONFIG)
        self._write(config)
        return config

    def _write(self, config: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
