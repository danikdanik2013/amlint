// node --test: the amlint.path check that runs before the language client starts, and the
// search for amlint when it isn't on PATH. `npm test` bundles src/preflight.ts to
// out/test/preflight.js first.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const {
  atLeast,
  checkExecutable,
  fallbackLocations,
  findExecutable,
  parseVersion,
  problemMessage,
} = require("../out/test/preflight.js");

const posix = process.platform !== "win32";

/** A fake executable that prints `text` and exits with `code`. */
function fake(name, text, code = 0) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "amlint-preflight-"));
  const file = path.join(dir, name);
  fs.writeFileSync(file, `#!/bin/sh\nprintf '%s\\n' '${text}'\nexit ${code}\n`, { mode: 0o755 });
  return file;
}

/** checkExecutable, except that `amlint` is never on PATH, whatever this machine has. */
function notOnPath(command) {
  return command === "amlint"
    ? Promise.resolve({ ok: false, reason: "missing", detail: "amlint: ENOENT" })
    : checkExecutable(command);
}

const nowhere = path.join(os.tmpdir(), "no-such-amlint-dir", "amlint");

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

test("amlint that isn't on PATH is found in a fallback location", { skip: !posix }, async () => {
  const brew = fake("amlint", "amlint 0.8.5");
  const found = await findExecutable("amlint", [nowhere, brew], notOnPath);
  assert.deepEqual(found,
    { command: brew, result: { ok: true, version: "0.8.5" }, tried: [nowhere, brew] });
});

test("a fallback that is too old is skipped, and reported if none is newer", { skip: !posix },
  async () => {
    const old = fake("amlint", "amlint 0.4.1");
    const current = fake("amlint", "amlint 0.8.5");
    assert.equal((await findExecutable("amlint", [old, current], notOnPath)).command, current);

    const found = await findExecutable("amlint", [nowhere, old], notOnPath);
    assert.deepEqual([found.command, found.result.reason, found.result.version],
      [old, "too-old", "0.4.1"]);
    assert.match(problemMessage(found.command, found.result), /is amlint 0\.4\.1/);
  });

test("nothing found: amlint is missing, and the message says how to install it", async () => {
  const found = await findExecutable("amlint", [nowhere], notOnPath);
  assert.deepEqual([found.command, found.result.reason, found.tried],
    ["amlint", "missing", [nowhere]]);
  const message = problemMessage(found.command, found.result);
  for (const step of [
    "brew tap danikdanik2013/amlint https://github.com/danikdanik2013/amlint",
    "brew install danikdanik2013/amlint/amlint",
    "pipx install amlint",
  ]) {
    assert.ok(message.includes(step), message);
  }
});

test("an amlint.path the user set never falls back", { skip: !posix }, async () => {
  const brew = fake("amlint", "amlint 0.8.5");
  const missing = await findExecutable(nowhere, [brew]);
  assert.deepEqual([missing.command, missing.result.reason, missing.tried],
    [nowhere, "missing", []]);

  const old = fake("amlint", "amlint 0.4.1");
  const tooOld = await findExecutable(old, [brew]);
  assert.deepEqual([tooOld.command, tooOld.result.reason, tooOld.tried], [old, "too-old", []]);
});

test("amlint on PATH is used without looking further", async () => {
  const onPath = async (command) => {
    assert.equal(command, "amlint");
    return { ok: true, version: "0.8.5" };
  };
  const found = await findExecutable("amlint", [nowhere], onPath);
  assert.deepEqual(found, { command: "amlint", result: { ok: true, version: "0.8.5" }, tried: [] });
});

test("fallback locations: Homebrew, then pipx", () => {
  assert.deepEqual(fallbackLocations({ platform: "darwin", home: "/Users/me" }),
    ["/opt/homebrew/bin/amlint", "/usr/local/bin/amlint", "/Users/me/.local/bin/amlint"]);
  assert.deepEqual(fallbackLocations({ platform: "linux", home: "/home/me" }), [
    "/home/linuxbrew/.linuxbrew/bin/amlint",
    "/usr/local/bin/amlint",
    "/home/me/.local/bin/amlint",
  ]);
});

test("a workspace .venv is tried first, only when the folder is passed", () => {
  const places = { platform: "darwin", home: "/Users/me", workspaceFolder: "/work/alerts" };
  assert.equal(fallbackLocations(places)[0], "/work/alerts/.venv/bin/amlint");
  assert.ok(!fallbackLocations({ platform: "darwin", home: "/Users/me" })
    .some((file) => file.includes(".venv")));
});

test("fallback locations on Windows: pipx, then each Python's Scripts, newest first", () => {
  const listings = {
    "C:\\Users\\me\\AppData\\Roaming\\Python": ["Python39", "Python312", "Python310", "pip"],
  };
  const readdir = (dir) => {
    if (dir in listings) {
      return listings[dir];
    }
    throw Object.assign(new Error(`ENOENT: ${dir}`), { code: "ENOENT" });
  };
  const found = fallbackLocations({
    platform: "win32",
    home: "C:\\Users\\me",
    env: {
      APPDATA: "C:\\Users\\me\\AppData\\Roaming",
      LOCALAPPDATA: "C:\\Users\\me\\AppData\\Local",
    },
    readdir,
  });
  assert.deepEqual(found, [
    "C:\\Users\\me\\.local\\bin\\amlint.exe",
    "C:\\Users\\me\\AppData\\Roaming\\Python\\Python312\\Scripts\\amlint.exe",
    "C:\\Users\\me\\AppData\\Roaming\\Python\\Python310\\Scripts\\amlint.exe",
    "C:\\Users\\me\\AppData\\Roaming\\Python\\Python39\\Scripts\\amlint.exe",
  ]);
});
