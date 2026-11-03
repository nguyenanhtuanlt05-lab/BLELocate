import { test } from "node:test";
import assert from "node:assert/strict";
import { DeviceList, DROP_GONE_AFTER_S, GONE_AFTER_S, signalBars, steady } from "../../blelocate/web/devices.js";

const dev = (address, best, extra = {}) => ({
  address, name: null, maker: null, uuids: [], rssi: { P1: best, P2: null, P3: null },
  best_rssi: best, last_seen_age_s: 0, is_target: false, ...extra,
});

test("signal bars hold their level near a boundary", () => {
  assert.equal(signalBars(-75), 2);
  assert.equal(signalBars(-69), 3);
  assert.equal(signalBars(-71, 3), 3, "1 dB below the -70 edge keeps 3 bars");
  assert.equal(signalBars(-74, 3), 2, "4 dB below drops to 2 bars");
  assert.equal(signalBars(-68, 2), 2, "2 dB above the edge keeps 2 bars");
  assert.equal(signalBars(-55, 2), 4, "a two-level jump is shown at once");
  assert.equal(signalBars(null, 3), 0);
});

test("numbers only change beyond the dead band", () => {
  assert.equal(steady(-71, -70), -70);
  assert.equal(steady(-72, -70), -72);
  assert.equal(steady(-60, undefined), -60);
  assert.equal(steady(null, -70), null);
});

test("rows keep their position while signals change", () => {
  const list = new DeviceList();
  list.update([dev("A", -80), dev("B", -60), dev("C", -70)], 0);
  assert.deepEqual(list.order, ["B", "C", "A"], "the first view is ordered by signal");
  for (let t = 1; t < 30; t++) {
    const wobble = (t % 2 ? 1 : -1) * 15;
    list.update([dev("A", -60 + wobble), dev("B", -85), dev("C", -70 - wobble)], t);
  }
  assert.deepEqual(list.order, ["B", "C", "A"]);
});

test("new devices are added at the bottom, strongest first", () => {
  const list = new DeviceList();
  list.update([dev("A", -80)], 0);
  list.update([dev("A", -80), dev("D", -90), dev("E", -50)], 1);
  assert.deepEqual(list.order, ["A", "E", "D"]);
});

test("quiet devices fade, stay in place, and are dropped much later", () => {
  const list = new DeviceList();
  list.update([dev("A", -60), dev("B", -70), dev("C", -80)], 0);
  list.update([dev("A", -60), dev("C", -80)], GONE_AFTER_S + 1); // B not reported any more
  assert.deepEqual(list.order, ["A", "B", "C"]);
  assert.equal(list.isGone("B", GONE_AFTER_S + 1), true);
  assert.equal(list.isGone("A", GONE_AFTER_S + 1), false);
  list.update([dev("A", -60), dev("C", -80)], DROP_GONE_AFTER_S + 1);
  assert.deepEqual(list.order, ["A", "C"]);
});

test("a device already gone when first reported is not added", () => {
  const list = new DeviceList();
  list.update([dev("A", null, { last_seen_age_s: GONE_AFTER_S + 5 })], 100);
  assert.deepEqual(list.order, []);
});

test("sort puts the tracked device first, then strongest, and drops gone rows", () => {
  const list = new DeviceList();
  list.update([dev("A", -80), dev("B", -60), dev("C", -70), dev("D", -65)], 0);
  list.update([dev("A", -80, { is_target: true }), dev("B", -60), dev("C", -70)], GONE_AFTER_S + 2);
  list.sort(GONE_AFTER_S + 2);
  assert.deepEqual(list.order, ["A", "B", "C"]);
  assert.equal(list.rows.has("D"), false);
});

test("pin moves one row to the top", () => {
  const list = new DeviceList();
  list.update([dev("A", -60), dev("B", -70)], 0);
  list.pin("B");
  assert.deepEqual(list.order, ["B", "A"]);
  list.pin("missing");
  assert.deepEqual(list.order, ["B", "A"]);
});
