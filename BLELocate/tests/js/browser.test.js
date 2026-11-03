import { test } from "node:test";
import assert from "node:assert/strict";
import { detectBleSupport } from "../../blelocate/web/support.js";
import { BrowserAnchor } from "../../blelocate/web/bleanchor.js";

const TARGET = "12345678-1234-5678-1234-56789abcdef0";
const ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/141.0 Mobile Safari/537.36";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1";
const WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36";

const env = (userAgent, { secure = true, bluetooth } = {}) => ({
  isSecureContext: secure,
  location: { origin: "http://192.168.1.10:8780" },
  navigator: { userAgent, bluetooth },
});

test("Android Chrome with scanning enabled can scan", async () => {
  const bt = { requestLEScan() {}, getAvailability: async () => true };
  const r = await detectBleSupport(env(ANDROID, { bluetooth: bt }));
  assert.equal(r.canScan, true);
  assert.equal(r.problem, null);
});

test("plain HTTP explains the insecure-origin flag with this exact origin", async () => {
  const r = await detectBleSupport(env(ANDROID, { secure: false }));
  assert.equal(r.canScan, false);
  assert.match(r.problem.steps.join(" "), /unsafely-treat-insecure-origin-as-secure.*http:\/\/192\.168\.1\.10:8780/);
});

test("Android Chrome without the experimental flag is told to enable it", async () => {
  const r = await detectBleSupport(env(ANDROID, { bluetooth: { getAvailability: async () => true } }));
  assert.equal(r.canScan, false);
  assert.match(r.problem.steps[0], /enable-experimental-web-platform-features/);
});

test("iPhone is told to use a laptop instead", async () => {
  const r = await detectBleSupport(env(IPHONE, { secure: false }));
  assert.equal(r.canScan, false);
  assert.match(r.problem.title, /iPhone/);
  assert.match(r.problem.steps.join(" "), /laptop/);
});

test("desktop Chrome without scanning points to the laptop anchor app", async () => {
  const r = await detectBleSupport(env(WINDOWS, { bluetooth: { getAvailability: async () => true } }));
  assert.equal(r.canScan, false);
  assert.match(r.problem.steps.join(" "), /start_anchor\.bat/);
});

test("Bluetooth adapter reported missing", async () => {
  const bt = { requestLEScan() {}, getAvailability: async () => false };
  const r = await detectBleSupport(env(ANDROID, { bluetooth: bt }));
  assert.equal(r.canScan, false);
  assert.match(r.problem.title, /No Bluetooth adapter/);
});

// ---- BrowserAnchor against fake browser APIs --------------------------------
function fakeBrowser() {
  let now = 0;
  const listeners = {};
  const sockets = [];
  class FakeSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 1;
      this.sent = [];
      sockets.push(this);
    }
    send(text) {
      this.sent.push(JSON.parse(text));
    }
    close() {
      this.readyState = 3;
    }
  }
  const document = { hidden: false, listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; }, removeEventListener() {} };
  const scan = { active: true, stopped: false, stop() { this.active = false; this.stopped = true; } };
  const bluetooth = {
    requestLEScan: async () => scan,
    addEventListener: (type, fn) => { listeners[type] = fn; },
    removeEventListener: (type) => { delete listeners[type]; },
  };
  return {
    env: {
      navigator: { userAgent: ANDROID, bluetooth, wakeLock: { request: async () => ({ release: async () => {} }) } },
      document,
      location: { protocol: "http:", host: "192.168.1.10:8780" },
      performance: { now: () => now * 1000 },
      WebSocket: FakeSocket,
      setInterval: () => 1,
      clearInterval: () => {},
      setTimeout: () => 1,
    },
    advance: (s) => { now += s; },
    advert: (fields) => listeners.advertisementreceived && listeners.advertisementreceived(fields),
    sockets,
    scan,
    document,
  };
}

const CONFIG = { revision: 4, target: { service_uuid: TARGET, name: "" }, freshness: { forget_after_s: 30 } };

