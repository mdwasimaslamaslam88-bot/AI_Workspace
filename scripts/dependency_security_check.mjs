import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const xcodeRequire = createRequire(require.resolve("xcode"));
const uuid = xcodeRequire("uuid");

// Check the actual consumer's resolution, not only an audit/lockfile count.
// An undersized caller buffer must fail before producing a partial identifier.
for (const generate of [uuid.v3, uuid.v5]) {
  assert.throws(
    () => generate("synthetic", uuid.v5.DNS, new Uint8Array(8), 4),
    RangeError,
    "The Xcode dependency still accepts an out-of-bounds UUID buffer.",
  );
}

const project = require("xcode").project("synthetic");
project.hash = { project: { objects: {} } };
const identifiers = new Set(Array.from({ length: 100 }, () => project.generateUuid()));
assert.equal(identifiers.size, 100);
assert.ok([...identifiers].every((value) => /^[0-9A-F]{24}$/.test(value)));
console.log("dependency security: UUID buffer rejection and Xcode identifier contract passed");
