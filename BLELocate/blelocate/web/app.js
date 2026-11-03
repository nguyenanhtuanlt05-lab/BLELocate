import { detectBleSupport } from "./support.js";
import { BrowserAnchor } from "./bleanchor.js";
import { DeviceList, GONE_AFTER_S } from "./devices.js";
import { trackIdentity } from "./matcher.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const MEASUREMENT = {
  fresh: { text: "Fresh measurement", tone: "fresh" },
  no_fresh_sample: { text: "No fresh sample", tone: "stale" },
  target_not_seen: { text: "Target not seen", tone: "stale" },
  scanner_stopped: { text: "Scanner stopped", tone: "stopped" },
  offline: { text: "Not connected to P1", tone: "offline" },
};

const app = {
  config: null,
  info: null,
  state: null,
  linkUp: false,
  everConnected: false,
  tab: "live",
  support: null,
  phone: null,
  settingsDirty: false,
  settingsNote: null,
  settingsErrors: [],
  pendingRender: false,
};

// ---------- helpers ----------------------------------------------------------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  return fill(el, attrs, children);
}

function s(tag, attrs = {}, ...children) {
  return fill(document.createElementNS(SVG_NS, tag), attrs, children);
}

function fill(el, attrs, children) {
  for (const [key, value] of Object.entries(attrs || {})) {
    const data = key.startsWith("data-");
    if (value === undefined || (!data && (value === null || value === false))) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "text") el.textContent = value;
    else el.setAttribute(key, value === true && !data ? "" : String(value)); // data-* keep "true"/"false"
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const $ = (id) => document.getElementById(id);

// Like el.replaceChildren(), but skips null/false (the DOM would print "null").
function replace(el, ...children) {
  el.replaceChildren();
  return fill(el, {}, children);
}

function num(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  return value.toFixed(digits).replace("-", "−");
}

function age(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 1) return "just now";
  if (seconds < 90) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s ago`;
  return `${Math.round(seconds / 60)} min ago`;
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  return { ok: response.ok, status: response.status, data };
}

// ---------- connection to P1 -------------------------------------------------
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/dashboard`);
  ws.onopen = () => {
    app.linkUp = true;
    app.everConnected = true;
    refreshInfo();
    renderLink();
  };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "config") onConfig(msg.config);
    else if (msg.type === "devices") onDevices(msg);
    else if (msg.type === "state") {
      app.state = msg;
      scheduleRender();
    }
  };
  ws.onclose = () => {
    app.linkUp = false;
    renderLink();
    scheduleRender();
    setTimeout(connect, 1500);
  };
}

async function refreshInfo() {
  const { ok, data } = await api("GET", "/api/info").catch(() => ({ ok: false }));
  if (ok) {
    app.info = data;
    renderConnect();
  }
}

function onConfig(config) {
  const changedElsewhere = app.config && config.revision !== app.config.revision;
  app.config = config;
  if (!app.settingsDirty) renderSettings();
  else if (changedElsewhere) {
    app.settingsNote = { tone: "warn", text: `Settings were changed on another device (revision ${config.revision}). Discard your edits to load them, or save to see the conflict.` };
    renderSettingsNote();
  }
  renderCalibration();
  scheduleRender();
}

function renderLink() {
  const el = $("hub-link");
  el.textContent = app.linkUp ? "Connected to P1" : app.everConnected ? "Reconnecting to P1…" : "Connecting to P1…";
  el.dataset.up = app.linkUp;
  const banner = $("banner");
  banner.hidden = app.linkUp || !app.everConnected;
  banner.textContent = "The connection to P1 was lost. The readings on this page are frozen, not live. Reconnecting…";
  document.body.classList.toggle("frozen", !app.linkUp);
  if (app.tab === "devices") renderDevices();
}

function scheduleRender() {
  if (app.pendingRender) return;
  app.pendingRender = true;
  requestAnimationFrame(() => {
    app.pendingRender = false;
    renderLive();
    renderCalibrationProgress();
  });
}

// ---------- live view ---------------------------------------------------------
function renderLive() {
  if (!app.config || !app.state) return;
  renderPlan();
  renderAnchors();
  renderReadout();
}

function renderReadout() {
  const { position, position_reason: reason } = app.state;
  const el = $("readout");
  el.replaceChildren();
  if (!app.linkUp) {
    el.append(h("strong", {}, "No live position."), " P1 is not reachable from this device.");
    el.dataset.tone = "offline";
    return;
  }
  if (position) {
    el.dataset.tone = "target";
    el.append(
      h("strong", {}, `Estimated position: x ${num(position.x, 2)} m, y ${num(position.y, 2)} m`),
      ` RSSI-based estimate; the distance circles disagree by about ${num(position.residual_m, 2)} m.`,
      position.clamped ? " The fit fell outside the room outline, so it is shown at the nearest wall." : "",
    );
  } else {
    el.dataset.tone = "stale";
    const why = reason.replace(/^No position: /, "");
    el.append(h("strong", {}, "No position estimate."), " ", why.charAt(0).toUpperCase() + why.slice(1));
  }
}

