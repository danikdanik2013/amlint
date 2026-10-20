# Changelog

## [Unreleased]

- `.tmpl` files that aren't Alertmanager templates, such as chezmoi or gomplate templates,
  no longer get "function not defined" errors. The fix is in amlint itself (`amlint lsp`),
  so it comes with the amlint release after 0.8.0; the extension doesn't change.

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
