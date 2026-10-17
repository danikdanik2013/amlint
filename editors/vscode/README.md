# amlint for VS Code

Catch Prometheus Alertmanager config mistakes while you edit: alerts that reach no
receiver, routes that never match, inhibit rules that mute too much. Hover a route to see
where its alerts go.

The extension runs [amlint](https://github.com/danikdanik2013/amlint), an open-source
linter for Alertmanager configs. It catches what `amtool check-config` catches, plus
configs that load fine but don't route the way you meant.

## Features

**Findings as you type.** The same findings as `amlint check`, on the same lines. amlint
lints the editor buffer, so you don't have to save first. Click a check code to read what
goes wrong and how to fix it.

**Hover a route** to see:

- its matchers, and the matchers on the way to it
- its receiver, `group_by` and timings, and where each comes from: set here, inherited
  from a parent route, or Alertmanager's default
- an example alert that ends on this route, and the receivers it goes to. The example comes
  from amlint's routing simulation, the same one behind `amlint route`.

**Quick fixes.** Silence a finding you accept: add `# amlint: disable=<code>` to its line,
or `# amlint: disable-file=<code>` to the top of the file.

**Reads the file you deploy.** A plain `alertmanager.yml`, a Kubernetes Secret or
ConfigMap, kube-prometheus-stack Helm values, a Mimir or Cortex tenant config, or
prometheus-operator `AlertmanagerConfig` resources. Findings point into your file, even
inside an embedded `alertmanager.yaml: |` block.

## Requirements

amlint 0.6.0 or newer, which has the `amlint lsp` command:

```bash
pip install -U amlint
```

Check that it works: `amlint lsp --help`. If VS Code can't find `amlint` on its `PATH`,
set `amlint.path` to the full path, for example `${workspaceFolder}/.venv/bin/amlint`.

## Which files it lints

Every YAML file that holds an Alertmanager config:

- a document with `route:` or `receivers:` at the top
- a Secret or ConfigMap with an `alertmanager.yaml` key
- Helm values with an `alertmanager:` section, or a Mimir config with `alertmanager_config:`
- `AlertmanagerConfig` resources

Other YAML files, such as Kubernetes manifests or Prometheus rule files, get no findings.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `amlint.enable` | `true` | Turn amlint off without uninstalling the extension. |
| `amlint.path` | `amlint` | The amlint executable. `~` and `${workspaceFolder}` are expanded. |
| `amlint.trace.server` | `off` | `messages` or `verbose` logs the traffic to the **amlint** output channel. |

In an untrusted workspace, `amlint.path` comes from your user settings only.

To ignore checks or change their severity for a whole repository, use `.amlint.yml` in the
workspace folder, as for the CLI. amlint re-lints open files when it changes:

```yaml
# .amlint.yml
ignore: [empty-receiver]
severity:
  unused-receiver: error
```

## Commands

- **amlint: Restart Server**: restart `amlint lsp`, for example after upgrading amlint.

## Troubleshooting

- **"could not start amlint lsp"**: amlint isn't installed, isn't on VS Code's `PATH`, or
  is older than 0.6.0. Run `which amlint` in a terminal and put that path in `amlint.path`.
- **No findings in a config**: check that the file's language mode (bottom right) is YAML.
  Then set `amlint.trace.server` to `messages` and look at the **amlint** output channel.

## Links

- [Docs](https://danikdanik2013.github.io/amlint/): every check, with examples and fixes
- [Editor setup](https://danikdanik2013.github.io/amlint/editors/), including Neovim and Helix
- [Playground](https://danikdanik2013.github.io/amlint/playground/): try amlint in the browser
- [Issues](https://github.com/danikdanik2013/amlint/issues)
