# Template checks

These checks catch notification templates that break when Alertmanager sends a
notification. Alertmanager doesn't look at them when it loads the config, so a broken
template ships fine and fails at 3 a.m.: the notification isn't sent, or with Slack, it goes
out with an empty title or text.

A **template** is Go template text in `{{ }}`. It sits in a notifier field such as Slack's
`title:` or `text:`, or in a template file listed under `templates:` that defines named
templates with `{{ define "name" }}`. A field calls one with `{{ template "name" . }}`.

The data a field runs on is the notification: `.Status`, `.Receiver`, `.CommonLabels`,
`.CommonAnnotations`, `.GroupLabels`, `.ExternalURL`, and `.Alerts`, the list of alerts.
Inside `{{ range .Alerts }}`, the dot is one alert: `.Labels`, `.Annotations`,
`.StartsAt`, `.EndsAt`, `.GeneratorURL`, `.Fingerprint`. `$` is always the notification.

amlint parses templates the way Alertmanager does, with the functions Alertmanager 0.34
defines. Template files are read next to the config file (relative `templates:` paths), or
from the Secret, Helm `templateFiles` or Mimir `template_files` they ship in. When some file
can't be read (an absolute path into the Alertmanager container), the templates the fields
call aren't looked up.

---

## bad-template-file

**Level:** error · **amtool:** also catches it

**What goes wrong:** A template file under `templates:` isn't a valid Go template: an action
that isn't closed, an `{{ end }}` too many or too few, or a function Alertmanager doesn't
have.

```text title="templates/slack.tmpl (bad)"
{{ define "slack.title" }}
  [{{ .Status | toUpperCase }}] {{ .CommonLabels.alertname }}
{{ end }}
```

**What Alertmanager does:** Refuses to load the config: `function "toUpperCase" not
defined`.

**How to fix:** Fix the line amlint points at. The functions are Go's (`printf`, `len`,
`index`, `eq`, ...) and Alertmanager's (`toUpper`, `toLower`, `title`, `join`, `match`,
`reReplaceAll`, `safeHtml`, `stringSlice`, `trimSpace`, and in newer versions `date`, `tz`,
`since`, `humanizeDuration`, `toJson`, `list`, `dict`, ...).

```text title="templates/slack.tmpl (fixed)"
{{ define "slack.title" }}
  [{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}
{{ end }}
```

---

## bad-template

**Level:** error · **amtool:** doesn't catch it

**What goes wrong:** The template text in a notifier field doesn't parse: a `}` missing, an
`{{ end }}` missing, or a function Alertmanager doesn't have.

```yaml title="Bad"
receivers:
  - name: team-slack
    slack_configs:
      - channel: "#alerts"
        title: '[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }'
```

**What Alertmanager does:** Loads the config. When it sends a notification, the template
fails: the notification isn't sent, and Slack sends the message with this field and the
fields after it empty. A webhook `url` with `{{` is the exception: Alertmanager 0.31 and later
parse it at load time and refuse the config.

**How to fix:** Fix the template. amlint gives the line and the problem, with the same
message Go gives.

```yaml title="Fixed"
receivers:
  - name: team-slack
    slack_configs:
      - channel: "#alerts"
        title: '[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}'
```

---

## undefined-template

**Level:** error · **amtool:** doesn't catch it

**What goes wrong:** A field, or a template file, calls `{{ template "name" }}` for a name
that no template file and none of Alertmanager's own templates (such as
`slack.default.title`) define. Usually a typo, or a file that isn't listed under
`templates:`.

```yaml title="Bad"
templates:
  - templates/*.tmpl        # defines "slack.myorg.title"
receivers:
  - name: team-slack
    slack_configs:
      - channel: "#alerts"
        title: '{{ template "slack.myorg.titel" . }}'
```

**What Alertmanager does:** Loads the config, and `amtool check-config` passes. When it sends
a notification, the template fails with `no such template "slack.myorg.titel"`.

**How to fix:** Use the name the file defines; amlint suggests the closest one. Check that
the `templates:` glob matches the file.

```yaml title="Fixed"
templates:
  - templates/*.tmpl
receivers:
  - name: team-slack
    slack_configs:
      - channel: "#alerts"
        title: '{{ template "slack.myorg.title" . }}'
```

Only checked when amlint can read every template file the config loads.

---

## template-unknown-field

**Level:** error · **amtool:** doesn't catch it

**What goes wrong:** A template reads a field the data doesn't have. The usual ones:

- `.Labels` or `.Annotations` on the notification. Those are fields of each alert; the
  notification has `.CommonLabels` and `.CommonAnnotations`.
- `.CommonLabels`, `.Receiver` or `.ExternalURL` inside `{{ range .Alerts }}`, where the dot
  is one alert. Use `$.CommonLabels`.
- A typo, such as `.Commonlabels`.

```yaml title="Bad"
receivers:
  - name: team-slack
    slack_configs:
      - channel: "#alerts"
        title: '{{ .Labels.alertname }}'
        text: '{{ range .Alerts }}{{ .Labels.pod }} in {{ .CommonLabels.env }}{{ end }}'
```

**What Alertmanager does:** Loads the config. When it sends a notification, the template
fails with `can't evaluate field Labels in type *template.Data`: the notification isn't
sent, and Slack sends it with this field and the fields after it empty.

**How to fix:** Use the field where it exists.

```yaml title="Fixed"
receivers:
  - name: team-slack
    slack_configs:
      - channel: "#alerts"
        title: '{{ .CommonLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Labels.pod }} in {{ $.CommonLabels.env }}{{ end }}'
```

Labels themselves are never a problem: `.CommonLabels.anything` is empty when no such label
exists. In template files, a `{{ define }}` is checked with the data the fields call it with
(`{{ template "name" . }}`); one called with different data in different places, or never
called from a field, isn't checked.
