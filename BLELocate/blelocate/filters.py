"""RSSI smoothing: a short rolling median followed by a time-aware Kalman filter."""

from __future__ import annotations

import statistics
from collections import deque


class RollingMedian:
    def __init__(self, window: int):
        self._values: deque[float] = deque(maxlen=max(1, int(window)))

    def update(self, value: float) -> float:
        self._values.append(value)
        return statistics.median(self._values)

    def reset(self) -> None:
        self._values.clear()


class TimeAwareKalman:
    """1-D random-walk Kalman filter whose process noise grows with elapsed time.

    ``process_noise`` is in dBm^2 per second, so a reading after a long pause
    is trusted more than one arriving 10 ms after the previous reading.
    """

    def __init__(self, process_noise: float, measurement_noise: float):
        self.q = float(process_noise)
        self.r = float(measurement_noise)
        self.reset()

    def reset(self) -> None:
        self.x: float | None = None
        self.p = 0.0
        self.t: float | None = None

    def update(self, z: float, t: float) -> float:
        if self.x is None:
            self.x, self.p, self.t = float(z), self.r, t
            return self.x
        dt = max(0.0, t - self.t)
        self.p += self.q * dt
        gain = self.p / (self.p + self.r)
        self.x += gain * (z - self.x)
        self.p *= 1.0 - gain
        self.t = t
        return self.x


class RssiSmoother:
    def __init__(
        self,
        median_window: int = 5,
        kalman_enabled: bool = True,
        process_noise: float = 2.0,
        measurement_noise: float = 10.0,
        reset_gap_s: float = 3.0,
    ):
        self.median = RollingMedian(median_window)
        self.kalman = TimeAwareKalman(process_noise, measurement_noise) if kalman_enabled else None
        self.reset_gap_s = reset_gap_s
        self._last_t: float | None = None

    def update(self, rssi: float, t: float) -> float:
        # After a gap the old state describes a moment that no longer exists.
        if self._last_t is not None and t - self._last_t > self.reset_gap_s:
            self.reset()
        self._last_t = t
        value = self.median.update(rssi)
        return self.kalman.update(value, t) if self.kalman else value

    def reset(self) -> None:
        self.median.reset()
        if self.kalman:
            self.kalman.reset()
        self._last_t = None

    @classmethod
    def from_config(cls, config: dict) -> "RssiSmoother":
        s = config["smoothing"]
        return cls(
            median_window=s["median_window"],
            kalman_enabled=s["kalman_enabled"],
            process_noise=s["process_noise"],
            measurement_noise=s["measurement_noise"],
            reset_gap_s=config["freshness"]["stale_after_s"],
        )
