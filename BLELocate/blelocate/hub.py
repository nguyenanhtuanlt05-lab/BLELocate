"""P1: web host, dashboard API, anchor connections, config sync and LAN discovery."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import socket
import time
import uuid
from pathlib import Path

import segno
from aiohttp import WSMsgType, web

from . import APP_NAME, DEFAULT_DISCOVERY_PORT, DEFAULT_HTTP_PORT, __version__
from .anchor import AnchorAgent, AnchorStopped, open_in_browser
from .config import ANCHOR_IDS, ConfigConflict, ConfigInvalid, ConfigStore
from .devices import DeviceDirectory
from .discovery import build_reply, start_responder
from .engine import LocalizationEngine
from .netinfo import lan_ipv4_addresses
from .scanner import bleak_backend

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
# Explicit types: Windows' registry can map .js to text/plain, which browsers refuse for modules.
STATIC_FILES = {
    "index.html": "text/html",
    "style.css": "text/css",
    "app.js": "text/javascript",
    "matcher.js": "text/javascript",
    "support.js": "text/javascript",
    "bleanchor.js": "text/javascript",
    "devices.js": "text/javascript",
}


class Hub:
    def __init__(self, store: ConfigStore, *, port: int = DEFAULT_HTTP_PORT,
                 discovery_port: int | None = DEFAULT_DISCOVERY_PORT, bind: str = "0.0.0.0",
                 local_anchor: bool = True, open_browser: bool = False, backend_factory=bleak_backend,
                 state_interval: float = 0.25, devices_interval: float = 1.0, clock=time.monotonic):
        self.store = store
        self.engine = LocalizationEngine(store.config, clock)
        self.devices = DeviceDirectory(clock)
        self.port = port
        self.discovery_port = discovery_port
        self.bind = bind
        self.local_anchor = local_anchor
        self.open_browser = open_browser
        self.backend_factory = backend_factory
        self.state_interval = state_interval
        self.devices_interval = devices_interval
        self.hub_id = uuid.uuid4().hex[:8]
        self.hostname = socket.gethostname()
        self.anchors: dict[str, web.WebSocketResponse] = {}
        self.dashboards: set[web.WebSocketResponse] = set()
        self.discovery_error: str | None = None
        self._runner: web.AppRunner | None = None
        self._transport = None
        self._tasks: list[asyncio.Task] = []

    # ----- lifecycle -----------------------------------------------------
    def make_app(self) -> web.Application:
        app = web.Application()
        app.add_routes([
            web.get("/", self.static),
            web.get("/api/info", self.api_info),
            web.get("/api/config", self.api_get_config),
            web.put("/api/config", self.api_put_config),
            web.post("/api/calibration", self.api_calibration),
            web.get("/api/qr.svg", self.api_qr),
            web.get("/ws/dashboard", self.ws_dashboard),
            web.get("/ws/anchor", self.ws_anchor),
            web.get("/{name}", self.static),
        ])
        return app

    async def start(self) -> None:
        self._runner = web.AppRunner(self.make_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.bind, self.port)
        await site.start()
        if self.port == 0:  # tests ask the OS for a free port
            self.port = self._runner.addresses[0][1]
        if self.discovery_port is not None:
            try:
                self._transport = await start_responder(
                    lambda: build_reply(self.hub_id, self.hostname, self.port, __version__),
                    self.discovery_port, self.bind)
            except OSError as exc:
                self.discovery_error = (f"Automatic discovery is off: UDP port {self.discovery_port} is busy ({exc}). "
                                        "Anchors must be started with --hub <P1 address>.")
                log.warning(self.discovery_error)
        self._tasks.append(asyncio.create_task(self._broadcast_loop()))
        if self.local_anchor:
            self._tasks.append(asyncio.create_task(self._run_local_anchor()))
        if self.open_browser:
            open_in_browser(f"http://localhost:{self.port}/")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._transport:
            self._transport.close()
        for ws in list(self.anchors.values()) + list(self.dashboards):
            with contextlib.suppress(Exception):
                await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def _run_local_anchor(self) -> None:
        host = "127.0.0.1" if self.bind in ("0.0.0.0", "") else self.bind
        agent = AnchorAgent(f"http://{host}:{self.port}", "P1", backend_factory=self.backend_factory,
                            kind="laptop (hub)", print_status=False)
        try:
            await agent.run()
        except AnchorStopped as exc:
            log.warning("P1's own scanner stopped: %s", exc)

    # ----- pages and REST --------------------------------------------------
    async def static(self, request: web.Request) -> web.Response:
        name = request.match_info.get("name", "index.html")
        if name not in STATIC_FILES:
            raise web.HTTPNotFound()
        return web.Response(body=(WEB_DIR / name).read_bytes(), content_type=STATIC_FILES[name],
                            charset="utf-8", headers={"Cache-Control": "no-cache"})

    def info(self) -> dict:
        addresses = lan_ipv4_addresses()
        urls = [f"http://{a['ip']}:{self.port}/" for a in addresses]
        return {"app": APP_NAME, "version": __version__, "hub_id": self.hub_id, "hostname": self.hostname,
                "port": self.port, "discovery_port": self.discovery_port, "discovery_error": self.discovery_error,
                "addresses": addresses, "dashboard_urls": urls,
                "primary_url": urls[0] if urls else f"http://localhost:{self.port}/",
                "config_path": str(self.store.path)}

    async def api_info(self, request):
        return web.json_response(self.info())

    async def api_get_config(self, request):
        return web.json_response(self.store.config)

    async def api_put_config(self, request):
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"errors": ["Request body is not valid JSON."]}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"errors": ["Request body must be an object."]}, status=400)
        try:
            config = self.store.update(body.get("config"), body.get("base_revision"))
        except ConfigInvalid as exc:
            return web.json_response({"errors": exc.errors}, status=400)
        except ConfigConflict as exc:
            return web.json_response({"errors": [str(exc)], "config": exc.current}, status=409)
        await self.publish_config(config)
        return web.json_response(config)

    async def api_calibration(self, request):
        try:
            body = await request.json()
            action = body.get("action")
            if action == "start":
                self.engine.start_calibration(body.get("anchor_id"), body.get("distance_m"), body.get("duration_s", 10))
            elif action == "cancel":
                self.engine.cancel_calibration()
            elif action == "apply":
                result = self.engine.calibration_result()
                config = self.store.update_anchor(result["anchor_id"], rssi_at_1m=result["rssi_at_1m"])
                await self.publish_config(config)
                self.engine.mark_calibration_applied()
            else:
                raise ValueError("Unknown calibration action.")
        except (ValueError, AttributeError, ConfigInvalid) as exc:
            return web.json_response({"errors": [str(exc)]}, status=400)
        return web.json_response({"calibration": self.engine.calibration_view(self.engine.clock())})

    async def api_qr(self, request):
        data = request.query.get("data", "")
        if not data or len(data) > 300:
            raise web.HTTPBadRequest(text="data must be 1-300 characters")
        out = io.BytesIO()
        segno.make(data, error="m").save(out, kind="svg", scale=6, border=2, xmldecl=False,
                                         dark="#1f2b33", light="#ffffff")
        return web.Response(body=out.getvalue(), content_type="image/svg+xml")

    # ----- websockets ------------------------------------------------------
    async def ws_dashboard(self, request):
        ws = web.WebSocketResponse(heartbeat=10.0, max_msg_size=64 * 1024)
        await ws.prepare(request)
        self.dashboards.add(ws)
        try:
            await ws.send_json({"type": "config", "config": self.store.config})
            await ws.send_json(self.engine.snapshot())
            await ws.send_json(self.devices_message())
            async for _ in ws:
                pass  # dashboards edit through the REST API
        finally:
            self.dashboards.discard(ws)
        return ws

    async def ws_anchor(self, request):
        ws = web.WebSocketResponse(heartbeat=5.0, max_msg_size=256 * 1024)
        await ws.prepare(request)
        try:
            hello = await ws.receive_json(timeout=10)
        except (asyncio.TimeoutError, TypeError, ValueError):
            await ws.close()
            return ws
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await ws.close()
            return ws
        requested = hello.get("anchor_id") or None
        if requested is not None and requested not in ANCHOR_IDS:
            await ws.send_json({"type": "error", "message": f"Unknown anchor '{requested}'. Use P1, P2 or P3."})
            await ws.close()
            return ws
        anchor_id = requested or next((a for a in ANCHOR_IDS if a not in self.anchors), None)
        if anchor_id is None:
            await ws.send_json({"type": "error", "message": "P1, P2 and P3 are all connected. "
                                "Start this anchor with an explicit role (for example --anchor P3) to replace one."})
            await ws.close()
            return ws

        client = {"kind": str(hello.get("kind", "laptop"))[:32], "host": str(hello.get("host", ""))[:64],
                  "ip": request.remote, "version": str(hello.get("version", ""))[:16]}
        old = self.anchors.get(anchor_id)
        self.anchors[anchor_id] = ws
        if old is not None:
            log.info("%s: replaced by %s (%s)", anchor_id, client["host"], client["ip"])
            with contextlib.suppress(Exception):
                await old.send_json({"type": "replaced", "by": f"{client['host']} {client['ip']}".strip()})
                await old.close()
        self.engine.anchor_connected(anchor_id, client)
        self.devices.forget_anchor(anchor_id)
        log.info("%s connected: %s %s (%s)", anchor_id, client["kind"], client["host"], client["ip"])
        try:
            await ws.send_json({"type": "welcome", "anchor_id": anchor_id, "config": self.store.config})
            async for msg in ws:
                if self.anchors.get(anchor_id) is not ws:
                    break  # replaced: ignore anything still queued on the old link
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("type")
                if kind == "samples":
                    self.engine.ingest_samples(anchor_id, data.get("cfg_rev"), data.get("samples"))
                elif kind == "nearby":
                    self.devices.ingest(anchor_id, data.get("devices"))
                elif kind == "status":
                    self.engine.update_status(anchor_id, data)
                elif kind == "config_ack":
                    self.engine.set_cfg_rev(anchor_id, data.get("rev"))
        finally:
            if self.anchors.get(anchor_id) is ws:
                del self.anchors[anchor_id]
                self.engine.anchor_disconnected(anchor_id)
                log.info("%s disconnected", anchor_id)
        return ws

    async def publish_config(self, config: dict) -> None:
        self.engine.set_config(config)
        await self._send_all(list(self.anchors.values()) + list(self.dashboards),
                             json.dumps({"type": "config", "config": config}))

    def devices_message(self) -> dict:
        listing = [t.anchor_id for t in self.engine.tracks.values() if t.connected and t.client.get("kind") != "browser"]
        return {"type": "devices", "devices": self.devices.snapshot(self.store.config["target"], ANCHOR_IDS),
                "listing_anchors": listing}

    async def _broadcast_loop(self) -> None:
        next_devices = 0.0
        while True:
            await asyncio.sleep(self.state_interval)
            snapshot = self.engine.snapshot()  # also finishes due calibrations
            if self.dashboards:
                await self._send_all(list(self.dashboards), json.dumps(snapshot))
                if self.engine.clock() >= next_devices:
                    next_devices = self.engine.clock() + self.devices_interval
                    await self._send_all(list(self.dashboards), json.dumps(self.devices_message()))

    async def _send_all(self, sockets, text: str) -> None:
        for ws in sockets:
            try:
                await asyncio.wait_for(ws.send_str(text), timeout=2.0)
            except Exception:  # noqa: BLE001 - a dead client must not stall the others
                self.dashboards.discard(ws)
                with contextlib.suppress(Exception):
                    await ws.close()
