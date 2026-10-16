# Kubernetes & Mimir

Most Alertmanagers don't read a hand-written `alertmanager.yml`. They get their config from a
Kubernetes Secret, from kube-prometheus-stack Helm values, from prometheus-operator
`AlertmanagerConfig` resources, or from a Mimir or Cortex tenant config. amlint reads all of
these directly. `check`, `tree`, `route`, `test` and `diff` work the same on each, and
findings point at the line in the file you gave it, not at some config it generated.

```bash
amlint check alertmanager-secret.yaml
amlint check values.yaml
amlint check tenant-config.yaml
kubectl kustomize deploy/monitoring | amlint check -
amlint route alertmanagerconfigs.yaml namespace=team-a severity=critical
```

| input | what amlint reads | findings point at |
|-------|-------------------|-------------------|
| plain `alertmanager.yml` | the file, as before | the line in the file |
| Kubernetes `Secret` / `ConfigMap` | `alertmanager.yaml` in `stringData` or base64 `data`, other keys as template files | inside a `\|` block: the exact line; base64 or a quoted string: the `alertmanager.yaml:` key |
| kube-prometheus-stack values | `alertmanager.config` merged over the chart's default, `alertmanager.templateFiles` | the line in the values file |
| Mimir / Cortex | `alertmanager_config` (a string) and `template_files` | inside a `\|` block: the exact line, otherwise the key |
| `AlertmanagerConfig` (v1alpha1, v1beta1) | the resources, merged into the base config the way prometheus-operator merges them | the line in the resource |

## Detection and `--input-format`

The format is detected from the content. A file can be a stream of YAML documents
(`---`) and can contain `kind: List` objects, as `kubectl get -o yaml` prints them. Other
Kubernetes objects in the stream (Deployments, Services, PrometheusRules) are skipped.

- An `AlertmanagerConfig` anywhere in the stream makes it an `alertmanagerconfig` input. A
  Secret, values file, Mimir config or plain config in the same stream is the base config
  the resources are merged into.
- A `Secret` or `ConfigMap` with an `alertmanager.yaml`, `alertmanager.yml` or
  `alertmanager.yaml.gz` key is a `secret` input. One without such a key is skipped.
- A top-level `alertmanager_config:` string is a `mimir` input.
- A top-level `alertmanager:` section, and no top-level `route:` or `receivers:`, is a
  `helm` input. Alertmanager rejects unknown top-level keys, so a plain config can't have one.
- Anything else is a plain Alertmanager config, read exactly as amlint always read it.

`--input-format` (on `check`, `diff`, `tree`, `route` and `test`) overrides the detection:
`auto` (the default), `alertmanager`, `secret`, `helm`, `mimir` or `alertmanagerconfig`.
`--input-format alertmanager` reads the file as one plain document, even if it looks like
something else. A file that holds two Alertmanager configs (two config Secrets, say) exits 2:
amlint lints one config per file.

Notes about how a file was read, like which parts of a Helm config come from the chart's
defaults, go to stderr as `note:` lines and don't change the exit code.

## Kubernetes Secrets

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: alertmanager-main
  namespace: monitoring
stringData:
  alertmanager.yaml: |-
    route:
      receiver: default
      ...
  notify.tmpl: |-
    {{ define "notify.description" }}...{{ end }}
```

- `stringData` wins over `data` for the same key, as the API server merges them. `data`
  values are base64. `alertmanager.yaml.gz` is the gzipped config in the Secret the operator
  generates (`alertmanager-<name>-generated`).
- With a literal block (`alertmanager.yaml: |`), every finding points at its line and column
  inside the block, and `# amlint: disable=...` comments inside the block work as in a
  plain file. With base64 data or a quoted string the lines can't be mapped one to one, so
  findings point at the `alertmanager.yaml:` key.
