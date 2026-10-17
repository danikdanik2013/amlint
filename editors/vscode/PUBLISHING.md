# Publishing the VS Code extension

Nothing publishes the extension automatically. The `VS Code extension` workflow only builds
the `.vsix` and keeps it as a workflow artifact. These are the steps for a maintainer.

## Before the first release

1. amlint 0.6.0, the first release with `amlint lsp`, is on PyPI. The extension's README
   and error message ask for it.
2. The publisher is `dpyly`: https://marketplace.visualstudio.com/manage/publishers/dpyly.
   `publisher` in `package.json` must match it, or the Marketplace rejects the package.
3. Only for publishing from the command line, not for an upload by hand, and only until
   December 1, 2026 (see below): create a personal access token in Azure DevOps
   (https://dev.azure.com, User settings → Personal access tokens): organization
   **All accessible organizations**, scope **Marketplace → Manage**.
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
   npx vsce login dpyly                                   # once; asks for the token
   npx vsce publish --packagePath amlint-<version>.vsix
   ```

   Or upload the `.vsix` by hand on https://marketplace.visualstudio.com/manage.

4. Optional: also publish to Open VSX (VSCodium, Cursor, Gitpod) with
   `npx ovsx publish amlint-<version>.vsix -p <open-vsx-token>`.

## Automating it later

A new amlint release doesn't need a new extension release: the extension runs whatever
amlint is installed. Publish the extension only when its own code changes. VS Code then
updates it for everyone on its own.

To publish from CI instead of uploading by hand:

- **Personal access token: don't.** The Marketplace needs a global token (organization
  **All accessible organizations**), and Azure DevOps retires global tokens on December 1,
  2026.
- **Microsoft Entra ID with workload identity federation:** no secret to store or rotate.
  It needs an Azure subscription, a user-assigned managed identity with a federated
  credential that trusts this repository's workflow, and that identity added to the `dpyly`
  publisher as a Contributor. The workflow then signs in with `azure/login` and runs
  `npx vsce publish --azure-credential`. Microsoft documents the setup for Azure Pipelines
  only: https://code.visualstudio.com/api/working-with-extensions/publishing-extension
- **Open VSX** (VSCodium, Cursor) takes a token that Azure's retirement doesn't affect:
  `npx ovsx publish <file>.vsix -p $OVSX_PAT`.

Don't trigger publishing with a tag that starts with `v`: `publish.yml` publishes amlint to
PyPI on every `v*` tag.
