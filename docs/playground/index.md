---
title: Playground
description: Lint an Alertmanager config in your browser. Nothing is uploaded.
template: playground.html
hide:
  - navigation
  - toc
---

# Playground

Paste your `alertmanager.yml`, or the [Kubernetes Secret, Helm values, Mimir config or
AlertmanagerConfig resources](../kubernetes.md) that hold it, and amlint checks it as you
type. You see the findings and the lines they point at, the route tree, where an alert is
delivered, and how a change affects routing. Add Prometheus alerting rules to check routes
against the alerts that exist.

**Your config stays in your browser.** amlint runs in this page as Python compiled to
WebAssembly. Nothing you paste or type is uploaded, and once the page has loaded it needs no
network.

??? info "How it works, and limits"
    - The page downloads [Pyodide](https://pyodide.org) (Python for the browser) from the
      jsDelivr CDN, plus the amlint wheel built from this site's sources. Your browser
      caches them, so later visits start faster. None of the page's requests contains your
      config.
    - Checks run in a Web Worker (a background thread of the page) and call amlint's own
      `--format json` output, so the results match `amlint check`, `amlint route` and
      `amlint diff`.
    - **Share link** puts the config, compressed, into the URL after `#`. Browsers don't
      send that part to any server, including this one. But anyone you give the link to can
      read the config, so remove secrets first; the page lists the lines that may hold them.
    - The page doesn't keep your config anywhere. Reload it and your edits are gone.
    - `template-file-missing` isn't checked for a plain `alertmanager.yml`, because its
      template files aren't in the browser. Run `amlint check` on your machine or in CI for
      that. Template files that ship in a Secret or in Helm values are checked under
      Findings, but not in the Diff tab.
