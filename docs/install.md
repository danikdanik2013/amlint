# Install

## pip

```bash
pip install amlint
```

## Docker

No Python required:

```bash
docker run --rm -v $(pwd):/cfg ghcr.io/danikdanik2013/amlint check /cfg/alertmanager.yml
```

## Homebrew (macOS)

```bash
brew tap danikdanik2013/amlint
brew install amlint
```

## pre-commit

Add to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/danikdanik2013/amlint
    rev: v0.1.2
    hooks:
      - id: amlint
```

amlint will run automatically on every `git commit` for any file matching `alertmanager*.yml`.

## Shell completions

```bash
pip install "amlint[completions]"

# bash
echo 'eval "$(register-python-argcomplete amlint)"' >> ~/.bashrc

# zsh
echo 'eval "$(register-python-argcomplete amlint)"' >> ~/.zshrc
```

## Requirements

- Python 3.9+
- pyyaml (installed automatically)