function renderPlan() {
  const svg = $("plan");
  const { room } = app.config;
  // Drawing units are centimetres: Chrome mis-sizes SVG text set in fractions of a unit.
  const U = 100;
  const W = room.width_m * U;
  const D = room.depth_m * U;
  const anchorCfg = app.config.anchors.map((a) => ({ ...a, x: a.x * U, y: a.y * U }));
  // Marker and text scale; on narrow screens grow it so the smallest label stays ≥ 13 px.
  const pxPerUnit = (svg.clientWidth || 800) / (W * 1.35);
  const k = Math.max(Math.max(W, D) / 8, 13 / (0.22 * pxPerUnit));
  const pad = 1.1 * k;
  svg.setAttribute("viewBox", `${-pad} ${-pad * 0.6} ${W + pad * 1.6} ${D + pad * 1.7}`);
  const Y = (y) => D - y;
  const live = app.linkUp;
  const views = new Map(app.state.anchors.map((a) => [a.id, a]));
  const parts = [];

  const metres = Math.max(room.width_m, room.depth_m);
  const step = (metres > 20 ? 5 : metres > 8 ? 2 : 1) * U;
  const grid = s("g", { class: "grid" });
  for (let x = step; x < W - 1e-6; x += step) grid.append(s("line", { x1: x, y1: 0, x2: x, y2: D }));
  for (let y = step; y < D - 1e-6; y += step) grid.append(s("line", { x1: 0, y1: Y(y), x2: W, y2: Y(y) }));
  parts.push(grid, s("rect", { class: "room", x: 0, y: 0, width: W, height: D }));

  // Dimension lines, as on a floor plan.
  const off = 0.45 * k;
  const tick = 0.12 * k;
  parts.push(
    s("g", { class: "dim" },
      s("line", { x1: 0, y1: D + off, x2: W, y2: D + off }),
      s("line", { x1: 0, y1: D + off - tick, x2: 0, y2: D + off + tick }),
      s("line", { x1: W, y1: D + off - tick, x2: W, y2: D + off + tick }),
      s("text", { x: W / 2, y: D + off - tick * 1.3, "font-size": 0.28 * k, "text-anchor": "middle" }, `${num(room.width_m, 2)} m`),
      s("line", { x1: -off, y1: 0, x2: -off, y2: D }),
      s("line", { x1: -off - tick, y1: 0, x2: -off + tick, y2: 0 }),
      s("line", { x1: -off - tick, y1: D, x2: -off + tick, y2: D }),
      s("text", { x: -off - tick * 1.3, y: D / 2, "font-size": 0.28 * k, "text-anchor": "middle", transform: `rotate(-90 ${-off - tick * 1.3} ${D / 2})` }, `${num(room.depth_m, 2)} m`),
      s("text", { x: 0, y: D + off + tick * 3.2, "font-size": 0.22 * k, "text-anchor": "middle", class: "origin" }, "0, 0"),
    ),
  );

  for (const a of anchorCfg) {
    const view = views.get(a.id);
    if (live && view && view.horizontal_m !== null) {
      parts.push(s("circle", { class: "ring", cx: a.x, cy: Y(a.y), r: view.horizontal_m * U }));
    }
  }
  for (const a of anchorCfg) {
    const view = views.get(a.id);
    const tone = live && view ? MEASUREMENT[view.measurement].tone : "offline";
    const r = 0.22 * k;
    const cy = Y(a.y);
    parts.push(
      s("g", { class: `anchor-mark tone-${tone}` },
        s("polygon", { points: `${a.x},${cy - r} ${a.x + r * 0.9},${cy + r * 0.6} ${a.x - r * 0.9},${cy + r * 0.6}` }),
        s("circle", { cx: a.x, cy, r: r * 0.16, class: "pin" }),
        s("text", { x: a.x, y: cy - r * 1.35, "font-size": 0.3 * k, "text-anchor": "middle", class: "anchor-id" }, a.id),
      ),
    );
  }

  const position = live ? app.state.position : null;
  if (position) {
    const x = position.x * U;
    const y = Y(position.y * U);
    const r = Math.max(position.residual_m * U, 0.15 * k);
    const c = 0.3 * k;
    parts.push(
      s("g", { class: "estimate" },
        s("circle", { class: "spread", cx: x, cy: y, r }),
        s("line", { x1: x - c, y1: y, x2: x + c, y2: y }),
        s("line", { x1: x, y1: y - c, x2: x, y2: y + c }),
        s("circle", { class: "dot", cx: x, cy: y, r: 0.07 * k }),
        s("text", { x: x + c * 0.5, y: y - c * 0.6, "font-size": 0.26 * k }, "T (estimate)"),
      ),
    );
  } else {
    parts.push(
      s("text", { x: W / 2, y: D / 2, "font-size": 0.3 * k, "text-anchor": "middle", class: "no-fix" }, live ? "No position estimate" : "Not live"),
    );
  }
  replace(svg, parts);
  svg.setAttribute("aria-label", position
    ? `Room plan. Estimated position of T: x ${num(position.x, 2)} m, y ${num(position.y, 2)} m.`
    : "Room plan. No position estimate.");
}

