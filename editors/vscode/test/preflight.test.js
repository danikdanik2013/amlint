// node --test: the amlint.path check that runs before the language client starts.
// `npm test` bundles src/preflight.ts to out/test/preflight.js first.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { atLeast, checkExecutable, parseVersion, problemMessage } = require("../out/test/preflight.js");

const posix = process.platform !== "win32";

/** A fake executable that prints `text` and exits with `code`. */
function fake(name, text, code = 0) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "amlint-preflight-"));
  const file = path.join(dir, name);
  fs.writeFileSync(file, `#!/bin/sh\nprintf '%s\\n' '${text}'\nexit ${code}\n`, { mode: 0o755 });
  return file;
}

test("parseVersion reads amlint --version", () => {
  assert.equal(parseVersion("amlint 0.6.1"), "0.6.1");
  assert.equal(parseVersion("amlint 0.10.0\n"), "0.10.0");
  assert.equal(parseVersion("Python 3.12.1"), undefined);
  assert.equal(parseVersion(""), undefined);
});

test("atLeast compares numerically", () => {
  assert.ok(atLeast("0.6.0", "0.6.0"));
  assert.ok(atLeast("0.10.0", "0.6.0"));
  assert.ok(atLeast("1.0.0", "0.6.0"));
  assert.ok(!atLeast("0.5.9", "0.6.0"));
  assert.ok(!atLeast("0.1.2", "0.6.0"));
});

test("a missing executable is reported as missing", async () => {
  const result = await checkExecutable(path.join(os.tmpdir(), "no-such-amlint-here"));
  assert.equal(result.ok, false);
  assert.equal(result.reason, "missing");
  assert.match(problemMessage("amlint", result), /can't find "amlint"/);
});

test("an amlint without `amlint lsp` is too old", { skip: !posix }, async () => {
  const result = await checkExecutable(fake("amlint", "amlint 0.1.2"));
  assert.deepEqual([result.ok, result.reason, result.version], [false, "too-old", "0.1.2"]);
  assert.match(problemMessage("amlint", result), /is amlint 0\.1\.2, and the editor needs 0\.6\.0/);
});

test("a current amlint passes", { skip: !posix }, async () => {
  const result = await checkExecutable(fake("amlint", "amlint 0.6.1"));
  assert.deepEqual(result, { ok: true, version: "0.6.1" });
});

test("something that isn't amlint is unknown, even if it exits 0", { skip: !posix }, async () => {
  assert.equal((await checkExecutable(fake("amlint", "hello"))).reason, "unknown");
  assert.equal((await checkExecutable(fake("amlint", "", 1))).reason, "unknown");
});
