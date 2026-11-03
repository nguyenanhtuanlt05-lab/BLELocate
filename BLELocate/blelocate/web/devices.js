// The nearby-device list, kept still while BLE underneath it is not.
// - Rows never move on their own: new devices are added at the bottom (strongest first),
//   and only Sort or Track reorders.
// - Quiet devices fade ("gone") instead of vanishing; long-gone rows are dropped.
// - Numbers change only when they move by more than a dead band, and signal bars
//   only when the signal is clearly past a level boundary.
// No DOM here, so tests/js/devices.test.js can drive it.

export const GONE_AFTER_S = 15;
export const DROP_GONE_AFTER_S = 300;
const BAR_EDGES = [-90, -80, -70, -60]; // dBm needed for 1, 2, 3, 4 bars
const BAR_HYSTERESIS_DB = 3;
const DEAD_BAND_DB = 2;

/** 0-4 bars; within 3 dB of a boundary the previous level is kept, so bars don't flicker. */
export function signalBars(rssi, previous) {
  if (rssi === null || rssi === undefined) return 0;
  let level = BAR_EDGES.filter((edge) => rssi >= edge).length;
  if (previous !== undefined && Math.abs(level - previous) === 1) {
    const edge = BAR_EDGES[Math.max(level, previous) - 1];
    if (Math.abs(rssi - edge) < BAR_HYSTERESIS_DB) level = previous;
  }
  return level;
}

/** Keep showing `shown` until `value` differs from it by the dead band. */
export function steady(value, shown) {
  if (value === null || value === undefined) return null;
  return shown !== null && shown !== undefined && Math.abs(value - shown) < DEAD_BAND_DB ? shown : value;
}

export class DeviceList {
  constructor() {
    this.rows = new Map(); // address -> { device, heardAt, best, bars, shown: {anchor: dBm} }
    this.order = [];
  }

  /** Merge one update from P1. `now` is this page's clock in seconds. */
  update(devices, now) {
    const added = [];
    for (const device of devices) {
      const heardAt = now - device.last_seen_age_s;
      let row = this.rows.get(device.address);
      if (!row) {
        if (now - heardAt > GONE_AFTER_S) continue; // already gone before we ever showed it
        row = { shown: {}, best: null, bars: undefined };
        this.rows.set(device.address, row);
        added.push(device);
      }
      row.device = device;
      row.heardAt = heardAt;
      for (const [anchor, value] of Object.entries(device.rssi)) row.shown[anchor] = steady(value, row.shown[anchor]);
      row.best = steady(device.best_rssi, row.best);
      row.bars = signalBars(row.best, row.bars);
    }
    added.sort((a, b) => (b.best_rssi ?? -999) - (a.best_rssi ?? -999));
    this.order.push(...added.map((d) => d.address));
    this.order = this.order.filter((address) => {
      const keep = now - this.rows.get(address).heardAt <= DROP_GONE_AFTER_S;
      if (!keep) this.rows.delete(address);
      return keep;
    });
  }

  isGone(address, now) {
    return now - this.rows.get(address).heardAt > GONE_AFTER_S;
  }

  /** Tidy up on request: tracked device first, then strongest signal; gone devices removed. */
  sort(now) {
    const present = this.order.filter((address) => !this.isGone(address, now));
    for (const address of this.order) if (this.isGone(address, now)) this.rows.delete(address);
    const key = (address) => {
      const row = this.rows.get(address);
      return [row.device.is_target ? 0 : 1, -(row.best ?? -999)];
    };
    this.order = present.sort((a, b) => {
      const [ta, sa] = key(a);
      const [tb, sb] = key(b);
      return ta - tb || sa - sb;
    });
  }

  pin(address) {
    if (!this.rows.has(address)) return;
    this.order = [address, ...this.order.filter((a) => a !== address)];
  }
}