function renderAnchors() {
  const list = $("anchors");
  list.replaceChildren(...app.state.anchors.map(anchorCard));
}

function anchorCard(a) {
  const measurement = app.linkUp ? MEASUREMENT[a.measurement] : { text: "Unknown (P1 unreachable)", tone: "offline" };
  const fresh = a.measurement === "fresh" && app.linkUp;
  const client = a.client && a.client.kind ? `${a.client.kind}${a.client.host ? ", " + a.client.host : ""}${a.client.ip ? " (" + a.client.ip + ")" : ""}` : "–";
  let scanner = a.connected ? { running: "Running", starting: "Starting", paused: "Paused", stopped: "Stopped" }[a.scanner] : "–";
  if (a.connected && a.adverts_seen) scanner += `, ${a.adverts_seen.toLocaleString()} advertisements heard`;
  const target = !a.connected ? "–" : a.target_matched ? `Matched${a.matched_address ? " (" + a.matched_address + ")" : ""}` : "Not matched";
  const rssi = fresh
    ? `${num(a.rssi_raw, 0)} dBm raw, ${num(a.rssi_smoothed, 1)} dBm smoothed`
    : a.last_rssi !== null ? `Last ${num(a.last_rssi, 0)} dBm (not fresh)` : "–";
  const distance = fresh ? `${num(a.distance_m, 2)} m (${num(a.horizontal_m, 2)} m along the floor)` : "–";
  const rows = [
    ["Scanner", scanner],
    ["Target", target],
    ["Last seen", app.linkUp ? age(a.last_seen_age_s) : "unknown while P1 is unreachable"],
    ["RSSI", rssi],
    ["Samples", a.sample_count.toLocaleString()],
    ["Distance estimate", distance],
    ["Device", client],
    ["Settings", a.connected ? (a.config_synced ? `Up to date (revision ${a.cfg_rev})` : "Waiting for the anchor to confirm") : "–"],
  ];
  const notes = [];
  if (a.scanner_error) notes.push(h("p", { class: "note error" }, a.scanner_error));
  if (a.scanner_detail) notes.push(h("p", { class: "note" }, a.scanner_detail));
  if (a.last_reject) notes.push(h("p", { class: "note" }, `Last rejected reading: ${a.last_reject}.`));
  if (!a.connected) notes.push(h("p", { class: "note" }, a.id === "P1" ? "P1's own scanner is not connected. Check the P1 console window." : `Start the anchor app on ${a.id}, or open the Connect tab on a phone.`));
  return h("article", { class: `anchor tone-${measurement.tone}`, "aria-label": `${a.id} ${measurement.text}` },
    h("header", {},
      h("h3", {}, a.id),
      h("span", { class: "anchor-label" }, a.label !== a.id ? a.label : ""),
      h("span", { class: "link", "data-up": a.connected && app.linkUp },
        !app.linkUp ? "Link unknown" : a.connected ? "Connected to P1" : "Not connected to P1"),
    ),
    h("p", { class: "measurement" }, measurement.text),
    h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
    notes,
  );
}

// ---------- connect view ----------------------------------------------------
function renderConnect() {
  const info = app.info;
  const box = $("connect-info");
  if (!info) return;
  const url = info.primary_url;
  const others = info.dashboard_urls.filter((u) => u !== url);
  const host = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
  replace(box,
    h("h2", {}, "Open this dashboard on another device"),
    h("p", {}, "Join the same Wi-Fi as P1, then scan the code or type the address."),
    h("div", { class: "share" },
      h("img", { class: "qr", src: `/api/qr.svg?data=${encodeURIComponent(url)}`, width: 196, height: 196, alt: `QR code for ${url}` }),
      h("div", {},
        h("p", { class: "big-url" }, h("a", { href: url }, url)),
        others.length ? h("p", { class: "muted" }, "Other addresses of P1: ", others.join(", ")) : null,
        info.dashboard_urls.length ? null : h("p", { class: "note error" }, "P1 has no network address. Connect it to the Wi-Fi the anchors use."),
      ),
    ),
    h("h2", {}, "Add P2 and P3 laptops"),
    h("ol", { class: "steps" },
      h("li", {}, "Copy the BLE Locate folder to the laptop and connect it to the same Wi-Fi."),
      h("li", {}, "Run Start anchor.bat (start_anchor.bat when running from source) and type P2 or P3."),
      h("li", {}, "It finds P1 by itself. If it keeps searching, add P1's address when you start it: ", h("code", {}, `"Start anchor.bat" --hub ${host}`)),
    ),
    info.discovery_error ? h("p", { class: "note error" }, info.discovery_error) : null,
    h("p", { class: "muted" }, `Shared settings are stored on P1 in ${info.config_path}.`),
  );
}

