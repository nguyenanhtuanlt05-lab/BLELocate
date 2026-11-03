// Browser twin of blelocate/matching.py. Both are checked against
// tests/vectors/matching.json, so change them together.

const BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb";

export function normalizeUuid(value) {
  let text = String(value).trim().toLowerCase();
  if (text.startsWith("0x")) text = text.slice(2);
  if (/^[0-9a-f]{4}$|^[0-9a-f]{8}$/.test(text)) text = text.padStart(8, "0") + BASE_UUID_SUFFIX;
  // Same forms Python's uuid.UUID accepts: braces, urn:uuid: prefix, hyphens anywhere.
  const hex = text.replace(/^urn:uuid:/, "").replace(/^\{|\}$/g, "").replace(/-/g, "");
  if (!/^[0-9a-f]{32}$/.test(hex)) throw new Error(`Not a valid UUID: ${value}`);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * How to recognise a device picked from the nearby list, most stable first:
 * a custom 128-bit UUID (survives address changes), else its name, else its address.
 * Standard 16-bit UUIDs (Fast Pair, battery, ...) are shared by many devices, so they don't count.
 */
export function trackIdentity(device) {
  const custom = (device.uuids || []).filter((u) => !u.endsWith(BASE_UUID_SUFFIX));
  if (custom.length) return { service_uuid: custom[0], name: device.name || "", address: "", by: device.name ? "UUID and name" : "UUID" };
  if (device.name) return { service_uuid: "", name: device.name, address: "", by: "name" };
  return { service_uuid: "", name: "", address: device.address, by: "address" };
}

export function isValidRssi(rssi) {
  return typeof rssi === "number" && Number.isFinite(rssi) && rssi >= -120 && rssi < 0;
}

export function normalizeAddress(value) {
  const text = String(value).trim().toUpperCase().replace(/-/g, ":");
  if (!/^[0-9A-F]{2}(:[0-9A-F]{2}){5}$/.test(text)) throw new Error(`Not a Bluetooth address: ${value}`);
  return text;
}

/** Every configured part (service UUID, exact name, address) must match; empty parts are ignored. */
export class TargetMatcher {
  constructor(serviceUuid, name = "", forgetAfterS = 30, address = "") {
    this.serviceUuid = this.name = this.address = "";
    this.forgetAfterS = forgetAfterS;
    this.evidence = new Map();
    this.configure(serviceUuid, name, forgetAfterS, address);
  }

  /** Returns true (and forgets every match) when the target changed. */
  configure(serviceUuid, name = "", forgetAfterS = 30, address = "") {
    const next = [serviceUuid ? normalizeUuid(serviceUuid) : "", name || "", address ? normalizeAddress(address) : ""];
    if (!next.some(Boolean)) throw new Error("The target needs a service UUID, a name or an address.");
    this.forgetAfterS = Number(forgetAfterS);
    if (next[0] === this.serviceUuid && next[1] === this.name && next[2] === this.address) return false;
    [this.serviceUuid, this.name, this.address] = next;
    this.evidence.clear();
    return true;
  }

  /** Record one scanner callback; true if it comes from the target. `now` is in seconds. */
  observe(address, uuids, name, now) {
    this.expire(now);
    const hasUuid = Boolean(this.serviceUuid) && (uuids || []).some((u) => this.sameUuid(u));
    let ev = this.evidence.get(address);
    if (!ev) {
      if (!(hasUuid || (this.name && name === this.name) || this.sameAddress(address))) return false;
      ev = { sawUuid: false, name: null, lastSeen: now };
      this.evidence.set(address, ev);
    }
    ev.lastSeen = now;
    if (hasUuid) ev.sawUuid = true;
    if (name) ev.name = name;
    return this.isMatch(address, ev);
  }

  matchedAddresses(now) {
    this.expire(now);
    return [...this.evidence].filter(([address, ev]) => this.isMatch(address, ev)).map(([address]) => address);
  }

  isMatch(address, ev) {
    return (!this.serviceUuid || ev.sawUuid) && (!this.name || ev.name === this.name) && (!this.address || this.sameAddress(address));
  }

  // Browser device ids are opaque, never addresses, so they can't match an address target.
  sameAddress(address) {
    return Boolean(this.address) && String(address).toUpperCase() === this.address;
  }

  sameUuid(value) {
    try {
      return normalizeUuid(value) === this.serviceUuid;
    } catch {
      return false;
    }
  }

  expire(now) {
    const cutoff = now - this.forgetAfterS;
    for (const [address, ev] of this.evidence) if (ev.lastSeen < cutoff) this.evidence.delete(address);
  }
}
