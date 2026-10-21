# Kubernetes, Grafana & Mimir

Lint the file you actually deploy: a Kubernetes Secret, kube-prometheus-stack Helm values, a
Mimir or Cortex tenant config, prometheus-operator `AlertmanagerConfig` resources, or Grafana
Alerting's provisioning files. You don't need to extract `alertmanager.yml` first, and
findings point at the line in the file you gave amlint.

## Point amlint at the file you deploy

```bash
amlint check alertmanager-secret.yaml                    # Secret or ConfigMap
amlint check values.yaml                                 # kube-prometheus-stack values
amlint check tenant-config.yaml                          # Mimir / Cortex
amlint check provisioning/alerting/*.yaml                # Grafana Alerting
amlint route alertmanagerconfigs.yaml namespace=team-a severity=critical
kubectl kustomize deploy/monitoring | amlint check -     # a whole stream on stdin
```

`check`, `diff`, `tree`, `route` and `test` all read these formats. amlint detects the
format from the content:

| If the file has… | amlint reads it as | It lints | Findings point at |
|---|---|---|---|
| a `kind: AlertmanagerConfig` anywhere | `alertmanagerconfig` | the resources, merged into the base config the way the operator merges them ([details](#prometheus-operator-alertmanagerconfig)) | the line in the resource |
| a `Secret` or `ConfigMap` with an `alertmanager.yaml`, `alertmanager.yml` or `alertmanager.yaml.gz` key | `secret` | that key; the other keys are template files | in a literal block (<code>key: &#124;</code>): the exact line. Base64 or a quoted string: the key |
| a top-level `alertmanager_config:` string | `mimir` | that string, plus `template_files` | in a literal block: the exact line. Otherwise: the key |
| top-level `contactPoints:`, `policies:`, `muteTimes:` or `templates:` lists (Grafana provisioning or export), or a config with `object_matchers` or `grafana_managed_receiver_configs` | `grafana` | the notification policies, contact points, mute timings and templates, with the provisioning files next to it ([details](#grafana)) | the line in the file |
| a top-level `alertmanager:` map, and no top-level `route:` or `receivers:` | `helm` | `alertmanager.config` merged over the chart's defaults, plus `alertmanager.templateFiles` | the line in the values file |
| none of the above | `alertmanager` | the file as a plain config | the line in the file |

- A file can be a stream of YAML documents (`---`) or a `kind: List`, as
  `kubectl get -o yaml` prints it. amlint skips other objects in it (Deployments, Services,
  PrometheusRules, and Secrets or ConfigMaps without a config key).
- amlint lints one config per file. A file with two configs (two config Secrets, say) exits
  2: split it, or, if the two are in different formats, pick one with `--input-format`. A
  file with no config at all also exits 2.
- `note:` lines on stderr say how amlint read the file, for example which parts of a Helm
  config come from the chart. They don't change the exit code.

To skip detection, pass `--input-format` with `auto` (the default), `alertmanager`, `secret`,
`helm`, `mimir`, `alertmanagerconfig` or `grafana`. `--input-format alertmanager` reads the file as
one plain document, even if it looks like something else.

## Secrets and ConfigMaps { #kubernetes-secrets }

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

- **Use a literal block** (`alertmanager.yaml: |`, YAML's multi-line string that keeps
  line breaks). Then every finding points at its line and column inside the block, and
  `# amlint: disable=...` comments inside the block work as in a plain file. With base64
  `data` or a quoted string, lines can't be mapped one to one, so findings point at the
  `alertmanager.yaml:` key.
- `stringData` wins over `data` for the same key, as the API server merges them. `data`
  values are base64.
- `alertmanager.yaml.gz` is the gzipped, base64 config in the Secret the operator generates
  (`alertmanager-<name>-generated`).
- **Template files.** The Secret's other keys are template files. The operator mounts them
  in `/etc/alertmanager/config/`, so amlint checks `templates:` entries in that directory
  against the keys instead of against files on disk:
    - a file name that isn't a key is an error
    - a glob that matches no key is a warning, but only if the Secret has template files at
      all (`*.tmpl` with no template files just loads nothing)
    - relative paths and other directories aren't checked. The operator runs Alertmanager on
      a copy of the config in `config_out/`, so they don't resolve next to the Secret.
- A **ConfigMap** is read the same way, from `data` or base64 `binaryData`. Its template
  files aren't checked, because where it is mounted depends on how you deploy it.

## kube-prometheus-stack Helm values { #kube-prometheus-stack-values }

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

Helm merges your values over the chart's own `values.yaml`, so Alertmanager gets more than
your file says. amlint merges `alertmanager.config` over the default config of
kube-prometheus-stack 92.10.1 the same way: maps key by key, lists replaced whole, and
`null` deletes a key.

That catches a common mistake. The values above replace `receivers`, so the chart's `null`
receiver is gone, but the chart's Watchdog route to `null` is still there:

```
$ amlint check values.yaml
note: values.yaml:2: alertmanager.config is merged over the kube-prometheus-stack 92.10.1 defaults, as Helm merges values: global, inhibit_rules, templates, route.routes come from the chart

  ✖  ERROR  [undefined-receiver]  ·  values.yaml:3:5  ·  route.routes[0]
     Route references receiver 'null' which is not defined in receivers. Alertmanager refuses to load this config.

──────────────────────────────────────────────────────────────────────────────
  ✖ 1 error
```

A finding in a part that only the chart's defaults have points at the nearest part your
file does have: here, the `route:` that the default routes are merged into.

- `alertmanager.templateFiles` end up in the config Secret, so `templates:` entries in
  `/etc/alertmanager/config/` are checked against them, as for a Secret.
- `alertmanager.stringConfig` is used only with `alertmanager.tplConfig: true`, as in the
  chart's Secret template. Without it the chart renders `alertmanager.config`, and amlint
  notes that the string is ignored. A string `alertmanager.config` is read as a config.
- `alertmanager.alertmanagerSpec.alertmanagerConfigMatcherStrategy.type` sets the
  [matcher strategy](#matcher-strategy) for `AlertmanagerConfig` resources in the same
  stream.
- Values from `--set` or other values files aren't in the file. Lint the values Helm
  actually uses, or the rendered chart, which holds the config Secret:

    ```bash
    helm get values <release> --all -o yaml | amlint check -
    helm template monitoring ./chart | amlint check -
    ```

## Mimir and Cortex { #mimir-and-cortex }

This is the format `mimirtool alertmanager get` prints and `POST /api/v1/alerts` takes:

```yaml
template_files:
  default_template: |
    {{ define "email.subject" }}...{{ end }}
alertmanager_config: |
  route:
    receiver: team-email
  ...
```

amlint doesn't check `templates:` against `template_files`: Mimir loads every template file
of the tenant, whatever `templates:` lists, and refuses entries that contain a path.

## Grafana Alerting { #grafana }

Grafana runs its own Alertmanager. Its notification policies, contact points, mute timings
and templates work the same way, so amlint checks them, and `route`, `test` and `diff`
simulate their routing.

```bash
amlint check provisioning/alerting/*.yaml         # file provisioning
amlint check notification-policies.yaml           # an export (Alerting → Export, or the API)
amlint route provisioning/alerting/policies.yaml grafana_folder=Infra alertname=DiskFull
```

amlint reads:

- **file provisioning** (`contactPoints`, `policies`, `muteTimes`, `templates`), and the same
  format Grafana exports (`/api/v1/provisioning/policies/export`, contact points, mute
  timings);
- **Grafana's Alertmanager config**, as `GET /api/alertmanager/grafana/config/api/v1/alerts`
  returns it (`alertmanager_config` with `template_files`), or without that wrapper.

**Good to know**

- Grafana reads every `.yaml`, `.yml` and `.json` file of the provisioning directory
  together, and so does amlint: a policy in one file can use a contact point from another.
  Each file's findings are reported when you check that file.
- Contact points, mute timings and templates made in Grafana's UI aren't in these files. A
  policy that uses one is not an `undefined-receiver`: a `note:` lists the names amlint
  takes to be in Grafana. Templates are looked up only when the files define some.
- Every alert from Grafana has an `alertname` label (the rule's title) and `grafana_folder`.
  Use them in `amlint route` and in routing tests.
- One org at a time: org 1, or the lowest one in the files. A `note:` says which.
- `$$` is a plain `$`. `$VAR`, `${VAR}`, `$__env{…}`, `$__file{…}` and an exported
  `[REDACTED]` stand for a secret kept elsewhere, so they aren't a `plaintext-secret`.
- [`grafana-contact-point`](checks/receivers.md#grafana-contact-point) reports a type or a
  required setting Grafana refuses. Templates are checked with Grafana's functions and data
  (`.ValueString`, `.DashboardURL`, `coll.Dict`, …).
- `unused-receiver` and `global-resolve-timeout-missing` don't apply: Grafana adds a route
  for every contact point itself, for alert rules that pick one directly, and has no
  `global:` section.
- Not modeled: the routes Grafana adds for alert rules that pick a contact point directly,
  named routing trees, and inhibition rules, which provisioning files can't hold.
- Messages say "Alertmanager" where they mean the Alertmanager that runs inside Grafana.

## prometheus-operator AlertmanagerConfig { #prometheus-operator-alertmanagerconfig }

`AlertmanagerConfig` resources (`monitoring.coreos.com/v1alpha1` and `v1beta1`) let each team
define its own routes and receivers in its own namespace. The operator converts them and
merges them into one Alertmanager config. amlint does the same, the way prometheus-operator
v0.94.1 does it, so findings, `tree`, `route` and `test` show the config Alertmanager will
actually run.

```
$ amlint tree alertmanagerconfigs.yaml

   (root)  →  default
├──    {namespace="team-a"}  →  team-a/payments/slack  [continue]
│   └──    {severity="critical"}  →  team-a/payments/pager
├──    {service=~"search-.*", namespace="team-b"}  →  team-b/search/webhook  [continue]
└──    {alertname = "Watchdog"}  →  null

  1 more issue outside routing (receivers, global, timing) — run 'amlint check alertmanagerconfigs.yaml' for full details.
```

??? example "alertmanagerconfigs.yaml: a config Secret and two teams' resources"

    ```yaml
    apiVersion: v1
    kind: Secret
    metadata:
      name: alertmanager-main
      namespace: monitoring
    stringData:
      alertmanager.yaml: |
        global:
          resolve_timeout: 5m
        route:
          receiver: default
          routes:
            - matchers: ['alertname = "Watchdog"']
              receiver: "null"
        receivers:
          - name: default
            webhook_configs:
              - url: http://example.com/default
          - name: "null"
    ---
    apiVersion: monitoring.coreos.com/v1alpha1
    kind: AlertmanagerConfig
    metadata:
      name: payments
      namespace: team-a
    spec:
      route:
        receiver: slack
        routes:
          - matchers:
              - name: severity
                value: critical
            receiver: pager
      receivers:
        - name: slack
          slackConfigs:
            - apiURL:
                name: slack-webhook
                key: url
              channel: '#payments'
        - name: pager
          pagerdutyConfigs:
            - routingKey:
                name: pagerduty
                key: routing-key
    ---
    apiVersion: monitoring.coreos.com/v1beta1
    kind: AlertmanagerConfig
    metadata:
      name: search
      namespace: team-b
    spec:
      route:
        receiver: webhook
        matchers:
          - name: service
            value: search-.*
            matchType: "=~"
      receivers:
        - name: webhook
          webhookConfigs:
            - url: http://search.example.com/hook
    ```

What the merge does:

- **Names.** Receiver and time interval names become `<namespace>/<name>/<receiver>`, so
  `receiver: pager` in `team-a/payments` is `team-a/payments/pager`. Findings, `tree`,
  `route` and `test` all use these names.
- **Namespace matcher.** Each resource's route gets `namespace="<namespace>"` added to its
  matchers, and so do both sides of each of its inhibit rules (unless a side already has
  it). So a resource only sees alerts from its own namespace. The
  [matcher strategy](#matcher-strategy) can turn this off.
- **Order and `continue`.** The resources' routes go first under the root route, sorted by
  `namespace/name`, before the base config's own routes. Each gets `continue: true`, so
  alerts also go on to the routes after it.
- **Base config.** A Secret, plain config, Helm values or Mimir config in the same stream is
  the base. Without one, amlint uses the operator's default config (a root route to a
  `null` receiver) and drops findings about that default, since there is nothing in your
  input to fix.
- **Secret references.** Fields that reference a Secret (`apiURL`, `routingKey`, `apiKey`,
  `urlSecret`, `botToken`, ...) count as set, because the operator fills in the value.
  amlint doesn't read the value, so it can't catch a Secret that doesn't exist.
- **Field names.** camelCase fields become Alertmanager's (`slackConfigs` →
  `slack_configs`, `groupWait` → `group_wait`). A webhook's `urlSecret` becomes its `url`, a
  Discord `apiURL` its `webhook_url`. A zero `groupInterval` or `repeatInterval` is dropped,
  as the operator drops it, so the parent's value applies.
- **API versions.** v1alpha1 calls the top-level time intervals `muteTimeIntervals`, v1beta1
  calls them `timeIntervals`. If a resource uses the other version's name, amlint notes that
  the API server drops it. A v1alpha1 matcher without `matchType` uses `regex: true` for
  `=~`.
- **No `spec.route`.** A resource without a route only contributes its inhibit rules. The
  operator drops its receivers and time intervals, and amlint notes it.
- **`spec.alertmanagerConfiguration`.** If an `Alertmanager` resource in the input names one
  of the resources there, that resource becomes the whole config, without a namespace
  matcher, as the operator does it.

Some findings would only describe what the operator adds on purpose, so amlint drops them:
`inhibit-same-match`, `inhibit-no-equal` and `circular-inhibition` when only the namespace
matcher causes them, and `useless-continue` on a resource's route. Problems in your own
rules are still reported.

### Matcher strategy

`alertmanagerConfigMatcherStrategy` decides which resources get the namespace matcher:

| Strategy | Namespace matcher |
|---|---|
| `OnNamespace` (default) | added to every resource |
| `OnNamespaceExceptForAlertmanagerNamespace` | added, except to resources in the Alertmanager's own namespace |
| `None` | never added |

amlint takes the first of these it finds: `--matcher-strategy`, an `Alertmanager` resource in
the input (`spec.alertmanagerConfigMatcherStrategy.type`), Helm values in the input
(`alertmanager.alertmanagerSpec.alertmanagerConfigMatcherStrategy.type`). Otherwise it uses
`OnNamespace`.

For `OnNamespaceExceptForAlertmanagerNamespace`, the Alertmanager's namespace comes from the
`Alertmanager` resource or the config Secret. If neither is in the input, every resource gets
the matcher, and amlint notes it.

### Routing tests

Test cases run against the merged config. So their labels need a `namespace`, and they use
the operator's receiver names:

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

```
$ amlint test alertmanagerconfigs.yaml routing-tests.yaml
  ✓  payments critical alerts page
  ✓  another namespace never reaches team-a
  ✓  team-a's route has continue, so the base routes still see its alerts
──────────────────────────────────────────────────────────────────────────────
  3 passed
```

### Lint several files together { #several-files }

The operator merges every selected resource, and amlint merges every resource in one input.
To lint resources from several files together, send them as one stream:

```bash
kubectl kustomize deploy/monitoring | amlint check -
helm template monitoring ./chart | amlint check -
kubectl get alertmanagerconfigs -A -o yaml | amlint check -
for f in alertmanagerconfigs/*.yaml; do echo ---; cat "$f"; done | amlint check -
```

With stdin, findings point at lines of the stream. `amlint check a.yaml b.yaml` instead
lints each file on its own, so each resource is merged into the operator's default config.

### Limits { #what-isnt-modeled }

- `alertmanagerConfigSelector` and `alertmanagerConfigNamespaceSelector` aren't modeled:
  every resource in the input counts as selected.
- The operator's validation isn't modeled. The operator skips a whole resource it rejects
  (say, a route to a receiver the resource doesn't define). amlint reports what is wrong
  instead.
- Matchers are always written in the `matchers:` syntax, so configs for Alertmanager before
  0.22 aren't modeled.
- A resource without `metadata.namespace` is taken to be in `default`, with a note.