async function renderPhonePanel() {
  const box = $("phone-anchor");
  if (!app.support) app.support = await detectBleSupport();
  const sup = app.support;
  const checks = h("ul", { class: "checks" }, sup.checks.map((c) =>
    h("li", { "data-ok": c.ok }, h("span", { class: "visually-hidden" }, c.ok === null ? "Unknown: " : c.ok ? "Yes: " : "No: "), c.label)));
  const content = [h("h2", {}, "Scan with this device"), checks];
  if (!sup.canScan) {
    content.push(
      h("p", { class: "lead" }, sup.problem.title),
      h("ol", { class: "steps" }, sup.problem.steps.map((t) => h("li", {}, t))),
      h("p", { class: "muted" }, "Laptops are the dependable option: the Windows anchor app scans continuously in the background."),
    );
  } else {
    content.push(...phoneControls());
  }
  replace(box, content);
}

function phoneControls() {
  const phone = app.phone;
  const ids = app.config ? app.config.anchors.map((a) => a.id) : ["P2", "P3"];
  if (!phone || phone.state === "idle" || (phone.state === "stopped" && !phone.ws)) {
    const select = h("select", { id: "phone-role" }, ids.map((id) => h("option", { value: id, selected: id === "P2" }, id)));
    return [
      h("p", {}, "Keep this page open with the screen on while scanning. Chrome pauses scanning in the background."),
      phone && phone.error ? h("p", { class: "note error" }, phone.error) : null,
      h("div", { class: "row" },
        h("label", { for: "phone-role" }, "This phone is anchor "), select,
        h("button", { type: "button", class: "primary", onclick: () => startPhone(select.value) }, "Start scanning"),
      ),
    ];
  }
  const ageText = phone.lastSeen === null ? "not seen yet" : age(phone.now() - phone.lastSeen);
  return [
    h("dl", {},
      h("dt", {}, "Anchor"), h("dd", {}, phone.anchorId || "–"),
      h("dt", {}, "Link"), h("dd", {}, phone.connected ? "Connected to P1" : "Connecting to P1…"),
      h("dt", {}, "Scanner"), h("dd", {}, { running: "Running", starting: "Starting", paused: "Paused", stopped: "Stopped" }[phone.state] || phone.state),
      h("dt", {}, "Target"), h("dd", {}, phone.lastRssi === null ? ageText : `${num(phone.lastRssi, 0)} dBm, ${ageText}`),
      h("dt", {}, "Sent"), h("dd", {}, `${phone.sent} readings, ${phone.advertsSeen} advertisements heard`),
      h("dt", {}, "Screen"), h("dd", {}, phone.wakeLock ? "Kept on while scanning" : "May turn off; keep it awake manually"),
    ),
    phone.error ? h("p", { class: "note error" }, phone.error) : null,
    phone.detail ? h("p", { class: "note" }, phone.detail) : null,
    h("div", { class: "row" },
      phone.state === "paused" || phone.state === "stopped"
        ? h("button", { type: "button", class: "primary", onclick: () => phone.start() }, "Resume scanning")
        : null,
      h("button", { type: "button", onclick: () => phone.stop() }, "Stop scanning"),
    ),
  ];
}

function startPhone(anchorId) {
  if (app.phone) app.phone.stop();
  app.phone = new BrowserAnchor({ anchorId, onChange: () => renderPhonePanel() });
  app.phone.start(); // inside the click handler, as Chrome requires
}

// ---------- nearby devices ------------------------------------------------------
const devices = { list: new DeviceList(), els: new Map(), listing: [], note: null, received: false };
const nowS = () => performance.now() / 1000;

function onDevices(msg) {
  devices.list.update(msg.devices, nowS());
  devices.listing = msg.listing_anchors;
  devices.received = true;
  if (app.tab === "devices") renderDevices();
}

function deviceLabel(d) {
  return d.name || (d.maker ? `Unnamed ${d.maker} device` : "Unnamed device");
}

function ageText(seconds) {
  if (seconds < 3) return "now";
  return seconds < 90 ? `${Math.round(seconds)} s ago` : `${Math.round(seconds / 60)} min ago`;
}

