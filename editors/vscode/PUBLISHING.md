# Publishing the VS Code extension

Nothing publishes the extension automatically. The `VS Code extension` workflow only builds
the `.vsix` and keeps it as a workflow artifact. These are the steps for a maintainer.

## Before the first release

1. Release amlint with `amlint lsp` to PyPI first. The extension starts `amlint lsp`, and its
   README and error message say "amlint 0.6.0 or newer". If that release gets another
   version number, update both.
2. Create the publisher `danikdanik2013` at https://marketplace.visualstudio.com/manage,
   signed in with a Microsoft account. If you pick another publisher ID, change `publisher`
   in `package.json`.
3. Create a personal access token in Azure DevOps (https://dev.azure.com, User settings →
   Personal access tokens): organization **All accessible organizations**, scope
   **Marketplace → Manage**.
4. Optional: add a 128×128 PNG as `icon.png` and `"icon": "icon.png"` in `package.json`.
   The Marketplace shows a placeholder without one.

## Each release

1. Bump `version` in `package.json` and add a section to `CHANGELOG.md` in this folder.
2. Build and check the package:

   ```bash
   cd editors/vscode
   npm ci
   npm run package          # writes amlint-<version>.vsix
   code --install-extension amlint-<version>.vsix   # try it in your own VS Code
   ```

3. Publish that file, with the token from step 3 above:

   ```bash
   npx vsce login danikdanik2013                          # once; asks for the token
   npx vsce publish --packagePath amlint-<version>.vsix
   ```

   Or upload the `.vsix` by hand on https://marketplace.visualstudio.com/manage.

4. Optional: also publish to Open VSX (VSCodium, Cursor, Gitpod) with
   `npx ovsx publish amlint-<version>.vsix -p <open-vsx-token>`.

## Automating it later

To publish from CI, store the token as a repository secret, for example `VSCE_PAT`, and add
a step after "Type-check, bundle and package" in `.github/workflows/vscode.yml`:

```yaml
      - name: Publish to the Marketplace
        if: github.event_name == 'workflow_dispatch'
        run: npx vsce publish --packagePath *.vsix
        env:
          VSCE_PAT: ${{ secrets.VSCE_PAT }}
```

Don't trigger it with a tag that starts with `v`: `publish.yml` publishes amlint to PyPI on
every `v*` tag.