test("phone anchor sends only matched, fresh readings with their age", async () => {
  const b = fakeBrowser();
  const anchor = new BrowserAnchor({ anchorId: "P2", env: b.env });
  assert.equal(await anchor.start(), true);
  const ws = b.sockets[0];
  ws.onopen();
  assert.deepEqual(ws.sent[0].type, "hello");
  ws.onmessage({ data: JSON.stringify({ type: "welcome", anchor_id: "P2", config: CONFIG }) });
  assert.deepEqual(ws.sent.at(-1), { type: "config_ack", rev: 4 });

  b.advert({ device: { id: "dev1" }, uuids: [TARGET], rssi: -60 });
  b.advert({ device: { id: "dev1" }, uuids: [], name: "T", rssi: -62 }); // scan response, no UUID
  b.advert({ device: { id: "dev2" }, uuids: [], name: "Other", rssi: -40 });
  b.advert({ device: { id: "dev1" }, uuids: [], rssi: -127 }); // invalid RSSI
  b.advance(0.05);
  anchor.tick();
  const batch = ws.sent.find((m) => m.type === "samples");
  assert.equal(batch.cfg_rev, 4);
  assert.deepEqual(batch.samples.map((s) => s.rssi), [-60, -62]);
  assert.ok(batch.samples.every((s) => s.age_ms === 50 && s.addr === "dev1"));
  const status = ws.sent.find((m) => m.type === "status");
  assert.equal(status.scanner, "running");
  assert.equal(status.target_matched, true);
});

test("phone anchor pauses when hidden and resets matches on target change", async () => {
  const b = fakeBrowser();
  const anchor = new BrowserAnchor({ anchorId: "P3", env: b.env });
  await anchor.start();
  const ws = b.sockets[0];
  ws.onmessage({ data: JSON.stringify({ type: "welcome", anchor_id: "P3", config: CONFIG }) });
  b.advert({ device: { id: "dev1" }, uuids: [TARGET], rssi: -60 });

  ws.onmessage({ data: JSON.stringify({ type: "config", config: { ...CONFIG, revision: 5, target: { service_uuid: TARGET, name: "T-phone" } } }) });
  assert.equal(anchor.pending.length, 0);
  b.advert({ device: { id: "dev1" }, uuids: [], rssi: -60 });
  assert.equal(anchor.pending.length, 0, "old match must not survive a target change");

  b.document.hidden = true;
  b.document.listeners.visibilitychange();
  assert.equal(anchor.state, "paused");
  assert.equal(b.scan.stopped, true);
  assert.equal(ws.sent.at(-1).scanner, "paused");
  b.advert({ device: { id: "dev1" }, uuids: [TARGET], name: "T-phone", rssi: -60 });
  assert.equal(anchor.pending.length, 0, "no readings while paused");
});

test("phone anchor stops when another device takes its role", async () => {
  const b = fakeBrowser();
  const anchor = new BrowserAnchor({ anchorId: "P2", env: b.env });
  await anchor.start();
  const ws = b.sockets[0];
  ws.onmessage({ data: JSON.stringify({ type: "welcome", anchor_id: "P2", config: CONFIG }) });
  ws.onmessage({ data: JSON.stringify({ type: "replaced", by: "LAPTOP-2 192.168.1.23" }) });
  assert.equal(anchor.state, "stopped");
  assert.match(anchor.error, /LAPTOP-2/);
  assert.equal(ws.readyState, 3);
});

test("denied permission gives an actionable message", async () => {
  const b = fakeBrowser();
  b.env.navigator.bluetooth.requestLEScan = async () => {
    const err = new Error("User denied");
    err.name = "NotAllowedError";
    throw err;
  };
  const anchor = new BrowserAnchor({ anchorId: "P2", env: b.env });
  assert.equal(await anchor.start(), false);
  assert.match(anchor.error, /choose Allow/);
  assert.equal(b.sockets.length, 0);
});
