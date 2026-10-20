# Editors

See amlint's findings in your editor as you type, and hover a route to see where its alerts
go. `amlint lsp` is a language server, so it works with VS Code, Neovim, Helix and any other
editor that speaks the Language Server Protocol.

You need amlint 0.6.0 or newer:

```bash
pip install -U amlint
amlint lsp --help    # check that the command is there
```

## What you get

- **Findings as you type**, on the same lines `amlint check` reports. amlint lints the editor
  buffer, so you don't have to save first. Each check code links to its section in these
  docs.
- **Hover on a route**: its matchers and the matchers on the way to it, its receiver,
  `group_by` and timings, and where each value comes from: set on this route, inherited from
  a parent, or Alertmanager's default. It also shows an example alert that ends on the route
  and the receivers it goes to, from the same simulation as
  [`amlint route`](usage.md#route).
- **Quick fixes** for a finding you accept: add `# amlint: disable=<code>` to its line, or
  `# amlint: disable-file=<code>` to the top of the file. See
  [inline suppressions](usage.md#inline-suppressions).

A hover over a route looks like this:

```text
Route route.routes[0].routes[0] · amlint

Matchers: severity="critical"

setting          value       from
receiver         db-oncall   set here
group_by         alertname   inherited from route
group_wait       10s         inherited from route.routes[0]
group_interval   5m          Alertmanager default
repeat_interval  4h          Alertmanager default

Alerts that reach it match every route on the way, unless an earlier sibling
route without continue: true takes them first:
- route.routes[0]: team="db"
- route.routes[0].routes[0]: severity="critical"

For example, an alert with team=db, severity=critical ends here and goes to db-oncall.
```

## Which files get findings

Every YAML file that holds an Alertmanager config, in any format `amlint check` reads:

- a document with `route:` or `receivers:` at the top
- a Kubernetes Secret or ConfigMap with an `alertmanager.yaml` key
- kube-prometheus-stack Helm values (`alertmanager:`), or a Mimir or Cortex config
  (`alertmanager_config:`)
- prometheus-operator `AlertmanagerConfig` resources

Other YAML files, such as Kubernetes manifests or Prometheus rule files, get nothing. A
config that doesn't parse gets one error at the broken line.

## VS Code

Install the [amlint extension](https://marketplace.visualstudio.com/items?itemName=dpyly.amlint) from the Marketplace: search for "amlint" in the
Extensions view, or run:

```bash
code --install-extension dpyly.amlint
```

Then open an `alertmanager.yml`. The extension runs the amlint you installed, so new
checks come with `pip install -U amlint`, not with an extension update. Settings:

| Setting | Default | What it does |
|---|---|---|
| `amlint.enable` | `true` | Turn amlint off without uninstalling the extension. |
| `amlint.path` | `amlint` | The amlint executable. `~` and `${workspaceFolder}` are expanded, for example `${workspaceFolder}/.venv/bin/amlint`. |
| `amlint.trace.server` | `off` | `messages` or `verbose` logs the traffic to the **amlint** output channel. |

**amlint: Restart Server** in the command palette restarts it, for example after you
upgrade amlint. If VS Code says it can't start `amlint lsp`, run `which amlint` in a
terminal and put that path in `amlint.path`.

## Neovim

Neovim 0.11 or newer. In your config:

```lua
vim.lsp.config("amlint", {
  cmd = { "amlint", "lsp" },
  filetypes = { "yaml" },
  root_markers = { ".amlint.yml", ".amlint.yaml", ".git" },
})
vim.lsp.enable("amlint")
```

It runs next to yaml-language-server if you use that too. `K` shows the hover; quick fixes
are in `vim.lsp.buf.code_action()`.

## Helix

In `~/.config/helix/languages.toml`:

```toml
[language-server.amlint]
command = "amlint"
args = ["lsp"]

[[language]]
name = "yaml"
language-servers = ["amlint", "yaml-language-server"]
```

Helix asks the first server in the list for hovers, so put amlint first to get its route
hovers.

## Other editors

Configure your LSP client to run `amlint lsp` over stdio for YAML files. The server needs
no initialization options.

## Configure the checks

The editor reads the same [project config](usage.md#project-config) as the CLI: `ignore`,
`severity` and `rules` from `.amlint.yml`, `.amlint.yaml` or `pyproject.toml` in the
workspace folder. A file outside any workspace folder uses the config next to it. Edits to
the project config apply at the next change to the file; VS Code re-lints open files right
away.

With `rules:` set, the [checks against your Prometheus rules](usage.md#checking-against-prometheus-rules)
run as you type too. amlint reads the rule files again when one of them changes. If a glob
matches nothing, a warning on the first line says so.

## Limits

- The Prometheus rules checks (`check --rules`) don't run in the editor.
- `template-file-missing` looks for template files next to the file on disk, so a new file
  that was never saved skips it.
