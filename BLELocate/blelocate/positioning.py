"""2D least-squares trilateration from estimated horizontal distances."""

from __future__ import annotations

import math
from dataclasses import dataclass


class PositionError(Exception):
    pass


@dataclass
class PositionEstimate:
    x: float
    y: float
    residual_m: float  # RMS mismatch between the estimate and the distance circles
    clamped: bool


def trilaterate(anchors: list[tuple[float, float, float]], bounds: tuple[float, float] | None = None) -> PositionEstimate:
    """``anchors`` is [(x, y, horizontal_distance)]; ``bounds`` is (width, depth) of the room."""
    if len(anchors) < 3:
        raise PositionError("At least three anchors are needed.")
    if _collinear(anchors):
        raise PositionError("Anchor positions lie on one line; move one anchor off that line.")

    x, y = _linear_guess(anchors)
    for _ in range(50):
        # Gauss-Newton step on sum w_i (|p - a_i| - r_i)^2, w_i = 1 / r_i^2:
        # RSSI distance error grows with distance, so near anchors count more.
        a11 = a12 = a22 = b1 = b2 = 0.0
        for ax, ay, r in anchors:
            dx, dy = x - ax, y - ay
            d = math.hypot(dx, dy) or 1e-9
            jx, jy = dx / d, dy / d
            w = 1.0 / max(r, 0.5) ** 2
            f = d - r
            a11 += w * jx * jx
            a12 += w * jx * jy
            a22 += w * jy * jy
            b1 -= w * jx * f
            b2 -= w * jy * f
        a11 += 1e-9
        a22 += 1e-9
        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-15:
            break
        sx = (b1 * a22 - b2 * a12) / det
        sy = (a11 * b2 - a12 * b1) / det
        x, y = x + sx, y + sy
        if math.hypot(sx, sy) < 1e-7:
            break

    residual = math.sqrt(sum((math.hypot(x - ax, y - ay) - r) ** 2 for ax, ay, r in anchors) / len(anchors))
    clamped = False
    if bounds:
        cx, cy = min(max(x, 0.0), bounds[0]), min(max(y, 0.0), bounds[1])
        clamped = (cx, cy) != (x, y)
        x, y = cx, cy
    return PositionEstimate(x, y, residual, clamped)


def _collinear(anchors) -> bool:
    pts = [(a[0], a[1]) for a in anchors]
    span = max(math.hypot(p[0] - q[0], p[1] - q[1]) for p in pts for q in pts)
    if span < 1e-6:
        return True
    best = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            for k in range(j + 1, len(pts)):
                (x1, y1), (x2, y2), (x3, y3) = pts[i], pts[j], pts[k]
                best = max(best, abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)))
    return best / (span * span) < 1e-3


def _linear_guess(anchors) -> tuple[float, float]:
    """Subtract the first circle equation from the others and solve the linear system."""
    x0, y0, r0 = anchors[0]
    a11 = a12 = a22 = b1 = b2 = 0.0
    for xi, yi, ri in anchors[1:]:
        ax, ay = 2 * (xi - x0), 2 * (yi - y0)
        b = r0 * r0 - ri * ri + xi * xi - x0 * x0 + yi * yi - y0 * y0
        a11 += ax * ax
        a12 += ax * ay
        a22 += ay * ay
        b1 += ax * b
        b2 += ay * b
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-12:
        return (sum(a[0] for a in anchors) / len(anchors), sum(a[1] for a in anchors) / len(anchors))
    return ((b1 * a22 - b2 * a12) / det, (a11 * b2 - a12 * b1) / det)
