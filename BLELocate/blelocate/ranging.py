"""Log-distance path-loss model. Every distance it produces is an estimate."""

from __future__ import annotations

import math


def rssi_to_distance(rssi: float, rssi_at_1m: float, path_loss_exponent: float) -> float:
    return 10 ** ((rssi_at_1m - rssi) / (10.0 * path_loss_exponent))


def horizontal_distance(distance: float, height_difference: float) -> float:
    """Project a straight-line distance onto the floor plane."""
    return math.sqrt(max(distance * distance - height_difference * height_difference, 0.0))


def calibrate_rssi_at_1m(median_rssi: float, distance_m: float, path_loss_exponent: float) -> float:
    """RSSI@1 m that makes the model reproduce ``median_rssi`` at ``distance_m``."""
    return median_rssi + 10.0 * path_loss_exponent * math.log10(distance_m)
