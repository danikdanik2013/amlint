// Starts `amlint lsp` for YAML files. The server decides which files are Alertmanager
// configs and stays silent on the rest, so the extension doesn't have to.

import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import {
  CloseAction,
  CloseHandlerResult,
  ErrorAction,
  ErrorHandlerResult,
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from "vscode-languageclient/node";
import { checkExecutable, MIN_VERSION, problemMessage } from "./preflight";

const DOCS = "https://danikdanik2013.github.io/amlint/editors/#vscode";
const MAX_RESTARTS = 2;           // after a crash; more than this in RESTART_WINDOW_MS stops it
const RESTART_WINDOW_MS = 3 * 60 * 1000;

let client: LanguageClient | undefined;
let output: vscode.LogOutputChannel;
let watcher: vscode.FileSystemWatcher;
const warned = new Set<string>(); // problems already shown in a notification this session

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  output = vscode.window.createOutputChannel("amlint", { log: true });
  // .amlint.yml and pyproject.toml set ignored checks and severities: re-lint when they change
  watcher = vscode.workspace.createFileSystemWatcher("**/{.amlint.yml,.amlint.yaml,pyproject.toml}");
  context.subscriptions.push(
    output,
    watcher,
    vscode.commands.registerCommand("amlint.restart", () => restart(true)),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("amlint.enable") || event.affectsConfiguration("amlint.path")) {
        void restart(true);
      }
    }),
    // amlint.path from workspace settings only counts once the workspace is trusted
    vscode.workspace.onDidGrantWorkspaceTrust(() => void restart(false)),
  );
  await start(false);
}

export async function deactivate(): Promise<void> {
  await stop();
}

/** amlint.path with `~` and `${workspaceFolder}` expanded. */
function executable(): string {
  const setting = vscode.workspace.getConfiguration("amlint").get<string>("path", "amlint");
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  let command = (setting || "amlint").trim().replace(/\$\{workspaceFolder\}/g, folder);
  if (command === "~" || command.startsWith("~/") || command.startsWith("~\\")) {
    command = path.join(os.homedir(), command.slice(1));
  }
  return command;
}

/** An error notification with the actions that fix a broken amlint.path. Shown once per
 * problem per session, unless the user asked for it by restarting or changing settings. */
async function report(key: string, message: string, always: boolean): Promise<void> {
  if (!always && warned.has(key)) {
    return;
  }
  warned.add(key);
  const choice = await vscode.window.showErrorMessage(
    message, "Open Settings", "Installation Help", "Show Log");
  if (choice === "Open Settings") {
    await vscode.commands.executeCommand("workbench.action.openSettings", "amlint.path");
  } else if (choice === "Installation Help") {
    await vscode.env.openExternal(vscode.Uri.parse(DOCS));
  } else if (choice === "Show Log") {
    output.show();
  }
}

async function start(userAsked: boolean): Promise<void> {
  if (!vscode.workspace.getConfiguration("amlint").get<boolean>("enable", true)) {
    return;
  }
  const command = executable();
  const check = await checkExecutable(command);
  if (!check.ok) {
    // Starting it anyway would only make the client restart a server that exits at once.
    output.error(`Not starting "${command} lsp": ${check.reason}. ${check.detail}`);
    void report(`${command}:${check.reason}`, problemMessage(command, check), userAsked);
    return;
  }
  output.info(`Starting "${command} lsp" (amlint ${check.version}).`);

  const crashes: number[] = [];
  const serverOptions: ServerOptions = { command, args: ["lsp"] };
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "yaml" },
      { scheme: "untitled", language: "yaml" },
    ],
    synchronize: { fileEvents: watcher },
    outputChannel: output,
    initializationFailedHandler: () => false, // no retry loop on a server that can't start
    errorHandler: {
      error: (): ErrorHandlerResult => ({ action: ErrorAction.Continue, handled: true }),
      closed: (): CloseHandlerResult => {
        const now = Date.now();
        crashes.push(now);
        while (crashes.length && now - crashes[0] > RESTART_WINDOW_MS) {
          crashes.shift();
        }
        if (crashes.length <= MAX_RESTARTS) {
          output.warn(`amlint lsp stopped; restarting (${crashes.length}/${MAX_RESTARTS}).`);
          return { action: CloseAction.Restart, handled: true };
        }
        void report(`${command}:crashed`,
          `amlint: "${command} lsp" stopped ${crashes.length} times in a few minutes, so ` +
            "it won't be restarted. The amlint output has the details; run amlint: Restart " +
            "Server to try again.", true);
        return { action: CloseAction.DoNotRestart, handled: true };
      },
    },
  };
  const candidate = new LanguageClient("amlint", "amlint", serverOptions, clientOptions);
  try {
    await candidate.start();
    client = candidate;
  } catch (error) {
    output.error(`Could not start "${command} lsp": ${error}`);
    await candidate.dispose().catch(() => undefined);
    void report(`${command}:start`,
      `amlint: could not start "${command} lsp". Install amlint ${MIN_VERSION} or newer ` +
        "(pipx install amlint), or point amlint.path at it.", userAsked);
  }
}

async function stop(): Promise<void> {
  const running = client;
  client = undefined;
  await running?.stop().catch(() => undefined);
}

async function restart(userAsked: boolean): Promise<void> {
  await stop();
  await start(userAsked);
}
