import json
from pathlib import Path

import pytest

from blelocate.matching import TargetMatcher, is_valid_rssi, normalize_address, normalize_uuid

VECTORS = json.loads((Path(__file__).parent / "vectors" / "matching.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("raw, expected", VECTORS["uuid_normalization"])
def test_uuid_normalization(raw, expected):
    if expected is None:
        with pytest.raises(ValueError):
            normalize_uuid(raw)
    else:
        assert normalize_uuid(raw) == expected


@pytest.mark.parametrize("raw, expected", VECTORS["address_normalization"])
def test_address_normalization(raw, expected):
    if expected is None:
        with pytest.raises(ValueError):
            normalize_address(raw)
    else:
        assert normalize_address(raw) == expected


def test_empty_target_is_refused():
    with pytest.raises(ValueError):
        TargetMatcher("", "", 30, "")


@pytest.mark.parametrize("rssi, expected", VECTORS["rssi_validity"])
def test_rssi_validity(rssi, expected):
    assert is_valid_rssi(rssi) is expected


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_matching_vectors(case):
    t = case["target"]
    matcher = TargetMatcher(t["service_uuid"], t["name"], t["forget_after_s"], t.get("address", ""))
    for i, step in enumerate(case["steps"]):
        if "configure" in step:
            c = step["configure"]
            assert matcher.configure(c["service_uuid"], c["name"], c["forget_after_s"], c.get("address", "")) is step["changed"], f"step {i}"
            continue
        matched = matcher.observe(step["addr"], step["uuids"], step["name"], step["t"])
        assert (matched and is_valid_rssi(step["rssi"])) is step["accept"], f"step {i}: {step}"


def test_unrelated_devices_are_not_remembered():
    matcher = TargetMatcher("12345678-1234-5678-1234-56789abcdef0")
    for i in range(1000):
        matcher.observe(f"addr-{i}", ["0000180f-0000-1000-8000-00805f9b34fb"], f"dev{i}", 0.0)
    assert matcher._evidence == {}


def test_matched_addresses_reports_current_matches():
    matcher = TargetMatcher("12345678-1234-5678-1234-56789abcdef0", forget_after_s=5)
    matcher.observe("AA", ["12345678-1234-5678-1234-56789abcdef0"], None, 0.0)
    assert matcher.matched_addresses(1.0) == ["AA"]
    assert matcher.matched_addresses(6.5) == []


def test_malformed_uuids_in_adverts_are_ignored():
    matcher = TargetMatcher("12345678-1234-5678-1234-56789abcdef0")
    assert matcher.observe("AA", ["garbage", None, 42], None, 0.0) is False
