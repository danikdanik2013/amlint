# Install

Pick whichever fits how you work. Or don't install anything:
[the playground](playground/index.md) runs amlint in your browser.

## pip

```bash
pip install amlint
```

You need Python 3.9 or newer. amlint pulls in two small dependencies, PyYAML and Rich.

## Docker

No Python needed. Mount the folder with your config and pass its path inside the container:

```bash
docker run --rm -v "$PWD":/cfg ghcr.io/danikdanik2013/amlint check /cfg/alertmanager.yml
```

Releases are tagged by version (`0.8.5`) and minor version (`0.8`); `latest` follows the main branch.

## Homebrew

```bash
brew tap danikdanik2013/amlint https://github.com/danikdanik2013/amlint
brew install amlint
```

## GitHub Action

Nothing to install. Add the action to a workflow; see [GitHub Action](github-action.md).

## pre-commit

Check the config on every commit. Add this to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/danikdanik2013/amlint
    rev: v0.8.5
    hooks:
      - id: amlint
```

The hook runs on files whose names end in `alertmanager.yml` or `alertmanager.yaml`.

## Editors

To see findings as you type, install amlint as above, then:

- **VS Code:** install the [amlint extension](https://marketplace.visualstudio.com/items?itemName=dpyly.amlint) (`code --install-extension dpyly.amlint`).
- **Neovim, Helix and others:** see [Editors](editors.md).

## AI coding assistants

**Claude Code:** add the plugin. It teaches Claude to run amlint when you ask it to check an
Alertmanager config, explain a finding, trace where an alert goes or write routing tests:

```text
/plugin marketplace add danikdanik2013/amlint
/plugin install amlint@amlint
```

You still need amlint itself installed (see above). The plugin only tells Claude how to use
it.

**Other assistants:** [llms.txt](llms.txt) is a short summary of amlint for AI tools, and
[llms-full.txt](https://danikdanik2013.github.io/amlint/llms-full.txt) has every page of these docs in one file.

## Shell completions

Tab completion for commands and flags in bash and zsh:

```bash
pip install "amlint[completions]"

# bash
echo 'eval "$(register-python-argcomplete amlint)"' >> ~/.bashrc

# zsh
echo 'eval "$(register-python-argcomplete amlint)"' >> ~/.zshrc
```

## Check that it works

```bash
amlint --version
amlint init > /tmp/alertmanager.yml && amlint check /tmp/alertmanager.yml
```

`amlint init` prints a minimal valid config, so the second command should report no errors.