- The Secret's other keys are template files: the operator mounts them in
  `/etc/alertmanager/config/`. A `templates:` entry in that directory is checked against
  them instead of against files on disk. A file name that isn't a key is an error, and a glob
  that matches none of them is a warning. A glob is only reported if the Secret has template
  files at all, since `*.tmpl` with no template files just loads nothing. Relative paths and
  other directories aren't checked: the operator runs Alertmanager on a copy of the config in
  `config_out/`, so they don't resolve next to the Secret.
- A `ConfigMap` (`data`, or base64 `binaryData`) is read the same way. Its template files
  aren't checked, because where it is mounted depends on the chart.

## kube-prometheus-stack values

```yaml
alertmanager:
  config:
    route:
      receiver: slack
    receivers:
      - name: slack
        slack_configs:
          - api_url: https://hooks.slack.com/services/T/B/X
```

Helm merges your values over the chart's own `values.yaml`, so the config Alertmanager gets
isn't only what your file says. amlint merges `alertmanager.config` over the default config
of kube-prometheus-stack 92.10.1 the way Helm does: maps key by key, lists replaced
whole, and `null` deletes a key. That catches a common mistake. The values above replace
`receivers`, so the chart's `null` receiver is gone, but the chart's Watchdog route to
`null` is still there:

```
note: values.yaml:2: alertmanager.config is merged over the kube-prometheus-stack 92.10.1 defaults, as Helm merges values: global, inhibit_rules, templates, route.routes come from the chart

  ✖  ERROR  [undefined-receiver]  ·  values.yaml:3:5  ·  route.routes[0]
     Route references receiver 'null' which is not defined in receivers. Alerts matched here will be
dropped.
```

A finding in a part that only exists in the chart's defaults points at the nearest part your
file does have, here the `route:` the default routes are merged into.

- `alertmanager.templateFiles` end up in the config Secret, so `templates:` entries in
  `/etc/alertmanager/config/` are checked against them, as for a Secret.
- `alertmanager.stringConfig` is only used with `alertmanager.tplConfig: true`, which is
  what the chart's Secret template does. Without it the chart renders `alertmanager.config`,
  and amlint notes that the string is ignored. A string `alertmanager.config` is read as a
  config.
- `alertmanager.alertmanagerSpec.alertmanagerConfigMatcherStrategy` is used for
  AlertmanagerConfig resources in the same stream.
- Values given with `--set` or in other values files aren't in the file. Lint the values
  Helm actually uses (`helm get values <release> --all`), or render the chart with
  `helm template` and lint its output, which holds the config Secret.

## Mimir and Cortex

The format `mimirtool alertmanager get` prints and `POST /api/v1/alerts` takes:

```yaml
template_files:
  default_template: |
    {{ define "email.subject" }}...{{ end }}
alertmanager_config: |
  route:
    receiver: team-email
  ...
```

`templates:` isn't checked against `template_files`. Mimir loads every template file of the
tenant, whatever `templates:` lists, and refuses entries that contain a path.

## prometheus-operator AlertmanagerConfig

`AlertmanagerConfig` resources (`monitoring.coreos.com/v1alpha1` and `v1beta1`) are
converted into Alertmanager's config and merged into the base config the way
prometheus-operator v0.94.1 does it (`pkg/alertmanager/amcfg.go`):

- **Names.** Receiver and time interval names become `<namespace>/<name>/<receiver>`, so
  `receiver: pager` in `team-a/payments` is `team-a/payments/pager`. Findings, `tree`,
  `route` and `test` all use these names.
- **Namespace matcher.** Each resource's route gets `namespace="<namespace>"` added to its
  matchers, and so do both sides of each of its inhibit rules (unless a side already has
  it). So a resource only sees alerts from its own namespace. This follows
  `alertmanagerConfigMatcherStrategy`:
  `OnNamespace` (the default) adds it, `None` doesn't, and
  `OnNamespaceExceptForAlertmanagerNamespace` doesn't for resources in the Alertmanager's
  own namespace. The strategy comes from `--matcher-strategy`, else from an `Alertmanager`
  resource in the input, else from Helm values in the input, else it is `OnNamespace`.
