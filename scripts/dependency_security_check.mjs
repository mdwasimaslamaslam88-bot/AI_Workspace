import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
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

const queryStringPath = require.resolve("query-string");
const queryStringRequire = createRequire(queryStringPath);
const decode = queryStringRequire("decode-uri-component");
assert.equal(typeof decode, "function", "The actual query-string consumer requires a function.");
assert.equal(decode("hello+world"), "hello world");
assert.equal(decode("hello%2Bworld"), "hello+world");
assert.deepEqual(
  { ...require("query-string").parse("name=hello+world&literal=%2B&value=%E2%9C%93") },
  { name: "hello world", literal: "+", value: "✓" },
);

// Exercise the installed consumer in a bounded child, since a vulnerable decoder
// can block the event loop before a JavaScript timeout has a chance to run.
const malformedProbe = spawnSync(process.execPath, ["-e", `
  const assert = require("node:assert/strict");
  const query = require(${JSON.stringify(queryStringPath)});
  const malformed = "%FF".repeat(20000);
  assert.equal(query.parse("value=" + malformed).value, malformed);
`], { encoding: "utf8", timeout: 2000, maxBuffer: 4096 });
assert.equal(malformedProbe.error, undefined, "Malformed URI decoding exceeded its process bound.");
assert.equal(malformedProbe.signal, null);
assert.equal(malformedProbe.status, 0, "The actual query-string decoder failed its malformed input contract.");
console.log("dependency security: actual URI consumer compatibility and malformed-input bound passed");
