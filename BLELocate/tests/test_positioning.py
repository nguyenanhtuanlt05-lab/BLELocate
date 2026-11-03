import math

import pytest

from blelocate.positioning import PositionError, trilaterate
from blelocate.ranging import calibrate_rssi_at_1m, horizontal_distance, rssi_to_distance

ANCHORS = [(0.0, 0.0), (6.0, 0.0), (3.0, 4.0)]


@pytest.mark.parametrize("truth", [(2.0, 1.5), (0.5, 3.5), (5.5, 0.4), (3.0, 2.0)])
def test_exact_distances_recover_position(truth):
    data = [(x, y, math.hypot(truth[0] - x, truth[1] - y)) for x, y in ANCHORS]
    est = trilaterate(data, (6.0, 4.0))
    assert (est.x, est.y) == pytest.approx(truth, abs=1e-6)
    assert est.residual_m < 1e-6 and not est.clamped


def test_noisy_distances_give_nearby_estimate_and_residual():
    truth = (2.0, 1.5)
    data = [(x, y, math.hypot(truth[0] - x, truth[1] - y) * f) for (x, y), f in zip(ANCHORS, (1.2, 0.85, 1.1))]
    est = trilaterate(data, (6.0, 4.0))
    assert math.hypot(est.x - truth[0], est.y - truth[1]) < 0.8
    assert est.residual_m > 0.01


def test_estimate_outside_room_is_clamped():
    data = [(0, 0, 9.0), (6, 0, 9.0), (3, 4, 1.0)]
    est = trilaterate(data, (6.0, 4.0))
    assert est.clamped and 0 <= est.x <= 6 and 0 <= est.y <= 4


def test_collinear_and_too_few_anchors_raise():
    with pytest.raises(PositionError):
        trilaterate([(0, 0, 1), (1, 0, 1), (2, 0, 1)])
    with pytest.raises(PositionError):
        trilaterate([(0, 0, 1), (1, 0, 1)])


def test_path_loss_round_trip_and_height_projection():
    assert rssi_to_distance(-59, -59, 2.0) == pytest.approx(1.0)
    assert rssi_to_distance(-79, -59, 2.0) == pytest.approx(10.0)
    p0 = calibrate_rssi_at_1m(-65, 2.0, 2.2)
    assert rssi_to_distance(-65, p0, 2.2) == pytest.approx(2.0)
    assert horizontal_distance(5.0, 3.0) == pytest.approx(4.0)
    assert horizontal_distance(0.2, 0.5) == 0.0  # closer than the height difference
