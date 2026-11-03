// A phone (or other Web Bluetooth browser) acting as an anchor. It speaks the
// same /ws/anchor protocol as the laptop app, so P1 treats both alike.
import { TargetMatcher, isValidRssi } from "./matcher.js";

export function explainScanError(err) {
  const name = err && err.name;
  if (name === "NotAllowedError")
    return "Bluetooth scanning was not allowed. Tap Start scanning again and choose Allow. If no prompt appears, check Chrome's site settings for this address (Nearby devices / Bluetooth) and Android's Location setting.";
  if (name === "NotFoundError") return "No Bluetooth adapter is available to Chrome. Turn Bluetooth on.";
  if (name === "InvalidStateError") return "Bluetooth is turned off. Turn it on and try again.";
  if (name === "SecurityError") return "Chrome does not allow Bluetooth on this page. See the setup steps above.";
  return `${name || "Error"}: ${(err && err.message) || err}`;
}

export class BrowserAnchor {
  constructor({ anchorId, onChange = () => {}, env = globalThis }) {
    this.anchorId = anchorId;
    this.onChange = onChange;
    this.env = env;
    this.state = "idle"; // idle | starting | running | paused | stopped
    this.error = null;
    this.detail = null;
    this.matcher = null;
    this.cfgRev = 0;
    this.pending = [];
    this.advertsSeen = 0;
    this.sent = 0;
    this.lastRssi = null;
    this.lastSeen = null;
    this.connected = false;
    this.closed = true;
    this.scan = null;
    this.ws = null;
    this.wakeLock = null;
    this.timer = null;
    this.nextStatus = 0;
    this.onAdvert = this.onAdvert.bind(this);
    this.onVisibility = this.onVisibility.bind(this);
  }

  now() {
    return this.env.performance.now() / 1000;
  }

  emit() {
    this.onChange(this);
  }

  /** Call directly from a tap/click handler: Chrome requires a user gesture to scan. */
  async start() {
    const nav = this.env.navigator;
    this.closed = false;
    this.state = "starting";
    this.error = null;
    this.emit();
    try {
      this.scan = await nav.bluetooth.requestLEScan({ acceptAllAdvertisements: true, keepRepeatedDevices: true });
    } catch (err) {
      this.state = "stopped";
      this.error = explainScanError(err);
      this.emit();
      return false;
    }
    nav.bluetooth.addEventListener("advertisementreceived", this.onAdvert);
    this.env.document.addEventListener("visibilitychange", this.onVisibility);
    this.state = "running";
    this.detail = null;
    try {
      this.wakeLock = nav.wakeLock ? await nav.wakeLock.request("screen") : null;
    } catch {
      this.wakeLock = null;
    }
    if (!this.ws) this.connect();
    if (!this.timer) this.timer = this.env.setInterval(() => this.tick(), 100);
    this.emit();
    return true;
  }

  stopScan(state, detail = null) {
    if (this.scan) {
      try {
        this.scan.stop();
      } catch {
        /* already stopped */
      }
      this.scan = null;
    }
    this.env.navigator.bluetooth?.removeEventListener("advertisementreceived", this.onAdvert);
    this.pending = [];
    this.state = state;
    this.detail = detail;
    this.sendStatus();
  }

  stop() {
    this.closed = true;
    this.stopScan("idle");
    this.env.document.removeEventListener("visibilitychange", this.onVisibility);
    if (this.timer) this.env.clearInterval(this.timer);
    this.timer = null;
    this.wakeLock?.release?.().catch(() => {});
    this.wakeLock = null;
    const ws = this.ws;
    this.ws = null;
    ws?.close();
    this.connected = false;
    this.emit();
  }

  onVisibility() {
    if (this.env.document.hidden && this.state === "running") {
      this.stopScan("paused", "Paused: Chrome stops Bluetooth scanning while this page is in the background or the screen is off.");
      this.emit();
    }
  }

  connect() {
    const loc = this.env.location;
    const ws = new this.env.WebSocket(`${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/ws/anchor`);
    this.ws = ws;
    ws.onopen = () =>
      ws.send(JSON.stringify({ type: "hello", anchor_id: this.anchorId, kind: "browser", host: shortAgent(this.env.navigator.userAgent), version: "web" }));
    ws.onmessage = (event) => {
      try {
        this.onMessage(JSON.parse(event.data));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.connected = false;
      this.pending = [];
      if (!this.closed) this.env.setTimeout(() => !this.closed && !this.ws && this.connect(), 2000);
      this.emit();
    };
  }

  onMessage(msg) {
    if (msg.type === "welcome") {
      this.anchorId = msg.anchor_id;
      this.connected = true;
      this.applyConfig(msg.config);
    } else if (msg.type === "config") {
      this.applyConfig(msg.config);
    } else if (msg.type === "replaced" || msg.type === "error") {
      const reason = msg.type === "replaced" ? `Another device (${msg.by}) is now anchor ${this.anchorId}.` : msg.message;
      this.stop();
      this.error = reason;
      this.state = "stopped";
    }
    this.emit();
  }

  applyConfig(config) {
    const t = config.target;
    const forget = config.freshness.forget_after_s;
    const ident = [t.service_uuid, t.name, forget, t.address || ""];
    if (!this.matcher) this.matcher = new TargetMatcher(...ident);
    else if (this.matcher.configure(...ident)) {
      this.pending = [];
      this.lastRssi = this.lastSeen = null;
    }
    this.cfgRev = config.revision;
    this.send({ type: "config_ack", rev: this.cfgRev });
  }

  onAdvert(event) {
    this.advertsSeen += 1;
    if (!this.matcher || this.state !== "running") return;
    const now = this.now();
    const uuids = [...(event.uuids || [])];
    if (event.serviceData && typeof event.serviceData.keys === "function") uuids.push(...event.serviceData.keys());
    const name = event.name || event.device?.name || null;
    const address = event.device?.id || "unknown";
    if (this.matcher.observe(address, uuids, name, now) && isValidRssi(event.rssi)) {
      this.lastRssi = event.rssi;
      this.lastSeen = now;
      if (this.connected) this.pending.push([now, event.rssi, address]);
    }
  }

  tick() {
    if (this.state === "running" && this.scan && this.scan.active === false) {
      this.stopScan("stopped", "Chrome ended the scan. Tap Start scanning again.");
    }
    if (this.pending.length && this.connected) {
      const now = this.now();
      const samples = this.pending.map(([t, rssi, addr]) => ({ rssi, age_ms: Math.round((now - t) * 1000), addr }));
      this.pending = [];
      this.sent += samples.length;
      this.send({ type: "samples", cfg_rev: this.cfgRev, samples });
    }
    if (this.now() >= this.nextStatus) {
      this.nextStatus = this.now() + 1;
      this.sendStatus();
      this.emit();
    }
  }

  sendStatus() {
    const now = this.now();
    const matched = this.matcher ? this.matcher.matchedAddresses(now) : [];
    const scanner = { running: "running", paused: "paused", starting: "starting" }[this.state] || "stopped";
    this.send({
      type: "status", scanner, error: this.error, detail: this.detail, adverts_seen: this.advertsSeen,
      target_matched: matched.length > 0, matched_address: matched.at(-1) || null, cfg_rev: this.cfgRev,
    });
  }

  send(msg) {
    if (this.ws && this.ws.readyState === 1 && this.connected) this.ws.send(JSON.stringify(msg));
  }
}

function shortAgent(ua = "") {
  const model = /Android[^;]*;\s*([^;)]+)/.exec(ua);
  return (model ? model[1] : "Browser").slice(0, 60);
}
