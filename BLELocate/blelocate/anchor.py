"""Laptop anchor: finds P1, receives the shared config, scans and streams target RSSI."""

from __future__ import annotations

import asyncio
import logging
import socket
import statistics
import threading
import time
import urllib.parse
import webbrowser
from collections import deque

import aiohttp

from . import DEFAULT_DISCOVERY_PORT, DEFAULT_HTTP_PORT, __version__
from .discovery import discover_hubs
from .matching import TargetMatcher, is_valid_rssi
from .scanner import ScannerRunner, bleak_backend

log = logging.getLogger(__name__)

MAX_NEARBY = 1000
MAX_NEARBY_REPORT = 300
NEARBY_INTERVAL_S = 1.0


class AnchorStopped(Exception):
    """The hub told this anchor to stop (another device took its place)."""


def normalize_hub_url(value: str) -> str:
    """Accept '192.168.1.10', '192.168.1.10:8780' or a full http:// URL."""
    value = value.strip()
    parts = urllib.parse.urlsplit(value if "://" in value else "http://" + value)
    if not parts.hostname:
        raise ValueError(f"Not a P1 address: {value!r}")
    # shortcut: IPv4/hostnames only; IPv6 literals would need brackets here.
    return f"http://{parts.hostname}:{parts.port or DEFAULT_HTTP_PORT}"


def open_in_browser(url: str) -> None:
    # On Windows, ShellExecute may initialize COM as single-threaded on the calling
    # thread, which breaks Bleak's WinRT callbacks; keep it off the event-loop thread.
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()