function deviceRow(address) {
  const bars = h("span", { class: "bars", "aria-hidden": "true" }, [1, 2, 3, 4].map(() => h("i")));
  const anchors = h("span", { class: "dev-anchors" });
  const parts = {
    name: h("span", { class: "dev-name" }),
    meta: h("span", { class: "dev-meta" }),
    signal: h("span", { class: "dev-signal" }, bars, h("span", { class: "dbm" })),
    anchors,
    seen: h("span", { class: "dev-seen" }),
    action: h("span", { class: "dev-action" }),
  };
  const li = h("li", { class: "device" },
    h("span", { class: "dev-id" }, parts.name, parts.meta), parts.signal, anchors, parts.seen, parts.action);
  const el = { li, parts, key: "" };
  devices.els.set(address, el);
  return el;
}

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function updateDeviceRow(el, address, row, now) {
  const d = row.device;
  const gone = devices.list.isGone(address, now);
  const age = now - row.heardAt;
  el.li.classList.toggle("gone", gone);
  el.li.classList.toggle("tracked", d.is_target);
  setText(el.parts.name, deviceLabel(d));
  el.parts.name.classList.toggle("unnamed", !d.name);
  setText(el.parts.meta, [address, d.name && d.maker ? d.maker : null].filter(Boolean).join("   "));
  const bars = gone ? 0 : row.bars;
  [...el.parts.signal.querySelectorAll("i")].forEach((bar, i) => bar.classList.toggle("on", i < bars));
  setText(el.parts.signal.querySelector(".dbm"), gone || row.best === null ? "–" : `${num(row.best, 0)} dBm`);
  el.parts.signal.setAttribute("aria-label", gone ? "No current signal" : `Signal ${bars} of 4, ${num(row.best, 0)} dBm`);
  const ids = app.config ? app.config.anchors.map((a) => a.id) : Object.keys(row.shown);
  const anchorKey = ids.map((id) => `${id}:${gone ? null : row.shown[id]}`).join(",");
  if (el.parts.anchors.dataset.key !== anchorKey) {
    el.parts.anchors.dataset.key = anchorKey;
    el.parts.anchors.replaceChildren(...ids.map((id) => {
      const v = gone ? null : row.shown[id];
      return h("span", { class: "chip", "data-heard": v !== null && v !== undefined }, `${id} `, h("b", {}, v === null || v === undefined ? "–" : num(v, 0)));
    }));
  }
  setText(el.parts.seen, gone ? `Last heard ${ageText(age)}` : ageText(age));
  // Rebuild the action only when it changes, so a button is never swapped under a finger.
  const actionKey = d.is_target ? "tracking" : gone ? "gone" : "track";
  if (el.key !== actionKey) {
    el.key = actionKey;
    el.parts.action.replaceChildren(
      actionKey === "tracking"
        ? h("span", { class: "tracking-label" }, "Tracking")
        : h("button", { type: "button", disabled: actionKey === "gone", onclick: () => trackDevice(address) }, "Track"),
    );
  }
}

function deviceVisible(row) {
  const d = row.device;
  if ($("device-named").checked && !d.name && !d.is_target) return false;
  const q = $("device-filter").value.trim().toLowerCase();
  return !q || [d.name, d.address, d.maker, ...d.uuids].some((v) => v && v.toLowerCase().includes(q));
}

function renderDevices() {
  const now = nowS();
  const list = $("device-list");
  const { order, rows } = devices.list;
  for (const [address, el] of devices.els) {
    if (!rows.has(address)) {
      el.li.remove();
      devices.els.delete(address);
    }
  }
  let shown = 0;
  let gone = 0;
  order.forEach((address, i) => {
    const row = rows.get(address);
    const el = devices.els.get(address) || deviceRow(address);
    updateDeviceRow(el, address, row, now);
    el.li.hidden = !deviceVisible(row);
    if (!el.li.hidden) shown += 1;
    if (devices.list.isGone(address, now)) gone += 1;
    if (list.children[i] !== el.li) list.insertBefore(el.li, list.children[i] || null); // moves only on reorder
  });

  const summary = $("device-summary");
  if (!app.linkUp) setText(summary, "P1 is not reachable, so this list is not updating.");
  else if (!devices.received) setText(summary, "Waiting for P1…");
  else if (!devices.listing.length) setText(summary, "No laptop anchor is connected. Laptop anchors (including P1's own scanner) list the devices they hear; phone anchors can't.");
  else if (!order.length) setText(summary, `Listening with ${devices.listing.join(", ")}. Devices appear after they've been heard a few times.`);
  else {
    const hidden = order.length - shown;
    setText(summary, `${order.length - gone} nearby, ${gone} not heard for ${GONE_AFTER_S} s${hidden ? `, ${hidden} hidden by the filter` : ""}. Listing from ${devices.listing.join(", ")}.`);
  }
  const note = $("device-note");
  note.replaceChildren();
  if (devices.note) note.append(h("p", { class: `note ${devices.note.tone}` }, devices.note.text));
}

async function trackDevice(address) {
  const row = devices.list.rows.get(address);
  if (!row || !app.config) return;
  const id = trackIdentity(row.device);
  const draft = structuredClone(app.config);
  Object.assign(draft.target, { service_uuid: id.service_uuid, name: id.name, address: id.address });
  const res = await api("PUT", "/api/config", { config: draft, base_revision: app.config.revision })
    .catch(() => ({ ok: false, status: 0, data: null }));
  if (res.ok) {
    app.config = res.data;
    devices.list.pin(address);
    row.device = { ...row.device, is_target: true };
    const label = deviceLabel(row.device);
    const caveat = {
      address: " Phones and many wearables change their address every few minutes, which ends tracking; phone anchors can't follow an address at all.",
      name: " Any device advertising exactly this name counts as the target.",
    }[id.by] || "";
    devices.note = { tone: "ok", text: `Tracking ${label} by ${id.by}. The Live tab now locates this device.${caveat}` };
  } else if (res.status === 409) {
    app.config = res.data.config;
    devices.note = { tone: "warn", text: "Settings were just changed on another device. Tap Track again." };
  } else {
    devices.note = { tone: "error", text: res.data && res.data.errors ? res.data.errors.join(" ") : "P1 could not be reached. Try again." };
  }
  renderDevices();
}

