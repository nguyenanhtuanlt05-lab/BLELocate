import copy
import json

import pytest

from blelocate.config import DEFAULT_CONFIG, ConfigConflict, ConfigInvalid, ConfigStore, validate_config


def test_defaults_are_valid_and_use_requested_uuid():
    config = validate_config(DEFAULT_CONFIG)
    assert config["target"]["service_uuid"] == "12345678-1234-5678-1234-56789abcdef0"
    assert [a["id"] for a in config["anchors"]] == ["P1", "P2", "P3"]


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["target"].update(service_uuid="nope"), "Target service UUID"),
    (lambda c: c["room"].update(width_m=0), "Room width"),
    (lambda c: c["anchors"].pop(), "Missing anchors: P3"),
    (lambda c: c["anchors"][1].update(id="P1"), "appears twice"),
    (lambda c: c["anchors"][0].update(x="3"), "P1 x must be a number"),
    (lambda c: c["smoothing"].update(median_window=2.5), "Median window"),
    (lambda c: c["smoothing"].update(kalman_enabled="yes"), "Kalman enabled"),
    (lambda c: c["freshness"].update(stale_after_s=10, forget_after_s=5), "Forget-after"),
    (lambda c: c["anchors"][0].update(rssi_at_1m=float("nan")), "RSSI at 1 m must be a number"),
    (lambda c: c["target"].update(service_uuid="", name="", address=""), "service UUID, a name or an address"),
    (lambda c: c["target"].update(address="AA:BB"), "Target address must look like"),
])
def test_invalid_configs_are_rejected_with_reasons(mutate, message):
    config = copy.deepcopy(DEFAULT_CONFIG)
    mutate(config)
    with pytest.raises(ConfigInvalid) as err:
        validate_config(config)
    assert any(message in e for e in err.value.errors), err.value.errors


def test_store_revisions_conflicts_and_target_revision(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    first = store.config
    assert first["revision"] == 1

    edit = copy.deepcopy(first)
    edit["room"]["width_m"] = 8.0
    second = store.update(edit, base_revision=1)
    assert second["revision"] == 2 and second["target_revision"] == 1  # target unchanged

    with pytest.raises(ConfigConflict) as conflict:
        store.update(edit, base_revision=1)  # stale editor
    assert conflict.value.current["revision"] == 2

    edit = store.config
    edit["target"]["name"] = "T-phone"
    third = store.update(edit, base_revision=2)
    assert third["revision"] == 3 and third["target_revision"] == 3


def test_store_persists_and_reloads(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    edit = store.config
    edit["anchors"][1]["x"] = 7.25
    store.update(edit, 1)
    reloaded = ConfigStore(path).config
    assert reloaded["anchors"][1]["x"] == 7.25 and reloaded["revision"] == 2


def test_unreadable_config_is_kept_aside_not_overwritten(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")
    store = ConfigStore(path)
    assert store.config["revision"] == 1
    kept = list(tmp_path.glob("config.json.unreadable-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "{broken"
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1


def test_update_anchor_used_by_calibration(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    config = store.update_anchor("P3", rssi_at_1m=-62.5)
    assert next(a for a in config["anchors"] if a["id"] == "P3")["rssi_at_1m"] == -62.5
    assert config["target_revision"] == 1


def test_target_by_name_or_address_and_old_configs_load(tmp_path):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["target"].update(service_uuid="", name="Galaxy Buds2", address="aa-bb-cc-dd-ee-ff")
    out = validate_config(config)
    assert out["target"]["service_uuid"] == "" and out["target"]["address"] == "AA:BB:CC:DD:EE:FF"

    # A config.json written before addresses existed still loads.
    old = copy.deepcopy(DEFAULT_CONFIG)
    del old["target"]["address"]
    (tmp_path / "config.json").write_text(json.dumps(old), encoding="utf-8")
    store = ConfigStore(tmp_path / "config.json")
    assert store.config["target"]["address"] == ""

    edit = store.config
    edit["target"]["address"] = "11:22:33:44:55:66"
    assert store.update(edit, store.config["revision"])["target_revision"] == 2  # address is part of the target
