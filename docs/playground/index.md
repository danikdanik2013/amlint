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
type. You get the findings and the lines they point at, the route tree, where a given alert
is delivered, and how a change affects routing.

**Your config stays in your browser.** amlint runs on this page as Python compiled to
WebAssembly. Nothing you paste or type is uploaded, and the page works offline once it has
loaded.

??? info "How it works"
    - The page downloads [Pyodide](https://pyodide.org) (Python for the browser) from the
      jsDelivr CDN, plus the amlint wheel built from this site's sources. Your browser
      caches them, so later visits start faster. None of the page's requests contains your
      config.
    - Checks run in a Web Worker: the same code and the same JSON output as
      `amlint check --format json`, `amlint route` and `amlint diff`.
    - **Share link** puts the config into the URL after `#`. Browsers don't send that part
      to any server, including this one, but anyone you give the link to can read the
      config, so remove secrets first. The page doesn't keep your config anywhere: reload
      it and your edits are gone.
    - `template-file-missing` isn't checked for a plain `alertmanager.yml` here, because its
      template files aren't in the browser. Run `amlint check` on your machine or in CI for
      that. Template files that ship in a Secret, Helm values or a Mimir config are checked.