function initDevices() {
  $("device-filter").addEventListener("input", renderDevices);
  $("device-named").addEventListener("change", renderDevices);
  $("device-sort").addEventListener("click", () => {
    devices.list.sort(nowS());
    renderDevices();
  });
}

// ---------- settings ----------------------------------------------------------
const FIELDS = [
  { group: "Room", items: [
    { path: "room.width_m", label: "Width (x)", unit: "m", step: 0.1 },
    { path: "room.depth_m", label: "Depth (y)", unit: "m", step: 0.1 },
  ] },
  { group: "Target (T)", items: [
    { path: "target.service_uuid", label: "Service UUID (optional)", type: "text", wide: true },
    { path: "target.name", label: "Advertised name (optional, exact)", type: "text", wide: true },
    { path: "target.address", label: "Bluetooth address (optional, exact)", type: "text", wide: true, hint: "Fill in at least one of the three. Every one you fill in must match. Tip: pick a device on the Devices tab." },
    { path: "target.height_m", label: "Height above floor", unit: "m", step: 0.05 },
  ] },
  { group: "Distance model", items: [
    { path: "ranging.path_loss_exponent", label: "Path-loss exponent", step: 0.1, hint: "2 in open space, 2.5–4 indoors with obstacles" },
  ] },
  { group: "Smoothing", items: [
    { path: "smoothing.median_window", label: "Median window", unit: "readings", step: 1, int: true, hint: "1 turns the median off" },
    { path: "smoothing.kalman_enabled", label: "Kalman filter", type: "checkbox" },
    { path: "smoothing.process_noise", label: "Kalman process noise", unit: "dB²/s", step: 0.1, hint: "Higher follows movement faster" },
    { path: "smoothing.measurement_noise", label: "Kalman measurement noise", unit: "dB²", step: 0.5, hint: "Higher smooths more" },
  ] },
  { group: "Freshness", items: [
    { path: "freshness.stale_after_s", label: "Reading counts as fresh for", unit: "s", step: 0.5 },
    { path: "freshness.forget_after_s", label: "Forget the target after", unit: "s", step: 1 },
  ] },
];

const getPath = (obj, path) => path.split(".").reduce((o, k) => o[k], obj);
function setPath(obj, path, value) {
  const keys = path.split(".");
  keys.slice(0, -1).reduce((o, k) => o[k], obj)[keys.at(-1)] = value;
}

function input(item, value, id) {
  if (item.type === "checkbox") return h("input", { type: "checkbox", id, checked: value, "data-path": item.path });
  return h("input", {
    id, "data-path": item.path, value,
    type: item.type || "number", step: item.step, inputmode: item.type ? undefined : "decimal",
    required: !item.path.startsWith("target.") || item.path === "target.height_m", spellcheck: "false", autocomplete: "off",
    class: item.wide ? "wide" : undefined,
  });
}

function renderSettings() {
  const form = $("settings-form");
  if (!app.config) return;
  const cfg = app.config;
  const groups = FIELDS.map((group) =>
    h("fieldset", {},
      h("legend", {}, group.group),
      group.items.map((item) => {
        const id = `f-${item.path.replace(/\./g, "-")}`;
        return h("div", { class: `field${item.wide ? " wide" : ""}${item.type === "checkbox" ? " check" : ""}` },
          h("label", { for: id }, item.label),
          h("div", { class: "control" }, input(item, getPath(cfg, item.path), id), item.unit ? h("span", { class: "unit" }, item.unit) : null),
          item.hint ? h("p", { class: "hint" }, item.hint) : null,
        );
      }),
    ));
  const anchorRows = cfg.anchors.map((a, i) =>
    h("tr", {},
      h("th", { scope: "row" }, a.id),
      ["label", "x", "y", "z", "rssi_at_1m"].map((key) =>
        h("td", {}, h("input", {
          "aria-label": `${a.id} ${key}`, "data-anchor": i, "data-key": key, value: a[key],
          type: key === "label" ? "text" : "number", step: key === "rssi_at_1m" ? 0.5 : 0.05,
          inputmode: key === "label" ? undefined : "decimal", required: true,
        })))));
  const anchors = h("fieldset", {},
    h("legend", {}, "Anchors"),
    h("p", { class: "hint" }, "Coordinates in metres from the room's lower-left corner (0, 0). Height is the Bluetooth adapter's height above the floor."),
    h("div", { class: "table-wrap" },
      h("table", { class: "anchor-table" },
        h("thead", {}, h("tr", {}, ["Anchor", "Label", "x (m)", "y (m)", "Height (m)", "RSSI at 1 m (dBm)"].map((t) => h("th", { scope: "col" }, t)))),
        h("tbody", {}, anchorRows))));
  replace(form,
    h("div", { id: "settings-note", role: "status" }),
    groups[0], groups[1], anchors, ...groups.slice(2),
    h("div", { class: "row actions" },
      h("button", { type: "submit", class: "primary" }, "Save settings"),
      h("button", { type: "button", onclick: discardSettings }, "Discard changes"),
      h("button", { type: "button", onclick: exportSettings }, "Export settings"),
      h("label", { class: "button" }, "Import settings", h("input", { type: "file", accept: "application/json,.json", class: "visually-hidden", onchange: importSettings })),
    ),
    h("p", { class: "muted" }, `Revision ${cfg.revision}. Saved settings reach every anchor automatically.`),
  );
  app.settingsDirty = false;
  renderSettingsNote();
}

