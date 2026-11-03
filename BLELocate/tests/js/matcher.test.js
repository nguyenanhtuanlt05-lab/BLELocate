import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { TargetMatcher, isValidRssi, normalizeAddress, normalizeUuid, trackIdentity } from "../../blelocate/web/matcher.js";

const vectors = JSON.parse(readFileSync(new URL("../vectors/matching.json", import.meta.url), "utf8"));

test("UUID normalization matches the Python implementation", () => {
  for (const [raw, expected] of vectors.uuid_normalization) {
    if (expected === null) assert.throws(() => normalizeUuid(raw), undefined, raw);
    else assert.equal(normalizeUuid(raw), expected, raw);
  }
});

test("address normalization matches the Python implementation", () => {
  for (const [raw, expected] of vectors.address_normalization) {
    if (expected === null) assert.throws(() => normalizeAddress(raw), undefined, raw);
    else assert.equal(normalizeAddress(raw), expected, raw);
  }
});

test("an empty target is refused", () => {
  assert.throws(() => new TargetMatcher("", "", 30, ""));
});

test("a device picked from the list is tracked by its most stable identity", () => {
  const custom = "12345678-1234-5678-1234-56789abcdef0";
  const fastPair = "0000fe2c-0000-1000-8000-00805f9b34fb";
  assert.deepEqual(trackIdentity({ address: "AA:BB:CC:DD:EE:FF", name: "T", uuids: [fastPair, custom] }),
    { service_uuid: custom, name: "T", address: "", by: "UUID and name" });
  assert.deepEqual(trackIdentity({ address: "AA:BB:CC:DD:EE:FF", name: "Buds", uuids: [fastPair] }),
    { service_uuid: "", name: "Buds", address: "", by: "name" });
  assert.deepEqual(trackIdentity({ address: "AA:BB:CC:DD:EE:FF", name: null, uuids: [] }),
    { service_uuid: "", name: "", address: "AA:BB:CC:DD:EE:FF", by: "address" });
});

test("RSSI validity matches the Python implementation", () => {
  for (const [rssi, expected] of vectors.rssi_validity) assert.equal(isValidRssi(rssi), expected, String(rssi));
});

for (const c of vectors.cases) {
  test(`matching: ${c.name}`, () => {
    const t = c.target;
    const matcher = new TargetMatcher(t.service_uuid, t.name, t.forget_after_s, t.address || "");
    c.steps.forEach((step, i) => {
      if (step.configure) {
        const k = step.configure;
        assert.equal(matcher.configure(k.service_uuid, k.name, k.forget_after_s, k.address || ""), step.changed, `step ${i}`);
        return;
      }
      const matched = matcher.observe(step.addr, step.uuids, step.name, step.t);
      assert.equal(matched && isValidRssi(step.rssi), step.accept, `step ${i}: ${JSON.stringify(step)}`);
    });
  });
}
