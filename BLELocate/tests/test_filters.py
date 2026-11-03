import random
import statistics

from blelocate.filters import RollingMedian, RssiSmoother, TimeAwareKalman


def test_rolling_median_rejects_single_spike():
    median = RollingMedian(5)
    outputs = [median.update(v) for v in (-60, -61, -60, -30, -61)]
    assert outputs[-1] == -60  # the -30 spike does not get through


def test_kalman_first_value_passes_through_and_converges():
    kf = TimeAwareKalman(process_noise=1.0, measurement_noise=10.0)
    assert kf.update(-70, 0.0) == -70
    t = 0.0
    for _ in range(200):
        t += 0.1
        value = kf.update(-60, t)
    assert abs(value + 60) < 0.5


def test_kalman_gain_grows_with_elapsed_time():
    quick, slow = TimeAwareKalman(2.0, 10.0), TimeAwareKalman(2.0, 10.0)
    for kf in (quick, slow):
        kf.update(-60, 0.0)
        kf.update(-60, 0.1)
    quick_step = quick.update(-50, 0.2) - -60   # 0.1 s after the previous reading
    slow_step = slow.update(-50, 5.1) - -60     # 5 s after: more trust in the new reading
    assert 0 < quick_step < slow_step <= 10


def test_kalman_reduces_noise():
    rng = random.Random(1)
    kf = TimeAwareKalman(0.5, 16.0)
    raw, filtered = [], []
    for i in range(400):
        z = -65 + rng.gauss(0, 4)
        raw.append(z)
        filtered.append(kf.update(z, i * 0.1))
    assert statistics.pstdev(filtered[50:]) < statistics.pstdev(raw[50:]) / 2


def test_smoother_resets_after_gap():
    smoother = RssiSmoother(median_window=5, kalman_enabled=True, process_noise=0.1, measurement_noise=50, reset_gap_s=3.0)
    for i in range(20):
        smoother.update(-80, i * 0.1)
    # After a 10 s silence the first reading is taken as-is, not blended with -80.
    assert smoother.update(-50, 12.0) == -50


def test_smoothing_can_be_disabled():
    smoother = RssiSmoother(median_window=1, kalman_enabled=False)
    assert [smoother.update(v, i) for i, v in enumerate((-60, -40, -70))] == [-60, -40, -70]
