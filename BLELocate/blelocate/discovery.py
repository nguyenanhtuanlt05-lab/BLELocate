"""LAN discovery: anchors broadcast a UDP probe, P1 answers with its HTTP port."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from . import DEFAULT_DISCOVERY_PORT
from .netinfo import broadcast_targets

log = logging.getLogger(__name__)

APP_TAG = "blelocate"
PROTOCOL_VERSION = 1
_MAX_DATAGRAM = 2048


@dataclass(frozen=True)
class HubInfo:
    ip: str
    port: int
    hub_id: str
    name: str
    version: str

    @property
    def url(self) -> str:
        return f"http://{self.ip}:{self.port}"


def build_probe() -> bytes:
    return json.dumps({"app": APP_TAG, "type": "discover", "v": PROTOCOL_VERSION}).encode()


def is_probe(data: bytes) -> bool:
    msg = _decode(data)
    return bool(msg) and msg.get("type") == "discover"


def build_reply(hub_id: str, name: str, port: int, version: str) -> bytes:
    return json.dumps({"app": APP_TAG, "type": "hub", "v": PROTOCOL_VERSION, "hub_id": hub_id,
                       "name": name, "port": port, "version": version}).encode()


def parse_reply(data: bytes, source_ip: str) -> HubInfo | None:
    msg = _decode(data)
    if not msg or msg.get("type") != "hub":
        return None
    port = msg.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    # The packet's source address is the one that is routable from here.
    return HubInfo(source_ip, port, str(msg.get("hub_id", ""))[:64], str(msg.get("name", ""))[:64],
                   str(msg.get("version", ""))[:32])


def _decode(data: bytes) -> dict | None:
    if len(data) > _MAX_DATAGRAM:
        return None
    try:
        msg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("app") != APP_TAG or msg.get("v") != PROTOCOL_VERSION:
        return None
    return msg


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, reply_fn):
        self.reply_fn = reply_fn
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if is_probe(data):
            self.transport.sendto(self.reply_fn(), addr)

    def error_received(self, exc):
        log.debug("Discovery socket error: %s", exc)


async def start_responder(reply_fn, port: int = DEFAULT_DISCOVERY_PORT, host: str = "0.0.0.0"):
    """Answer probes on UDP ``port``. ``reply_fn`` returns the reply bytes. Returns the transport."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: _Responder(reply_fn), local_addr=(host, port))
    return transport


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.hubs: dict[tuple[str, int], HubInfo] = {}

    def datagram_received(self, data, addr):
        hub = parse_reply(data, addr[0])
        if hub:
            self.hubs.setdefault((hub.ip, hub.port), hub)

    def error_received(self, exc):
        log.debug("Discovery probe error: %s", exc)


async def discover_hubs(port: int = DEFAULT_DISCOVERY_PORT, timeout: float = 1.5, targets=None) -> list[HubInfo]:
    """Probe the LAN and return every hub that answered within ``timeout``."""
    loop = asyncio.get_running_loop()
    transport, collector = await loop.create_datagram_endpoint(
        _Collector, local_addr=("0.0.0.0", 0), allow_broadcast=True)
    try:
        probe = build_probe()
        for target in targets or broadcast_targets():
            try:
                transport.sendto(probe, (target, port))
            except OSError as exc:  # e.g. an adapter without a route
                log.debug("Could not probe %s: %s", target, exc)
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return sorted(collector.hubs.values(), key=lambda h: (h.ip, h.port))
