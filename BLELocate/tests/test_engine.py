import copy
import math

import pytest

from blelocate.config import DEFAULT_CONFIG
from blelocate.engine import (FRESH, NO_FRESH_SAMPLE, OFFLINE, SCANNER_STOPPED, TARGET_NOT_SEEN,
                              LocalizationEngine)
from blelocate.ranging import rssi_to_distance


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_engine(**overrides):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["smoothing"].update(median_window=1, kalman_enabled=False)
    for key, value in overrides.items():
        config[key].update(value)
    clock = Clock()
    return LocalizationEngine(config, clock), clock


def connect(engine, *ids):
    for aid in ids:
        engine.anchor_connected(aid, {"kind": "test"})
        engine.update_status(aid, {"scanner": "running", "cfg_rev": engine.config["revision"]})


def view(engine, aid):
    return next(a for a in engine.snapshot()["anchors"] if a["id"] == aid)


def test_measurement_states_progress_with_time():
    engine, clock = make_engine()
    assert view(engine, "P2")["measurement"] == OFFLINE
    engine.anchor_connected("P2", {})
    assert view(engine, "P2")["measurement"] == SCANNER_STOPPED  # connected, scanner still starting
    engine.update_status("P2", {"scanner": "running"})
    assert view(engine, "P2")["measurement"] == TARGET_NOT_SEEN
    engine.ingest_samples("P2", 1, [{"rssi": -60, "age_ms": 10}])
    v = view(engine, "P2")
    assert v["measurement"] == FRESH and v["rssi_raw"] == -60 and v["distance_m"] is not None
    clock.t += 3.5
    v = view(engine, "P2")
    assert v["measurement"] == NO_FRESH_SAMPLE
    assert v["rssi_raw"] is None and v["distance_m"] is None  # stale values are never shown as current
    assert v["last_rssi"] == -60 and v["last_seen_age_s"] == pytest.approx(3.51, abs=0.02)
    clock.t += 30
    assert view(engine, "P2")["measurement"] == TARGET_NOT_SEEN
    engine.update_status("P2", {"scanner": "stopped", "error": "Bluetooth is turned off."})
    v = view(engine, "P2")
    assert v["measurement"] == SCANNER_STOPPED and v["scanner_error"] == "Bluetooth is turned off."
    engine.anchor_disconnected("P2")
    assert view(engine, "P2")["measurement"] == OFFLINE


def test_status_heartbeats_do_not_refresh_readings():
    engine, clock = make_engine()
    connect(engine, "P2")
    engine.ingest_samples("P2", 1, [{"rssi": -60, "age_ms": 0}])
    for _ in range(10):
        clock.t += 1
        engine.update_status("P2", {"scanner": "running", "target_matched": True})
    assert view(engine, "P2")["measurement"] == NO_FRESH_SAMPLE
    assert view(engine, "P2")["sample_count"] == 1


@pytest.mark.parametrize("sample, reason", [
    ({"rssi": -60, "age_ms": 3500}, "arrived already stale"),
    ({"rssi": -127, "age_ms": 5}, "invalid RSSI value"),
    ({"rssi": -60}, "missing or invalid sample age"),
    ({"rssi": -60, "age_ms": -5}, "missing or invalid sample age"),
    ({"rssi": True, "age_ms": 5}, "invalid RSSI value"),
    ("junk", "invalid RSSI value"),
])
def test_bad_samples_are_rejected(sample, reason):
    engine, _ = make_engine()
    connect(engine, "P2")
    assert engine.ingest_samples("P2", 1, [sample]) == 0
    v = view(engine, "P2")
    assert v["sample_count"] == 0 and v["measurement"] == TARGET_NOT_SEEN and v["last_reject"] == reason


def test_out_of_order_sample_is_rejected():
    engine, _ = make_engine()
    connect(engine, "P2")
    assert engine.ingest_samples("P2", 1, [{"rssi": -60, "age_ms": 100}, {"rssi": -70, "age_ms": 500}]) == 1
    assert view(engine, "P2")["rssi_raw"] == -60


def test_samples_for_an_old_target_are_rejected():
    engine, _ = make_engine()
    connect(engine, "P2")
    config = copy.deepcopy(engine.config)
    config.update(revision=5, target_revision=5)
    config["target"]["name"] = "T-phone"
    engine.set_config(config)
    assert engine.ingest_samples("P2", 4, [{"rssi": -60, "age_ms": 0}]) == 0
    assert view(engine, "P2")["last_reject"] == "sent for an older target configuration"
    assert engine.ingest_samples("P2", 5, [{"rssi": -60, "age_ms": 0}]) == 1


