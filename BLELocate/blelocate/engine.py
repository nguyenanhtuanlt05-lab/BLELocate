"""Hub-side state: per-anchor freshness, smoothing, distances, position, calibration.

Timestamps are taken on P1's clock: anchors send each sample's age at send
time, so no clock synchronization between devices is needed.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from .config import ANCHOR_IDS
from .filters import RssiSmoother
from .matching import is_valid_rssi
from .positioning import PositionError, trilaterate
from .ranging import calibrate_rssi_at_1m, horizontal_distance, rssi_to_distance

# Measurement states shown on the dashboard.
OFFLINE = "offline"                  # not connected to P1
SCANNER_STOPPED = "scanner_stopped"
TARGET_NOT_SEEN = "target_not_seen"
NO_FRESH_SAMPLE = "no_fresh_sample"
FRESH = "fresh"

SCANNER_STATES = {"starting", "running", "stopped", "paused"}
MAX_SAMPLE_AGE_MS = 60_000


@dataclass
class AnchorTrack:
    anchor_id: str
    smoother: RssiSmoother
    connected: bool = False
    client: dict = field(default_factory=dict)
    scanner: str = "stopped"
    scanner_error: str | None = None
    scanner_detail: str | None = None
    adverts_seen: int = 0
    target_matched: bool = False
    matched_address: str | None = None
    cfg_rev: int = 0
    last_sample_t: float | None = None
    rssi_raw: float | None = None
    rssi_smoothed: float | None = None
    sample_count: int = 0
    rejected: dict = field(default_factory=dict)
    last_reject: str | None = None

    def reset_measurements(self) -> None:
        self.smoother.reset()
        self.last_sample_t = self.rssi_raw = self.rssi_smoothed = None
        self.matched_address = None
        self.target_matched = False
        self.sample_count = 0
        self.rejected = {}
        self.last_reject = None


class LocalizationEngine:
    def __init__(self, config: dict, clock=time.monotonic):
        self.clock = clock
        self.config = config
        self.tracks = {aid: AnchorTrack(aid, RssiSmoother.from_config(config)) for aid in ANCHOR_IDS}
        self.calibration: dict | None = None

    # ----- configuration -------------------------------------------------
    def set_config(self, config: dict) -> None:
        old, self.config = self.config, config
        if config["target_revision"] != old["target_revision"]:
            for track in self.tracks.values():
                track.reset_measurements()
            if self.calibration and self.calibration["state"] == "collecting":
                self._fail_calibration("The target changed during calibration.")
        if (config["smoothing"], config["freshness"]) != (old["smoothing"], old["freshness"]):
            for track in self.tracks.values():
                track.smoother = RssiSmoother.from_config(config)

    def anchor(self, anchor_id: str) -> dict:
        return next(a for a in self.config["anchors"] if a["id"] == anchor_id)

    # ----- anchor connection --------------------------------------------
    def anchor_connected(self, anchor_id: str, client: dict) -> None:
        track = self.tracks[anchor_id]
        track.reset_measurements()  # a new device may have different radio characteristics
        track.connected, track.client = True, client
        track.scanner, track.scanner_error, track.scanner_detail = "starting", None, None
        track.cfg_rev = 0

    def anchor_disconnected(self, anchor_id: str) -> None:
        track = self.tracks[anchor_id]
        track.connected = False
        track.scanner = "stopped"

    def update_status(self, anchor_id: str, status: dict) -> None:
        track = self.tracks[anchor_id]
        if status.get("scanner") in SCANNER_STATES:
            track.scanner = status["scanner"]
        track.scanner_error = _short_text(status.get("error"))
        track.scanner_detail = _short_text(status.get("detail"))
        if isinstance(status.get("adverts_seen"), int):
            track.adverts_seen = status["adverts_seen"]
        track.target_matched = bool(status.get("target_matched"))
        track.matched_address = _short_text(status.get("matched_address"), 64)
        if isinstance(status.get("cfg_rev"), int):
            track.cfg_rev = status["cfg_rev"]

    def set_cfg_rev(self, anchor_id: str, rev) -> None:
        if isinstance(rev, int):
            self.tracks[anchor_id].cfg_rev = rev

    # ----- samples -------------------------------------------------------
    def ingest_samples(self, anchor_id: str, cfg_rev, samples, recv_t: float | None = None) -> int:
        """Accept fresh RSSI readings that an anchor's scanner actually produced."""
        track = self.tracks[anchor_id]
        now = self.clock() if recv_t is None else recv_t
        stale_after = self.config["freshness"]["stale_after_s"]
        accepted = 0
        if not isinstance(samples, list):
            return 0
        for sample in samples:
            reason = None
            rssi = sample.get("rssi") if isinstance(sample, dict) else None
            age_ms = sample.get("age_ms") if isinstance(sample, dict) else None
            if not isinstance(cfg_rev, int) or cfg_rev < self.config["target_revision"]:
                reason = "sent for an older target configuration"
            elif not is_valid_rssi(rssi):
                reason = "invalid RSSI value"
            elif isinstance(age_ms, bool) or not isinstance(age_ms, (int, float)) or not 0 <= age_ms <= MAX_SAMPLE_AGE_MS:
                reason = "missing or invalid sample age"
            elif age_ms / 1000.0 > stale_after:
                reason = "arrived already stale"
            else:
                t = now - age_ms / 1000.0
                if track.last_sample_t is not None and t < track.last_sample_t:
                    reason = "older than the previous sample"
            if reason:
                track.rejected[reason] = track.rejected.get(reason, 0) + 1
                track.last_reject = reason
                continue
            track.rssi_raw = float(rssi)
            track.rssi_smoothed = track.smoother.update(float(rssi), t)
            track.last_sample_t = t
            track.sample_count += 1
            if isinstance(sample.get("addr"), str):
                track.matched_address = sample["addr"][:64]
            accepted += 1
            cal = self.calibration
            if cal and cal["state"] == "collecting" and cal["anchor_id"] == anchor_id:
                cal["samples"].append(float(rssi))
        return accepted

    def measurement_state(self, track: AnchorTrack, now: float) -> str:
        if not track.connected:
            return OFFLINE
        if track.scanner != "running":
            return SCANNER_STOPPED
        if track.last_sample_t is None or now - track.last_sample_t > self.config["freshness"]["forget_after_s"]:
            return TARGET_NOT_SEEN
        if now - track.last_sample_t > self.config["freshness"]["stale_after_s"]:
            return NO_FRESH_SAMPLE
        return FRESH

    # ----- views ---------------------------------------------------------
    def snapshot(self) -> dict:
        now = self.clock()
        self._tick_calibration(now)
        ranging = self.config["ranging"]
        target_z = self.config["target"]["height_m"]
        anchors, fresh = [], {}
        for anchor in self.config["anchors"]:
            track = self.tracks[anchor["id"]]
            state = self.measurement_state(track, now)
            view = {
                "id": anchor["id"],
                "label": anchor["label"],
                "connected": track.connected,
                "client": track.client,
                "scanner": track.scanner,
                "scanner_error": track.scanner_error,
                "scanner_detail": track.scanner_detail,
                "adverts_seen": track.adverts_seen,
                "target_matched": track.target_matched,
                "matched_address": track.matched_address,
                "measurement": state,
                "last_seen_age_s": None if track.last_sample_t is None else round(now - track.last_sample_t, 2),
                "last_rssi": track.rssi_raw,
                "rssi_raw": None,
                "rssi_smoothed": None,
                "distance_m": None,
                "horizontal_m": None,
                "sample_count": track.sample_count,
                "config_synced": track.connected and track.cfg_rev == self.config["revision"],
                "cfg_rev": track.cfg_rev,
                "last_reject": track.last_reject,
            }
            if state == FRESH:
                distance = rssi_to_distance(track.rssi_smoothed, anchor["rssi_at_1m"], ranging["path_loss_exponent"])
                horizontal = horizontal_distance(distance, anchor["z"] - target_z)
                view.update(rssi_raw=track.rssi_raw, rssi_smoothed=round(track.rssi_smoothed, 2),
                            distance_m=round(distance, 3), horizontal_m=round(horizontal, 3))
                fresh[anchor["id"]] = (anchor["x"], anchor["y"], horizontal)
            anchors.append(view)
        position, reason = self._position(anchors, fresh)
        return {
            "type": "state",
            "config_revision": self.config["revision"],
            "anchors": anchors,
            "position": position,
            "position_reason": reason,
            "calibration": self.calibration_view(now),
        }

    def _position(self, anchors: list, fresh: dict):
        waiting = [a for a in anchors if a["id"] not in fresh]
        if waiting:
            names = {OFFLINE: "not connected", SCANNER_STOPPED: "scanner stopped",
                     TARGET_NOT_SEEN: "target not seen", NO_FRESH_SAMPLE: "no fresh sample"}
            detail = ", ".join(f"{a['id']} {names[a['measurement']]}" for a in waiting)
            return None, f"No position: waiting for fresh measurements ({detail})."
        room = self.config["room"]
        try:
            est = trilaterate(list(fresh.values()), (room["width_m"], room["depth_m"]))
        except PositionError as exc:
            return None, f"No position: {exc}"
        return {"x": round(est.x, 3), "y": round(est.y, 3), "residual_m": round(est.residual_m, 3),
                "clamped": est.clamped, "anchors": sorted(fresh)}, None

    # ----- calibration ---------------------------------------------------
    def start_calibration(self, anchor_id: str, distance_m, duration_s=10.0) -> None:
        if anchor_id not in self.tracks:
            raise ValueError("Unknown anchor.")
        for value, lo, hi, label in ((distance_m, 0.2, 20, "Distance"), (duration_s, 3, 120, "Duration")):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{label} must be between {lo} and {hi}.")
        self.calibration = {
            "state": "collecting", "anchor_id": anchor_id, "distance_m": float(distance_m),
            "duration_s": float(duration_s), "started": self.clock(), "samples": [],
            "result": None, "error": None,
        }

    def cancel_calibration(self) -> None:
        self.calibration = None

    def calibration_result(self) -> dict:
        cal = self.calibration
        if not cal or cal["state"] != "done":
            raise ValueError("There is no finished calibration to apply.")
        return cal["result"]

    def mark_calibration_applied(self) -> None:
        if self.calibration:
            self.calibration["state"] = "applied"

    def _tick_calibration(self, now: float) -> None:
        cal = self.calibration
        if not cal or cal["state"] != "collecting" or now - cal["started"] < cal["duration_s"]:
            return
        samples = cal["samples"]
        needed = max(10, int(cal["duration_s"]))
        if len(samples) < needed:
            self._fail_calibration(
                f"Only {len(samples)} readings arrived from {cal['anchor_id']} (need {needed}). "
                "Check that its card shows 'Fresh measurement' and try again.")
            return
        median = statistics.median(samples)
        quartiles = statistics.quantiles(samples, n=4)
        n = self.config["ranging"]["path_loss_exponent"]
        cal["state"] = "done"
        cal["result"] = {
            "anchor_id": cal["anchor_id"],
            "median_rssi": round(median, 2),
            "spread_db": round(quartiles[2] - quartiles[0], 2),
            "sample_count": len(samples),
            "rssi_at_1m": round(calibrate_rssi_at_1m(median, cal["distance_m"], n), 1),
            "previous_rssi_at_1m": self.anchor(cal["anchor_id"])["rssi_at_1m"],
        }

    def _fail_calibration(self, message: str) -> None:
        self.calibration["state"] = "failed"
        self.calibration["error"] = message

    def calibration_view(self, now: float) -> dict | None:
        cal = self.calibration
        if not cal:
            return None
        samples = cal["samples"]
        return {
            "state": cal["state"], "anchor_id": cal["anchor_id"], "distance_m": cal["distance_m"],
            "duration_s": cal["duration_s"], "elapsed_s": round(min(now - cal["started"], cal["duration_s"]), 1),
            "sample_count": len(samples),
            "live_median": round(statistics.median(samples), 1) if samples else None,
            "result": cal["result"], "error": cal["error"],
        }


def _short_text(value, limit: int = 300):
    return value[:limit] if isinstance(value, str) and value else None
