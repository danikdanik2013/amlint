// Checks that amlint.path points at an amlint that has `amlint lsp`, before the language
// client starts it. Without this, an old amlint (or something else called amlint) exits at
// once, and the client restarts it over and over. When amlint.path is left at `amlint` and
// that isn't on VS Code's PATH, it looks where Homebrew and pipx install amlint. Kept free
// of the vscode module so it can be tested with plain Node.

import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";

/** `amlint lsp` arrived in amlint 0.6.0. */
export const MIN_VERSION = "0.6.0";

/** The default amlint.path: looked up on PATH, and in fallbackLocations() if it isn't there. */
export const DEFAULT_COMMAND = "amlint";

/** How to install amlint. The tap is this repository, not homebrew-amlint, so it needs its
 * URL; Homebrew 7 trusts a formula from a third-party tap when it is named in full. */
export const INSTALL = "brew tap danikdanik2013/amlint https://github.com/danikdanik2013/amlint " +
  "and brew install danikdanik2013/amlint/amlint, or pipx install amlint";

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

export interface Places {
  platform: NodeJS.Platform;
  home: string;
  env?: NodeJS.ProcessEnv;
  /** Pass it only for a trusted workspace: its `.venv` is tried first, and that runs code
   * from the workspace. */
  workspaceFolder?: string;
  /** Lists a directory, or throws. Tests replace it. */
  readdir?: (dir: string) => string[];
}

/** Where amlint is often installed but VS Code may not look. Homebrew and pipx add their
 * directories to PATH in a shell profile, and VS Code started from the Dock, a desktop menu
 * or the Start menu doesn't read it. */
export function fallbackLocations(places: Places): string[] {
  const { platform, home, env = {}, workspaceFolder, readdir = fs.readdirSync } = places;
  if (platform === "win32") {
    const join = path.win32.join;
    const found = workspaceFolder ? [join(workspaceFolder, ".venv", "Scripts", "amlint.exe")] : [];
    found.push(join(home, ".local", "bin", "amlint.exe")); // pipx
    // pip install --user, then the Python from python.org: a Scripts directory per version
    const roots = [env.APPDATA && join(env.APPDATA, "Python"),
      env.LOCALAPPDATA && join(env.LOCALAPPDATA, "Programs", "Python")];
    for (const root of roots) {
      if (!root) {
        continue;
      }
      let versions: string[];
      try {
        versions = readdir(root).filter((name) => /^Python\d+/i.test(name));
      } catch {
        continue;
      }
      versions.sort((a, b) => b.localeCompare(a, undefined, { numeric: true })); // newest first
      found.push(...versions.map((version) => join(root, version, "Scripts", "amlint.exe")));
    }
    return found;
  }
  const join = path.posix.join;
  const homebrew = platform === "darwin"
    ? ["/opt/homebrew/bin", "/usr/local/bin"] // Apple silicon, Intel
    : ["/home/linuxbrew/.linuxbrew/bin", "/usr/local/bin"];
  const dirs = [
    ...(workspaceFolder ? [join(workspaceFolder, ".venv", "bin")] : []),
    ...homebrew,
    join(home, ".local", "bin"), // pipx
  ];
  return dirs.map((dir) => join(dir, "amlint"));
}

export interface Found {
  /** The executable to run, or the one the problem is about. */
  command: string;
  result: Preflight;
  /** The fallback locations tried, in order, because `amlint` wasn't on PATH. */
  tried: string[];
}

/** Checks `command`. If it is the default `amlint` and isn't on PATH, tries `fallbacks` in
 * order and returns the first that can serve `amlint lsp`. If none can, the problem to show
 * is the first fallback that exists (too old, or not amlint), else that amlint is missing.
 * An amlint.path the user set is never swapped for another executable. */
export async function findExecutable(
  command: string,
  fallbacks: string[],
  check: (command: string) => Promise<Preflight> = checkExecutable,
): Promise<Found> {
  const result = await check(command);
  if (result.ok || result.reason !== "missing" || command !== DEFAULT_COMMAND) {
    return { command, result, tried: [] };
  }
  const tried: string[] = [];
  let problem: Found | undefined;
  for (const candidate of fallbacks) {
    tried.push(candidate);
    const found = await check(candidate);
    if (found.ok) {
      return { command: candidate, result: found, tried };
    }
    if (!problem && found.reason !== "missing") {
      problem = { command: candidate, result: found, tried };
    }
  }
  return problem ?? { command, result, tried };
}

/** What to tell the user when the check fails. */
export function problemMessage(command: string, result: Exclude<Preflight, { ok: true }>): string {
  switch (result.reason) {
    case "missing":
      return `amlint: can't find "${command}". Install amlint ${MIN_VERSION} or newer ` +
        `(${INSTALL}), or set amlint.path to its full path: VS Code may not see the PATH ` +
        "of your shell.";
    case "too-old":
      return `amlint: "${command}" is amlint ${result.version}, and the editor needs ` +
        `${MIN_VERSION} or newer. Upgrade it (brew upgrade danikdanik2013/amlint/amlint, ` +
        "pipx upgrade amlint, or pip install -U amlint), or set amlint.path to a newer one.";
    default:
      return `amlint: "${command} --version" didn't print an amlint version, so it may not ` +
        "be amlint. Check amlint.path.";
  }
}