- **Order and `continue`.** The resources' routes go first under the root route, sorted by
  `namespace/name`, and before the base config's own routes. Each has `continue: true`, so
  alerts also go on to the routes after it.
- **Secrets.** Fields that reference a Secret (`apiURL`, `routingKey`, `apiKey`,
  `urlSecret`, `botToken`, ...) count as set: the operator fills in the Secret's value. The
  value itself isn't read, so a Secret that doesn't exist isn't caught.
- **Fields.** camelCase fields become Alertmanager's (`slackConfigs` → `slack_configs`,
  `groupWait` → `group_wait`). A webhook's `urlSecret` is its `url`, a Discord `apiURL` its
  `webhook_url`. A zero `groupInterval` or `repeatInterval` is dropped, as the operator drops
  it, so the parent's value applies.
- **Versions.** v1alpha1 spells the top-level time intervals `muteTimeIntervals`, v1beta1
  `timeIntervals`, and amlint notes the other one, which the API server drops. A v1alpha1
  matcher without `matchType` uses `regex: true` for `=~`.
- **Without a route.** A resource without `spec.route` only contributes its inhibit rules;
  the operator drops its receivers and time intervals, and amlint notes it.
- **Base config.** A Secret, plain config, Helm values or Mimir config in the same stream is
  the base. Without one, it is the operator's default config (a root route to a `null`
  receiver), and findings about that default are dropped, since there is nothing in the
  input to fix. An `Alertmanager` resource whose `spec.alertmanagerConfiguration.name` names
  one of the resources makes that resource the whole config, without a namespace matcher,
  as the operator does.

Some findings would only describe what the operator adds on purpose, so they are dropped:
`inhibit-same-match`, `inhibit-no-equal` and `circular-inhibition` when only the namespace
matcher causes them (it is on both sides of every rule of a resource), and
`useless-continue` on a resource's route when it happens to be the last one. Problems in the
rules themselves are still reported.

```
$ amlint tree alertmanagerconfigs.yaml

   (root)  →  default
├──    {namespace="team-a"}  →  team-a/payments/slack  [continue]
│   └──    {severity="critical"}  →  team-a/payments/pager
├──    {service=~"search-.*", namespace="team-b"}  →  team-b/search/webhook  [continue]
└──    {alertname = "Watchdog"}  →  null
```

### Routing tests

Test cases see the merged config, so their labels need a `namespace`, and receivers are
named the operator's way:

```yaml
tests:
  - name: payments critical alerts page
    labels: {namespace: team-a, alertname: PaymentsDown, severity: critical}
    expect: team-a/payments/pager
  - name: another namespace never reaches team-a
    labels: {namespace: team-c, alertname: PaymentsDown, severity: critical}
    expect: default
  - name: team-a's route has continue, so the base routes still see its alerts
    labels: {namespace: team-a, alertname: Watchdog}
    expect: [team-a/payments/slack, "null"]
```

### Several files

The operator merges every selected resource, and amlint merges every resource in one input.
To lint resources from several files together, put them in one stream:

```bash
kubectl kustomize deploy/monitoring | amlint check -
helm template monitoring ./chart | amlint check -
kubectl get alertmanagerconfigs -A -o yaml | amlint check -
for f in alertmanagerconfigs/*.yaml; do echo ---; cat "$f"; done | amlint check -
```

With stdin, findings point at lines of the stream. Passing the files one by one
(`amlint check a.yaml b.yaml`) lints each resource on its own, under the operator's default
config.

### What isn't modeled

- `alertmanagerConfigSelector` and `alertmanagerConfigNamespaceSelector`: every resource in
  the input counts as selected.
- The operator's validation, which skips a whole resource it rejects (say, a route to a
  receiver the resource doesn't define). amlint reports what is wrong instead.
- Alertmanager versions before 0.22: matchers are always written in the `matchers:` syntax.
- A resource without `metadata.namespace` is taken to be in `default`, with a note.
