# Changelog

## [Unreleased]

## [0.1.3]

- Finds amlint that isn't on VS Code's `PATH`. VS Code started from the Dock or a desktop
  menu often doesn't see the `PATH` your shell profile sets, so a Homebrew or pipx install
  gave "can't find amlint". With `amlint.path` left at `amlint`, the extension now also
  looks in Homebrew's directories (`/opt/homebrew/bin`, `/usr/local/bin`, Linuxbrew), in
  pipx's `~/.local/bin`, and on Windows in each Python's `Scripts` directory. In a trusted
  workspace it tries the workspace's `.venv` first. It uses the first amlint 0.6.0 or newer,
  and the **amlint** output channel says which. An `amlint.path` you set is used as it is.
- "can't find amlint" gives the Homebrew commands too: the tap with its URL, then
  `brew install danikdanik2013/amlint/amlint`, the full name Homebrew 7 needs to trust a
  formula from a third-party tap.
- `.tmpl` files that aren't Alertmanager templates, such as chezmoi or gomplate templates,
  no longer get "function not defined" errors. The fix is in amlint itself (`amlint lsp`),
  so it needs amlint 0.8.5 or newer; the extension doesn't change for it.

## [0.1.2]

- Template files (`.tmpl`): amlint's template findings show in the file itself, on the
  line where the problem is, not only on the config's `templates:` line. Needs amlint 0.8.0
  or newer; older versions stay silent on them.

## [0.1.1]

- No more restart loop when `amlint.path` points at an amlint without `amlint lsp`. Before
  starting the server, the extension runs `amlint --version`: if amlint is missing or older
  than 0.6.0, it says so once, with buttons for the setting and the install help, and
  doesn't start it. **amlint: Restart Server** or a changed `amlint.path` checks again.
- A server that crashes is restarted at most twice in three minutes, then the extension
  stops and says so, instead of restarting it again and again.
- An icon, and a dark banner on the Marketplace page.

## [0.1.0]

- First release. Runs `amlint lsp` (amlint 0.6.0 or newer) for YAML files: findings as you
  type, hover on a route, and quick fixes that add `# amlint: disable=` comments.
