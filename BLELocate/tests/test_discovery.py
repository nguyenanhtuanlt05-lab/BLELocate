import asyncio
import ipaddress
import json
import socket

from blelocate.discovery import (build_probe, build_reply, discover_hubs, is_probe, parse_reply,
                                 start_responder)
from blelocate.netinfo import broadcast_targets


def test_probe_and_reply_round_trip():
    assert is_probe(build_probe())
    hub = parse_reply(build_reply("abc123", "P1-laptop", 8780, "1.0.0"), "192.168.1.10")
    assert hub.url == "http://192.168.1.10:8780" and hub.hub_id == "abc123" and hub.name == "P1-laptop"


def test_foreign_and_malformed_packets_are_ignored():
    assert not is_probe(b"\xff\xfe junk")
    assert not is_probe(json.dumps({"app": "other", "type": "discover", "v": 1}).encode())
    assert not is_probe(json.dumps({"app": "blelocate", "type": "discover", "v": 99}).encode())
    assert not is_probe(b"x" * 5000)
    for port in (0, 70000, "8780", True, None):
        bad = json.dumps({"app": "blelocate", "type": "hub", "v": 1, "port": port}).encode()
        assert parse_reply(bad, "10.0.0.1") is None


def test_responder_answers_probe_and_client_finds_hub():
    async def scenario():
        transport = await start_responder(lambda: build_reply("hub1", "P1", 8780, "1.0.0"), port=0, host="127.0.0.1")
        port = transport.get_extra_info("sockname")[1]
        try:
            hubs = await discover_hubs(port=port, timeout=0.3, targets=["127.0.0.1"])
        finally:
            transport.close()
        return hubs

    hubs = asyncio.run(scenario())
    assert len(hubs) == 1
    assert (hubs[0].ip, hubs[0].port, hubs[0].hub_id) == ("127.0.0.1", 8780, "hub1")


def test_responder_stays_silent_for_junk():
    async def scenario():
        transport = await start_responder(lambda: build_reply("hub1", "P1", 8780, "1.0.0"), port=0, host="127.0.0.1")
        port = transport.get_extra_info("sockname")[1]
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.sendto(b"hello?", ("127.0.0.1", port))
            try:
                await asyncio.wait_for(loop.sock_recv(sock, 2048), 0.3)
                return "answered"
            except asyncio.TimeoutError:
                return "silent"
        finally:
            sock.close()
            transport.close()

    assert asyncio.run(scenario()) == "silent"


def test_no_hub_means_empty_result():
    async def scenario():
        free = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        return await discover_hubs(port=port, timeout=0.2, targets=["127.0.0.1"])

    assert asyncio.run(scenario()) == []


def test_broadcast_targets_are_ipv4_broadcasts():
    targets = broadcast_targets()
    assert targets[0] == "255.255.255.255"
    for target in targets:
        ipaddress.IPv4Address(target)
