// Checks that amlint.path points at an amlint that has `amlint lsp`, before the language
// client starts it. Without this, an old amlint (or something else called amlint) exits at
// once, and the client restarts it over and over. Kept free of the vscode module so it can
// be tested with plain Node.

import { execFile } from "child_process";

/** `amlint lsp` arrived in amlint 0.6.0. */
export const MIN_VERSION = "0.6.0";

export type Preflight =
  | { ok: true; version: string }
  | { ok: false; reason: "missing" | "too-old" | "unknown"; version?: string; detail: string };

/** The x.y.z out of `amlint --version` output ("amlint 0.6.1"), if there is one. */
export function parseVersion(text: string): string | undefined {
  const match = /\bamlint\s+v?(\d+)\.(\d+)\.(\d+)/.exec(text);
  return match ? `${match[1]}.${match[2]}.${match[3]}` : undefined;
}

/** Whether version `a` is at least `b`; both are x.y.z. */
export function atLeast(a: string, b: string): boolean {
  const x = a.split(".").map(Number);
  const y = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if (x[i] !== y[i]) {
      return x[i] > y[i];
    }
  }
  return true;
}

/** Runs `<command> --version` and says whether it can serve `amlint lsp`. */
export function checkExecutable(command: string, timeoutMs = 15000): Promise<Preflight> {
  return new Promise((resolve) => {
    execFile(command, ["--version"], { timeout: timeoutMs, windowsHide: true },
      (error, stdout, stderr) => {
        const output = `${stdout ?? ""}${stderr ?? ""}`.trim();
        const code = (error as NodeJS.ErrnoException | null)?.code;
        if (code === "ENOENT" || code === "EACCES") {
          resolve({ ok: false, reason: "missing", detail: `${command}: ${code}` });
          return;
        }
        const version = parseVersion(output);
        if (!version) {
          const why = error ? `${error.message}` : "no version in its output";
          resolve({ ok: false, reason: "unknown", detail: `${why}\n${output}`.trim() });
        } else if (!atLeast(version, MIN_VERSION)) {
          resolve({ ok: false, reason: "too-old", version, detail: output });
        } else {
          resolve({ ok: true, version });
        }
      });
  });
}

/** What to tell the user when the check fails. */
export function problemMessage(command: string, result: Exclude<Preflight, { ok: true }>): string {
  switch (result.reason) {
    case "missing":
      return `amlint: can't find "${command}". Install amlint ${MIN_VERSION} or newer ` +
        "(pipx install amlint), or set amlint.path to its full path: VS Code may not see " +
        "the PATH of your shell.";
    case "too-old":
      return `amlint: "${command}" is amlint ${result.version}, and the editor needs ` +
        `${MIN_VERSION} or newer. Upgrade it (pipx upgrade amlint, or pip install -U ` +
        "amlint), or set amlint.path to a newer one.";
    default:
      return `amlint: "${command} --version" didn't print an amlint version, so it may not ` +
        "be amlint. Check amlint.path.";
  }
}