function renderSettingsNote() {
  const box = $("settings-note");
  if (!box) return;
  box.replaceChildren();
  if (app.settingsNote) box.append(h("p", { class: `note ${app.settingsNote.tone}` }, app.settingsNote.text));
  if (app.settingsErrors.length) box.append(h("ul", { class: "note error" }, app.settingsErrors.map((e) => h("li", {}, e))));
}

function readSettingsForm() {
  const draft = structuredClone(app.config);
  for (const el of $("settings-form").querySelectorAll("[data-path]")) {
    const item = FIELDS.flatMap((g) => g.items).find((f) => f.path === el.dataset.path);
    let value = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
    if (item.int) value = Math.round(value);
    setPath(draft, el.dataset.path, value);
  }
  for (const el of $("settings-form").querySelectorAll("[data-anchor]")) {
    const anchor = draft.anchors[Number(el.dataset.anchor)];
    anchor[el.dataset.key] = el.type === "number" ? Number(el.value) : el.value;
  }
  return draft;
}

async function saveSettings(draft) {
  const base = app.config.revision;
  const { ok, status, data } = await api("PUT", "/api/config", { config: draft, base_revision: base })
    .catch(() => ({ ok: false, status: 0, data: null }));
  app.settingsErrors = [];
  if (ok) {
    app.config = data;
    app.settingsNote = { tone: "ok", text: `Saved as revision ${data.revision}. Anchors are receiving it now.` };
    renderSettings();
    scheduleRender();
  } else if (status === 409) {
    app.config = data.config;
    app.settingsNote = { tone: "warn", text: "Someone else saved settings first. Their version is loaded below; make your change again." };
    renderSettings();
  } else {
    app.settingsErrors = data && data.errors ? data.errors : ["P1 could not be reached. Check the connection and save again."];
    app.settingsNote = null;
    renderSettingsNote();
  }
}

function discardSettings() {
  app.settingsNote = null;
  app.settingsErrors = [];
  renderSettings();
}

function exportSettings() {
  const blob = new Blob([JSON.stringify(app.config, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: `blelocate-config-r${app.config.revision}.json` });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function importSettings(event) {
  const file = event.target.files[0];
  if (!file) return;
  try {
    await saveSettings(JSON.parse(await file.text()));
  } catch {
    app.settingsErrors = ["That file is not a BLE Locate settings file (JSON)."];
    renderSettingsNote();
  }
  event.target.value = "";
}

// ---------- calibration -----------------------------------------------------
function renderCalibration() {
  const box = $("calibration");
  if (!app.config || box.dataset.built) return;
  box.dataset.built = "1";
  const ids = app.config.anchors.map((a) => a.id);
  box.append(
    h("h2", {}, "Calibrate the distance model"),
    h("p", {}, "Each laptop or phone hears T at a different strength. Calibrate every anchor once, with T at a distance you have measured."),
    h("ol", { class: "steps" },
      h("li", {}, "Pick the anchor and make sure its card says Fresh measurement."),
      h("li", {}, "Put T at a measured straight-line distance from that anchor's Bluetooth adapter, with a clear line of sight."),
      h("li", {}, "Start, then keep T and yourself still until the readings finish."),
      h("li", {}, "Apply the result. Repeat for the other anchors."),
    ),
    h("form", { id: "cal-form", class: "row" },
      h("label", { for: "cal-anchor" }, "Anchor"),
      h("select", { id: "cal-anchor" }, ids.map((id) => h("option", { value: id }, id))),
      h("label", { for: "cal-distance" }, "Distance"),
      h("span", { class: "control" }, h("input", { id: "cal-distance", type: "number", step: 0.05, min: 0.2, max: 20, value: 1.0, required: true, inputmode: "decimal" }), h("span", { class: "unit" }, "m")),
      h("label", { for: "cal-duration" }, "Collect for"),
      h("span", { class: "control" }, h("input", { id: "cal-duration", type: "number", step: 1, min: 3, max: 120, value: 15, required: true, inputmode: "numeric" }), h("span", { class: "unit" }, "s")),
      h("button", { type: "submit", class: "primary" }, "Start calibration"),
    ),
    h("div", { id: "cal-progress", "aria-live": "polite" }),
  );
  $("cal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const res = await api("POST", "/api/calibration", {
      action: "start", anchor_id: $("cal-anchor").value,
      distance_m: Number($("cal-distance").value), duration_s: Number($("cal-duration").value),
    });
    if (!res.ok) showCalError(res.data && res.data.errors ? res.data.errors.join(" ") : "P1 could not be reached.");
  });
}

