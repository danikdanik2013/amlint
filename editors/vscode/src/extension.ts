// Starts `amlint lsp` for YAML files. The server decides which files are Alertmanager
// configs and stays silent on the rest, so the extension doesn't have to.

import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { LanguageClient, LanguageClientOptions, ServerOptions } from "vscode-languageclient/node";

let client: LanguageClient | undefined;
let output: vscode.LogOutputChannel;
let watcher: vscode.FileSystemWatcher;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  output = vscode.window.createOutputChannel("amlint", { log: true });
  // .amlint.yml and pyproject.toml set ignored checks and severities: re-lint when they change
  watcher = vscode.workspace.createFileSystemWatcher("**/{.amlint.yml,.amlint.yaml,pyproject.toml}");
  context.subscriptions.push(
    output,
    watcher,
    vscode.commands.registerCommand("amlint.restart", restart),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("amlint.enable") || event.affectsConfiguration("amlint.path")) {
        void restart();
      }
    }),
    // amlint.path from workspace settings only counts once the workspace is trusted
    vscode.workspace.onDidGrantWorkspaceTrust(() => void restart()),
  );
  await start();
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

async function start(): Promise<void> {
  if (!vscode.workspace.getConfiguration("amlint").get<boolean>("enable", true)) {
    return;
  }
  const command = executable();
  const serverOptions: ServerOptions = { command, args: ["lsp"] };
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "yaml" },
      { scheme: "untitled", language: "yaml" },
    ],
    synchronize: { fileEvents: watcher },
    outputChannel: output,
  };
  const candidate = new LanguageClient("amlint", "amlint", serverOptions, clientOptions);
  try {
    await candidate.start();
    client = candidate;
  } catch (error) {
    output.error(`Could not start "${command} lsp": ${error}`);
    await candidate.dispose().catch(() => undefined);
    const choice = await vscode.window.showErrorMessage(
      `amlint: could not start "${command} lsp". Install amlint 0.6.0 or newer ` +
        "(pip install -U amlint), or point amlint.path at it.",
      "Open Settings",
      "Show Log",
    );
    if (choice === "Open Settings") {
      await vscode.commands.executeCommand("workbench.action.openSettings", "amlint.path");
    } else if (choice === "Show Log") {
      output.show();
    }
  }
}

async function stop(): Promise<void> {
  const running = client;
  client = undefined;
  await running?.stop().catch(() => undefined);
}

async function restart(): Promise<void> {
  await stop();
  await start();
}
