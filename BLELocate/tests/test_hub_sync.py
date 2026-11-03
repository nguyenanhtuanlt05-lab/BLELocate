"""P1 hub + anchor agents over real HTTP/WebSocket connections, with fake scanners."""

import asyncio
import json

import aiohttp
import pytest

from blelocate.anchor import AnchorAgent, AnchorStopped, normalize_hub_url
from blelocate.config import ConfigStore
from blelocate.hub import Hub
from fakes import TARGET, BackendFactory, simulated_rssi, wait_for


def run(coro):
    return asyncio.run(coro)


async def start_hub(tmp_path, **kwargs):
    store = ConfigStore(tmp_path / "config.json")
    hub = Hub(store, port=0, discovery_port=None, bind="127.0.0.1", local_anchor=False, state_interval=0.05, **kwargs)
    await hub.start()
    return hub, f"http://127.0.0.1:{hub.port}"


def start_agent(url, anchor_id, factory):
    agent = AnchorAgent(url, anchor_id, backend_factory=factory, print_status=False,
                        flush_interval=0.02, status_interval=0.05, retry_delay=0.1)
    return agent, asyncio.create_task(agent.run())


async def stop(*tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_config_changes_reach_anchors_and_reset_matches(tmp_path):
    async def scenario():
        hub, url = await start_hub(tmp_path)
        factory = BackendFactory()
        agent, task = start_agent(url, "P2", factory)
        track = hub.engine.tracks["P2"]
        try:
            await wait_for(lambda: track.connected and track.scanner == "running", message="P2 running")
            assert track.cfg_rev == 1 and agent.matcher.service_uuid == TARGET

            # Partial advertisement: UUID first, then a scan response without it.
            factory.current.emit("AA:BB", [TARGET], None, -60)
            factory.current.emit("AA:BB", [], "T-phone", -62)
            factory.current.emit("CC:DD", [], "Other", -40)  # never matched: ignored
            await wait_for(lambda: track.sample_count == 2, message="two target readings")
            assert track.rssi_raw == -62 and track.matched_address == "AA:BB"

            async with aiohttp.ClientSession() as http:
                config = await (await http.get(f"{url}/api/config")).json()
                config["target"]["name"] = "T-phone"
                resp = await http.put(f"{url}/api/config", json={"config": config, "base_revision": 1})
                assert resp.status == 200
                saved = await resp.json()
                assert saved["revision"] == 2 and saved["target_revision"] == 2

                # A second editor still on revision 1 is refused instead of overwriting.
                resp = await http.put(f"{url}/api/config", json={"config": config, "base_revision": 1})
                assert resp.status == 409 and (await resp.json())["config"]["revision"] == 2

                bad = json.loads(json.dumps(saved))
                bad["target"]["service_uuid"] = "zzz"
                resp = await http.put(f"{url}/api/config", json={"config": bad, "base_revision": 2})
                assert resp.status == 400 and "UUID" in (await resp.json())["errors"][0]

            await wait_for(lambda: track.cfg_rev == 2, message="P2 acknowledged revision 2")
            assert agent.matcher.name == "T-phone"
            assert track.sample_count == 0  # readings for the old target are gone

            # The old match was cleared: a bare callback from AA:BB no longer counts.
            factory.current.emit("AA:BB", [], None, -60)
            await asyncio.sleep(0.15)
            assert track.sample_count == 0
            factory.current.emit("AA:BB", [TARGET], "T-phone", -58)
            await wait_for(lambda: track.sample_count == 1, message="reading under new target")
        finally:
            await stop(task)
            await hub.stop()
        return ConfigStore(tmp_path / "config.json").config

    persisted = run(scenario())
    assert persisted["revision"] == 2 and persisted["target"]["name"] == "T-phone"


def test_dashboard_receives_config_and_live_state(tmp_path):
    async def scenario():
        hub, url = await start_hub(tmp_path)
        try:
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(url.replace("http", "ws") + "/ws/dashboard") as ws:
                    first = await ws.receive_json(timeout=2)
                    second = await ws.receive_json(timeout=2)
                    assert first["type"] == "config" and first["config"]["revision"] == 1
                    assert second["type"] == "state" and [a["id"] for a in second["anchors"]] == ["P1", "P2", "P3"]
                    assert all(a["measurement"] == "offline" for a in second["anchors"])
                    assert second["position"] is None
                    info = await (await http.get(f"{url}/api/info")).json()
                    assert info["port"] == hub.port and info["primary_url"].startswith("http://")
                    qr = await http.get(f"{url}/api/qr.svg", params={"data": info["primary_url"]})
                    assert qr.status == 200 and qr.content_type == "image/svg+xml"
                    page = await http.get(f"{url}/app.js")
                    assert page.content_type == "text/javascript"
                    assert (await http.get(f"{url}/config.json")).status == 404
        finally:
            await hub.stop()

    run(scenario())


def test_three_anchors_produce_position_and_stale_anchor_removes_it(tmp_path):
    truth = (2.0, 1.5)

    async def scenario():
        hub, url = await start_hub(tmp_path)
        config = hub.store.config
        anchors = {a["id"]: a for a in config["anchors"]}
        factories, tasks = {}, []
        try:
            for aid in ("P1", "P2", "P3"):
                factories[aid] = BackendFactory()
                tasks.append(start_agent(url, aid, factories[aid])[1])
            await wait_for(lambda: all(t.scanner == "running" for t in hub.engine.tracks.values()), message="all running")
            for _ in range(10):
                for aid, factory in factories.items():
                    factory.current.emit(f"{aid}-addr", [TARGET], None, round(simulated_rssi(anchors[aid], truth)))
                await asyncio.sleep(0.02)
            await wait_for(lambda: hub.engine.snapshot()["position"] is not None, message="position")
            position = hub.engine.snapshot()["position"]

            # Jump 3.5 s ahead with no new callbacks: readings go stale and the position disappears.
            hub.engine.clock = lambda base=hub.engine.clock(): base + 3.5
            snap = hub.engine.snapshot()
            return position, snap
        finally:
            await stop(*tasks)
            await hub.stop()

    position, later = run(scenario())
    assert abs(position["x"] - truth[0]) < 0.3 and abs(position["y"] - truth[1]) < 0.3
    assert later["position"] is None and "no fresh sample" in later["position_reason"]


def test_second_device_takes_over_role_and_first_stops(tmp_path):
    async def scenario():
        hub, url = await start_hub(tmp_path)
        first, first_task = start_agent(url, "P3", BackendFactory())
        try:
            await wait_for(lambda: hub.engine.tracks["P3"].connected, message="first P3")
            second, second_task = start_agent(url, "P3", BackendFactory())
            with pytest.raises(AnchorStopped):
                await asyncio.wait_for(first_task, 3)
            await wait_for(lambda: hub.engine.tracks["P3"].connected and hub.anchors.get("P3") is not None, message="second P3")
            await stop(second_task)
            await wait_for(lambda: not hub.engine.tracks["P3"].connected, message="P3 offline after disconnect")
        finally:
            await stop(first_task)
            await hub.stop()

    run(scenario())


def test_auto_assigned_role_and_unknown_role(tmp_path):
    async def scenario():
        hub, url = await start_hub(tmp_path)
        agent, task = start_agent(url, None, BackendFactory())
        try:
            await wait_for(lambda: agent.anchor_id == "P1", message="auto role")
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(url.replace("http", "ws") + "/ws/anchor") as ws:
                    await ws.send_json({"type": "hello", "anchor_id": "P9"})
                    reply = await ws.receive_json(timeout=2)
                    assert reply["type"] == "error" and "Unknown anchor" in reply["message"]
        finally:
            await stop(task)
            await hub.stop()

    run(scenario())


def test_calibration_via_api_updates_shared_config(tmp_path):
    async def scenario():
        hub, url = await start_hub(tmp_path)
        factory = BackendFactory()
        agent, task = start_agent(url, "P2", factory)
        try:
            await wait_for(lambda: hub.engine.tracks["P2"].scanner == "running", message="P2 running")
            async with aiohttp.ClientSession() as http:
                resp = await http.post(f"{url}/api/calibration", json={"action": "start", "anchor_id": "P2", "distance_m": 2.0, "duration_s": 3})
                assert resp.status == 200
                for _ in range(40):
                    factory.current.emit("AA", [TARGET], None, -65)
                    await asyncio.sleep(0.03)
                await wait_for(lambda: (hub.engine.calibration or {}).get("state") == "done", timeout=6, message="calibration done")
                resp = await http.post(f"{url}/api/calibration", json={"action": "apply"})
                assert resp.status == 200
            await wait_for(lambda: agent.cfg_rev == 2, message="anchors got calibrated config")
        finally:
            await stop(task)
            await hub.stop()
        return hub.store.config

    config = run(scenario())
    p2 = next(a for a in config["anchors"] if a["id"] == "P2")
    assert p2["rssi_at_1m"] == pytest.approx(-65 + 10 * 2.2 * 0.30103, abs=0.1)


def test_samples_are_dropped_while_disconnected():
    agent = AnchorAgent("127.0.0.1", "P2", backend_factory=BackendFactory(), print_status=False)
    agent.apply_config({"revision": 1, "target": {"service_uuid": TARGET, "name": ""}, "freshness": {"forget_after_s": 30}})
    agent.on_advert("AA", [TARGET], None, -60)
    assert agent.take_samples() == []  # nothing is queued for later delivery as if it were new
    agent.connected = True
    agent.on_advert("AA", [], None, -61)
    samples = agent.take_samples()
    assert [s["rssi"] for s in samples] == [-61] and samples[0]["age_ms"] >= 0


@pytest.mark.parametrize("raw, url", [
    ("192.168.1.10", "http://192.168.1.10:8780"),
    ("192.168.1.10:9000", "http://192.168.1.10:9000"),
    ("http://p1-laptop:8780/", "http://p1-laptop:8780"),
])
def test_manual_hub_address_forms(raw, url):
    assert normalize_hub_url(raw) == url


def test_nearby_devices_reach_the_dashboard_and_can_be_tracked(tmp_path):
    async def scenario():
        hub, url = await start_hub(tmp_path)
        factory = BackendFactory()
        agent, task = start_agent(url, "P2", factory)
        agent.nearby_interval = 0.05
        try:
            await wait_for(lambda: hub.engine.tracks["P2"].scanner == "running", message="P2 running")
            for _ in range(6):
                factory.current.emit("11:22:33:44:55:66", [], "Galaxy Buds2", -71, companies=[0x0075])
                factory.current.emit("77:88:99:AA:BB:CC", [], None, -85)
                await asyncio.sleep(0.06)
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(url.replace("http", "ws") + "/ws/dashboard") as ws:
                    listing = None
                    while listing is None:
                        msg = await ws.receive_json(timeout=3)
                        if msg["type"] == "devices" and msg["devices"]:
                            listing = msg
                    buds = next(d for d in listing["devices"] if d["address"] == "11:22:33:44:55:66")
                    assert buds["name"] == "Galaxy Buds2" and buds["maker"] == "Samsung"
                    assert buds["rssi"]["P2"] == -71 and buds["rssi"]["P1"] is None
                    assert listing["listing_anchors"] == ["P2"] and not buds["is_target"]

                    # Track it by name, as the dashboard's Track button does.
                    config = await (await http.get(f"{url}/api/config")).json()
                    config["target"].update(service_uuid="", name="Galaxy Buds2", address="")
                    resp = await http.put(f"{url}/api/config", json={"config": config, "base_revision": 1})
                    assert resp.status == 200
            await wait_for(lambda: agent.matcher.name == "Galaxy Buds2" and not agent.matcher.service_uuid,
                           message="anchor got the new target")
            factory.current.emit("11:22:33:44:55:66", [], "Galaxy Buds2", -70)
            factory.current.emit("11:22:33:44:55:66", [], None, -69)  # later packet without the name
            await wait_for(lambda: hub.engine.tracks["P2"].sample_count == 2, message="readings of the picked device")
            listing = hub.devices_message()["devices"]
            assert next(d for d in listing if d["address"] == "11:22:33:44:55:66")["is_target"]
        finally:
            await stop(task)
            await hub.stop()

    run(scenario())