def test_target_change_clears_previous_readings():
    engine, _ = make_engine()
    connect(engine, "P2")
    engine.ingest_samples("P2", 1, [{"rssi": -60, "age_ms": 0}])
    config = copy.deepcopy(engine.config)
    config.update(revision=2, target_revision=2)
    config["target"]["service_uuid"] = "0000180f-0000-1000-8000-00805f9b34fb"
    engine.set_config(config)
    v = view(engine, "P2")
    assert v["measurement"] == TARGET_NOT_SEEN and v["sample_count"] == 0 and v["last_rssi"] is None


def test_position_only_with_all_anchors_fresh():
    engine, clock = make_engine()
    truth = (2.0, 1.5)
    n = engine.config["ranging"]["path_loss_exponent"]
    connect(engine, "P1", "P2", "P3")
    rssi = {}
    for a in engine.config["anchors"]:
        d = math.sqrt((a["x"] - truth[0]) ** 2 + (a["y"] - truth[1]) ** 2 + (a["z"] - 1.0) ** 2)
        rssi[a["id"]] = a["rssi_at_1m"] - 10 * n * math.log10(d)
    engine.ingest_samples("P1", 1, [{"rssi": rssi["P1"], "age_ms": 0}])
    engine.ingest_samples("P2", 1, [{"rssi": rssi["P2"], "age_ms": 0}])
    snap = engine.snapshot()
    assert snap["position"] is None and "P3 target not seen" in snap["position_reason"]

    engine.ingest_samples("P3", 1, [{"rssi": rssi["P3"], "age_ms": 0}])
    snap = engine.snapshot()
    assert snap["position"]["x"] == pytest.approx(2.0, abs=1e-3)
    assert snap["position"]["y"] == pytest.approx(1.5, abs=1e-3)

    clock.t += 1.0
    engine.ingest_samples("P1", 1, [{"rssi": rssi["P1"], "age_ms": 0}])
    engine.ingest_samples("P2", 1, [{"rssi": rssi["P2"], "age_ms": 0}])
    clock.t += 2.5  # P3's only reading is now 3.5 s old
    snap = engine.snapshot()
    assert snap["position"] is None
    assert "P3 no fresh sample" in snap["position_reason"]


def test_collinear_anchors_give_no_position():
    engine, _ = make_engine()
    config = copy.deepcopy(engine.config)
    for a, x in zip(config["anchors"], (0.0, 3.0, 6.0)):
        a.update(x=x, y=0.0)
    engine.set_config(config)
    connect(engine, "P1", "P2", "P3")
    for aid in ("P1", "P2", "P3"):
        engine.ingest_samples(aid, 1, [{"rssi": -65, "age_ms": 0}])
    snap = engine.snapshot()
    assert snap["position"] is None and "one line" in snap["position_reason"]


def test_calibration_computes_rssi_at_1m():
    engine, clock = make_engine()
    connect(engine, "P2")
    engine.start_calibration("P2", 2.0, 10)
    for i in range(40):
        clock.t += 0.25
        engine.ingest_samples("P2", 1, [{"rssi": -66 + (i % 3) - 1, "age_ms": 0}])
    snap = engine.snapshot()
    cal = snap["calibration"]
    assert cal["state"] == "done"
    n = engine.config["ranging"]["path_loss_exponent"]
    assert cal["result"]["rssi_at_1m"] == pytest.approx(-66 + 10 * n * math.log10(2.0), abs=0.1)
    # Using the calibrated value, the model reproduces the known distance.
    assert rssi_to_distance(-66, cal["result"]["rssi_at_1m"], n) == pytest.approx(2.0, rel=0.02)


def test_calibration_fails_without_enough_readings():
    engine, clock = make_engine()
    connect(engine, "P2")
    engine.start_calibration("P2", 1.0, 10)
    engine.ingest_samples("P2", 1, [{"rssi": -60, "age_ms": 0}])
    clock.t += 11
    cal = engine.snapshot()["calibration"]
    assert cal["state"] == "failed" and "Only 1 readings" in cal["error"]


def test_calibration_rejects_bad_input():
    engine, _ = make_engine()
    with pytest.raises(ValueError):
        engine.start_calibration("P2", 0, 10)
    with pytest.raises(ValueError):
        engine.start_calibration("P9", 1, 10)
