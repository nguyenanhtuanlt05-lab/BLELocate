import pytest

from blelocate.anchor import AnchorAgent
from blelocate.devices import DeviceDirectory, device_matches, short_maker
from fakes import TARGET, BackendFactory

ANCHORS = ("P1", "P2", "P3")
TARGET_CFG = {"service_uuid": TARGET, "name": "", "address": ""}


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def report(addr="AA:BB:CC:DD:EE:01", rssi=-70, n=3, age_ms=100, **extra):
    return {"addr": addr, "rssi": rssi, "n": n, "age_ms": age_ms, **extra}


def listed(directory, target=TARGET_CFG):
    return {d["address"]: d for d in directory.snapshot(target, ANCHORS)}


def test_device_is_listed_only_after_a_few_hits():
    directory = DeviceDirectory(Clock(), min_hits=3)
    directory.ingest("P1", [report(n=1)])
    directory.ingest("P1", [report(n=1)])
    assert listed(directory) == {}
    directory.ingest("P1", [report(n=1)])
    assert list(listed(directory)) == ["AA:BB:CC:DD:EE:01"]


def test_rssi_is_smoothed_per_anchor_and_names_keep_the_longest():
    clock = Clock()
    directory = DeviceDirectory(clock)
    directory.ingest("P1", [report(rssi=-70, name="Buds", companies=[0x004C])])
    clock.t += 1
    directory.ingest("P1", [report(rssi=-50, name="Galaxy Buds2")])
    clock.t += 1
    directory.ingest("P1", [report(rssi=-70, name="Buds")])  # shortened name arrives again
    d = listed(directory)["AA:BB:CC:DD:EE:01"]
    assert d["name"] == "Galaxy Buds2" and d["maker"] == "Apple"
    assert -66 <= d["rssi"]["P1"] <= -60  # a 20 dB jump moves the average only part of the way
    assert d["rssi"]["P2"] is None and d["best_rssi"] == d["rssi"]["P1"]


def test_old_values_are_not_shown_as_current_and_devices_are_forgotten():
    clock = Clock()
    directory = DeviceDirectory(clock, forget_after_s=120)
    directory.ingest("P1", [report()])
    directory.ingest("P2", [report(rssi=-80)])
    clock.t += 10
    directory.ingest("P1", [report(rssi=-72)])
    clock.t += 8  # P2 last heard 18 s ago, P1 8 s ago
    d = listed(directory)["AA:BB:CC:DD:EE:01"]
    assert d["rssi"]["P2"] is None and d["rssi"]["P1"] is not None
    assert d["last_seen_age_s"] == pytest.approx(8.1, abs=0.01)
    clock.t += 130
    assert listed(directory) == {}


@pytest.mark.parametrize("bad", [
    report(age_ms=6000),          # delayed: not a new reading
    report(rssi=-127),
    report(rssi=0),
    report(n=0),
    report(addr=""),
    report(addr=5),
    {"addr": "AA", "rssi": -60},  # no age, no count
    "junk",
])
def test_bad_reports_are_ignored(bad):
    directory = DeviceDirectory(Clock(), min_hits=1)
    assert directory.ingest("P1", [bad]) == 0
    assert directory.devices == {}


def test_reconnecting_anchor_starts_fresh():
    directory = DeviceDirectory(Clock(), min_hits=1)
    directory.ingest("P2", [report()])
    directory.forget_anchor("P2")
    assert listed(directory)["AA:BB:CC:DD:EE:01"]["rssi"]["P2"] is None


def test_target_marking_uses_the_same_rule_as_the_matcher():
    device = {"name": "Galaxy Buds2", "uuids": [TARGET]}
    addr = "AA:BB:CC:DD:EE:01"
    assert device_matches({"service_uuid": TARGET, "name": "", "address": ""}, addr, device)
    assert device_matches({"service_uuid": "", "name": "Galaxy Buds2", "address": ""}, addr, device)
    assert device_matches({"service_uuid": "", "name": "", "address": addr}, addr, device)
    assert not device_matches({"service_uuid": TARGET, "name": "Other", "address": ""}, addr, device)
    assert not device_matches({"service_uuid": "", "name": "", "address": "11:22:33:44:55:66"}, addr, device)


def test_short_maker_names():
    assert short_maker("Apple, Inc.") == "Apple"
    assert short_maker("Anhui Huami Information Technology Co., Ltd.") == "Anhui Huami"
    assert short_maker("HUAWEI Technologies Co., Ltd. ( )") == "HUAWEI"
    assert short_maker("Microsoft") == "Microsoft"
    assert short_maker(None) is None


def test_anchor_summarises_each_device_once_per_report():
    clock = Clock()
    agent = AnchorAgent("127.0.0.1", "P2", backend_factory=BackendFactory(), print_status=False, clock=clock)
    for rssi in (-60, -90, -62):  # one outlier
        agent.on_advert("AA:BB:CC:DD:EE:01", [], None, rssi, [0x004C])
    agent.on_advert("AA:BB:CC:DD:EE:01", [TARGET], "Buds", -127, [])  # invalid RSSI still teaches the name
    clock.t += 0.4
    [entry] = agent.take_nearby()
    assert entry == {"addr": "AA:BB:CC:DD:EE:01", "name": "Buds", "uuids": [TARGET], "companies": [0x004C],
                     "rssi": -62, "n": 3, "age_ms": 400}
    assert agent.take_nearby() == []  # nothing new heard: nothing re-sent as if new