function showCalError(text) {
  $("cal-progress").replaceChildren(h("p", { class: "note error" }, text));
}

function renderCalibrationProgress() {
  const box = $("cal-progress");
  if (!box || !app.state) return;
  const cal = app.state.calibration;
  const anchorId = $("cal-anchor").value;
  const view = app.state.anchors.find((a) => a.id === anchorId);
  const status = view ? MEASUREMENT[view.measurement] : null;
  const parts = [];
  if (status && (!cal || cal.state !== "collecting")) {
    parts.push(h("p", { class: `note tone-${status.tone}` }, `${anchorId} now: ${status.text}${view.rssi_raw !== null ? `, ${num(view.rssi_raw, 0)} dBm` : ""}.`));
  }
  if (cal && cal.state === "collecting") {
    parts.push(
      h("p", {}, `Collecting from ${cal.anchor_id} at ${num(cal.distance_m, 2)} m: ${cal.sample_count} readings${cal.live_median !== null ? `, median ${num(cal.live_median, 1)} dBm` : ""}.`),
      h("progress", { max: cal.duration_s, value: cal.elapsed_s }, `${cal.elapsed_s} s`),
      h("div", { class: "row" }, h("button", { type: "button", onclick: () => api("POST", "/api/calibration", { action: "cancel" }) }, "Cancel")),
    );
  } else if (cal && cal.state === "done") {
    const r = cal.result;
    parts.push(
      h("p", { class: "lead" }, `${r.anchor_id}: RSSI at 1 m is ${num(r.rssi_at_1m, 1)} dBm (now ${num(r.previous_rssi_at_1m, 1)} dBm).`),
      h("p", {}, `Median ${num(r.median_rssi, 1)} dBm at ${num(cal.distance_m, 2)} m from ${r.sample_count} readings; middle half within ${num(r.spread_db, 1)} dB.`),
      r.spread_db > 8 ? h("p", { class: "note warn" }, "The readings varied a lot. Check for people or objects between T and the anchor, then calibrate again.") : null,
      h("div", { class: "row" },
        h("button", { type: "button", class: "primary", onclick: async () => {
          const res = await api("POST", "/api/calibration", { action: "apply" });
          if (!res.ok) showCalError(res.data && res.data.errors ? res.data.errors.join(" ") : "Could not apply.");
        } }, `Apply to ${r.anchor_id}`),
        h("button", { type: "button", onclick: () => api("POST", "/api/calibration", { action: "cancel" }) }, "Discard"),
      ),
    );
  } else if (cal && cal.state === "failed") {
    parts.push(h("p", { class: "note error" }, cal.error));
  } else if (cal && cal.state === "applied") {
    parts.push(h("p", { class: "note ok" }, `Applied: ${cal.result.anchor_id} now uses ${num(cal.result.rssi_at_1m, 1)} dBm at 1 m.`));
  }
  replace(box, parts);
}

// ---------- tabs and start-up -------------------------------------------------
function showTab(name) {
  app.tab = name;
  for (const button of document.querySelectorAll("[role=tab]")) {
    const selected = button.dataset.tab === name;
    button.setAttribute("aria-selected", selected);
    button.tabIndex = selected ? 0 : -1;
  }
  for (const view of document.querySelectorAll("[role=tabpanel]")) view.hidden = view.id !== `view-${name}`;
  if (name === "connect") renderPhonePanel();
  if (name === "devices") renderDevices();
  if (history.replaceState) history.replaceState(null, "", `#${name}`);
}

function init() {
  const tabs = [...document.querySelectorAll("[role=tab]")];
  tabs.forEach((button, i) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      const delta = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!delta) return;
      const next = tabs[(i + delta + tabs.length) % tabs.length];
      next.focus();
      showTab(next.dataset.tab);
    });
  });
  const form = $("settings-form");
  form.addEventListener("input", () => {
    app.settingsDirty = true;
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSettings(readSettingsForm());
  });
  const tabFromHash = () => {
    const name = location.hash.slice(1);
    return tabs.some((b) => b.dataset.tab === name) ? name : "live";
  };
  window.addEventListener("hashchange", () => tabFromHash() !== app.tab && showTab(tabFromHash()));
  showTab(tabFromHash());
  initDevices();
  renderLink();
  connect();
}

init();