class AnchorAgent:
    def __init__(self, hub_url: str | None = None, anchor_id: str | None = None, *,
                 discovery_port: int = DEFAULT_DISCOVERY_PORT, backend_factory=bleak_backend,
                 kind: str = "laptop", open_dashboard: bool = False, print_status: bool = True,
                 flush_interval: float = 0.1, status_interval: float = 1.0, retry_delay: float = 2.0,
                 nearby_interval: float = NEARBY_INTERVAL_S,
                 clock=time.monotonic):
        self.hub_url = normalize_hub_url(hub_url) if hub_url else None
        self.anchor_id = anchor_id
        self.discovery_port = discovery_port
        self.kind = kind
        self.open_dashboard = open_dashboard
        self.print_status = print_status
        self.flush_interval = flush_interval
        self.status_interval = status_interval
        self.nearby_interval = nearby_interval
        self.retry_delay = retry_delay
        self.clock = clock
        self.scanner = ScannerRunner(self.on_advert, backend_factory=backend_factory, clock=clock)
        self.matcher: TargetMatcher | None = None
        self.cfg_rev = 0
        self.connected = False
        self.adverts_seen = 0
        self.pending: deque = deque(maxlen=2000)
        self.nearby: dict[str, dict] = {}
        self.last_rssi: float | None = None
        self.last_address: str | None = None
        self.last_match_t: float | None = None
        self._dashboard_opened = False
        self._scanner_task: asyncio.Task | None = None

    # Called by the scanner for every advertisement callback.
    def on_advert(self, address: str, uuids, name, rssi, companies=()) -> None:
        self.adverts_seen += 1
        now = self.clock()
        self._note_nearby(address, uuids, name, rssi, companies, now)
        if self.matcher is None:
            return
        if self.matcher.observe(address, uuids, name, now) and is_valid_rssi(rssi):
            self.last_rssi, self.last_address, self.last_match_t = rssi, address, now
            if self.connected:  # nothing is buffered while P1 is unreachable
                self.pending.append((now, rssi, address))

    def _note_nearby(self, address, uuids, name, rssi, companies, now) -> None:
        """Remember every device heard, for the dashboard's device list."""
        entry = self.nearby.get(address)
        if entry is None:
            if len(self.nearby) >= MAX_NEARBY:
                return  # shortcut: busy places with rotating addresses lose new entries until old ones expire
            entry = self.nearby[address] = {"name": None, "uuids": set(), "companies": set(), "rssi": [], "t": now}
        if name and len(name) >= len(entry["name"] or ""):
            entry["name"] = name  # the complete name is longer than a shortened one; keep it
        entry["uuids"].update(list(uuids or ())[:8])
        entry["companies"].update(list(companies or ())[:8])
        if is_valid_rssi(rssi) and len(entry["rssi"]) < 50:
            entry["rssi"].append(rssi)
            entry["t"] = now

    def take_nearby(self) -> list[dict]:
        """One summary per device heard since the last call: median RSSI and its age."""
        now = self.clock()
        report = []
        for address, entry in list(self.nearby.items()):
            if entry["rssi"]:
                report.append({"addr": address, "name": entry["name"], "uuids": sorted(entry["uuids"])[:8],
                               "companies": sorted(entry["companies"])[:8], "rssi": statistics.median(entry["rssi"]),
                               "n": len(entry["rssi"]), "age_ms": round((now - entry["t"]) * 1000)})
                entry["rssi"] = []
            elif now - entry["t"] > 60:
                del self.nearby[address]
        report.sort(key=lambda d: -d["rssi"])
        return report[:MAX_NEARBY_REPORT]

    def apply_config(self, config: dict) -> None:
        target = config["target"]
        forget = config["freshness"]["forget_after_s"]
        ident = (target["service_uuid"], target["name"], forget, target.get("address", ""))
        if self.matcher is None:
            self.matcher = TargetMatcher(*ident)
        elif self.matcher.configure(*ident):
            self.pending.clear()
            self.last_rssi = self.last_address = self.last_match_t = None
            log.info("Target changed to %s; previous matches cleared.",
                     " ".join(v for v in (target["service_uuid"], target["name"], target.get("address", "")) if v))
        self.cfg_rev = config["revision"]

    def status_message(self) -> dict:
        now = self.clock()
        matched = self.matcher.matched_addresses(now) if self.matcher else []
        return {"type": "status", "scanner": self.scanner.state, "error": self.scanner.error,
                "detail": self.scanner.detail, "adverts_seen": self.adverts_seen,
                "target_matched": bool(matched), "matched_address": matched[-1] if matched else None,
                "cfg_rev": self.cfg_rev}

    def take_samples(self) -> list[dict]:
        now = self.clock()
        batch = [{"rssi": rssi, "age_ms": round((now - t) * 1000), "addr": addr} for t, rssi, addr in self.pending]
        self.pending.clear()
        return batch

    async def run(self) -> None:
        try:
            while True:
                url = self.hub_url or await self._discover()
                try:
                    await self._session(url)
                except AnchorStopped:
                    raise
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as exc:
                    log.warning("Lost connection to P1 at %s (%s). Retrying in %.0f s.", url, exc or type(exc).__name__, self.retry_delay)
                finally:
                    self.connected = False
                    self.pending.clear()
                    self.nearby.clear()
                await asyncio.sleep(self.retry_delay)
        finally:
            if self._scanner_task:
                self._scanner_task.cancel()
                await asyncio.gather(self._scanner_task, return_exceptions=True)

    async def _discover(self) -> str:
        announced = False
        while True:
            hubs = await discover_hubs(self.discovery_port)
            if hubs:
                if len(hubs) > 1:
                    log.warning("Several hubs answered: %s. Using %s; pass --hub to choose.",
                                ", ".join(h.url for h in hubs), hubs[0].url)
                log.info("Found P1 at %s", hubs[0].url)
                return hubs[0].url
            if not announced:
                log.info("Looking for P1 on this network... (no answer yet; see TROUBLESHOOTING.md if this "
                         "continues, or start with --hub <P1 address>)")
                announced = True
            await asyncio.sleep(1.0)

    async def _session(self, url: str) -> None:
        ws_url = url.replace("http://", "ws://", 1) + "/ws/anchor"
        timeout = aiohttp.ClientTimeout(total=None, connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(ws_url, heartbeat=5.0, max_msg_size=1 << 20) as ws:
                await ws.send_json({"type": "hello", "anchor_id": self.anchor_id, "kind": self.kind,
                                    "host": socket.gethostname(), "version": __version__})
                welcome = await ws.receive_json(timeout=10)
                if welcome.get("type") == "error":
                    raise AnchorStopped(welcome.get("message", "P1 refused this anchor."))
                if welcome.get("type") != "welcome":
                    raise ValueError(f"unexpected reply {welcome.get('type')!r}")
                self.anchor_id = welcome["anchor_id"]  # keep an auto-assigned role across reconnects
                self.apply_config(welcome["config"])
                await ws.send_json({"type": "config_ack", "rev": self.cfg_rev})
                self.connected = True
                log.info("Connected to P1 at %s as anchor %s.", url, self.anchor_id)
                if self._scanner_task is None:
                    self._scanner_task = asyncio.create_task(self.scanner.run())
                if self.open_dashboard and not self._dashboard_opened:
                    self._dashboard_opened = True
                    open_in_browser(url + "/")
                sender = asyncio.create_task(self._send_loop(ws))
                try:
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        data = msg.json()
                        if data.get("type") == "config":
                            self.apply_config(data["config"])
                            await ws.send_json({"type": "config_ack", "rev": self.cfg_rev})
                        elif data.get("type") == "replaced":
                            raise AnchorStopped(f"Another device ({data.get('by', 'unknown')}) is now anchor "
                                                f"{self.anchor_id}. This anchor has stopped.")
                finally:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
        raise ConnectionResetError("P1 closed the connection")

    async def _send_loop(self, ws) -> None:
        next_status = next_print = next_nearby = 0.0
        while True:
            samples = self.take_samples()
            if samples:
                await ws.send_json({"type": "samples", "cfg_rev": self.cfg_rev, "samples": samples})
            now = self.clock()
            if now >= next_status:
                await ws.send_json(self.status_message())
                next_status = now + self.status_interval
            if now >= next_nearby:
                nearby = self.take_nearby()
                if nearby:
                    await ws.send_json({"type": "nearby", "devices": nearby})
                next_nearby = now + self.nearby_interval
            if self.print_status and now >= next_print:
                log.info(self.console_line())
                next_print = now + 5.0
            await asyncio.sleep(self.flush_interval)

    def console_line(self) -> str:
        scanner = self.scanner.state + (f" ({self.scanner.error})" if self.scanner.error else "")
        if self.last_match_t is None:
            target = "target not seen yet"
        else:
            age = self.clock() - self.last_match_t
            target = f"target {self.last_address} RSSI {self.last_rssi} dBm, {age:.1f} s ago"
        return f"[{self.anchor_id}] scanner {scanner} | {self.adverts_seen} adverts heard | {target}"
