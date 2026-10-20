# Checks overview

amlint's checks catch Alertmanager config mistakes that lose alerts, send them to the wrong
place, or mute them when they shouldn't be.

amlint has **40 checks**. `amtool check-config` also catches **20** of them: Alertmanager
refuses to load those configs, and amlint reports them so one run covers everything. The
other **20** are configs that amtool accepts and Alertmanager runs, just not the way you
meant, or not for much longer. Those are the main reason to run amlint.

The three **Prometheus rules** checks run only with `amlint check --rules`, which compares
your routes and inhibition rules with the alerts your Prometheus rule files define. See
[Prometheus rules](rules.md).

From the CLI, [`amlint list`](../usage.md#list) prints the same list, and
[`amlint explain <code>`](../usage.md#explain) explains one check.

## All checks

Grouped by page, then by level. ✓ in the **amtool** column means `amtool check-config`
catches it too.

| area | check | level | amtool | what it flags |
|------|-------|-------|:------:|---------------|
| Routing | [`no-root-route`](routing.md#no-root-route) | error | ✓ | The config has no `route:` key. |
| Routing | [`invalid-root-route`](routing.md#invalid-root-route) | error | ✓ | The root route has no receiver, or has matchers, `continue: true` or time intervals. |
| Routing | [`undefined-receiver`](routing.md#undefined-receiver) | error | ✓ | A route names a receiver that isn't defined in `receivers:`. |
| Routing | [`groupby-ellipsis`](routing.md#groupby-ellipsis) | error | ✓ | `group_by` lists `'...'` next to label names. |
| Routing | [`bad-regex`](routing.md#bad-regex) | error | ✓ | A regex doesn't compile, or uses syntax Go RE2 doesn't support, like lookahead. |
| Routing | [`bad-matcher`](routing.md#bad-matcher) | error | ✓ | A `matchers:` entry Alertmanager can't parse. |
| Routing | [`bad-match-label`](routing.md#bad-match-label) | error | ✓ | A `match:` or `match_re:` key that isn't a classic label name, like `k8s.ns`. |
| Routing | [`unreachable-route`](routing.md#unreachable-route) | warn | | A catch-all route without `continue: true` hides the sibling routes after it. |
| Routing | [`route-match-collision`](routing.md#route-match-collision) | warn | | Two sibling routes have identical matchers; the second never gets alerts. |
| Routing | [`shadowed-route`](routing.md#shadowed-route) | warn | | An earlier sibling route matches every alert this route matches; this one never gets alerts. |
| Routing | [`stripped-brace`](routing.md#stripped-brace) | warn | | A bare matcher value ends in `}`, which Alertmanager strips off. |
| Routing | [`useless-continue`](routing.md#useless-continue) | info | | `continue: true` on the last sibling route does nothing. |
| Routing | [`deep-nesting`](routing.md#deep-nesting) | info | | The routing tree is more than 5 levels deep. |
| Routing | [`deprecated-match`](routing.md#deprecated-match) | info | | A route uses `match:` or `match_re:`, or an inhibition rule `source_match:` or `target_match:`. `amlint fix` rewrites them. |
| Receivers | [`duplicate-receiver`](receivers.md#duplicate-receiver) | error | ✓ | Two receivers share the same name. |
| Receivers | [`email-no-smarthost`](receivers.md#email-no-smarthost) | error | ✓ | An `email_configs` entry has no `smarthost`, and `global.smtp_smarthost` isn't set. |
| Receivers | [`webhook-no-url`](receivers.md#webhook-no-url) | error | ✓ | A `webhook_configs` entry has no `url` or `url_file`. |
| Receivers | [`pagerduty-no-routing-key`](receivers.md#pagerduty-no-routing-key) | error | ✓ | A `pagerduty_configs` entry has no `routing_key` or `service_key` (or their `_file` forms). |
| Receivers | [`slack-no-api-url`](receivers.md#slack-no-api-url) | error | ✓ | A `slack_configs` entry has no `api_url`, and `global.slack_api_url` isn't set. |
| Receivers | [`opsgenie-no-api-key`](receivers.md#opsgenie-no-api-key) | error | ✓ | An `opsgenie_configs` entry has no `api_key`, and `global.opsgenie_api_key` isn't set. |
| Receivers | [`msteams-no-webhook-url`](receivers.md#msteams-no-webhook-url) | error | ✓ | An `msteams_configs` entry has no `webhook_url` or `webhook_url_file`. |
| Receivers | [`telegram-no-bot-token`](receivers.md#telegram-no-bot-token) | error | ✓ | A `telegram_configs` entry has no `bot_token` or `bot_token_file`. |
| Receivers | [`discord-no-webhook-url`](receivers.md#discord-no-webhook-url) | error | ✓ | A `discord_configs` entry has no `webhook_url` or `webhook_url_file`. |
| Receivers | [`victorops-no-api-key`](receivers.md#victorops-no-api-key) | error | ✓ | A `victorops_configs` entry has no `api_key`, and `global.victorops_api_key` isn't set. |
| Receivers | [`wechat-no-corp-id`](receivers.md#wechat-no-corp-id) | error | ✓ | A `wechat_configs` entry has no `corp_id`, and `global.wechat_api_corp_id` isn't set. |
| Receivers | [`sns-no-target`](receivers.md#sns-no-target) | error | ✓ | An `sns_configs` entry has none of `topic_arn`, `phone_number` or `target_arn`. |
| Receivers | [`template-file-missing`](receivers.md#template-file-missing) | error | | A relative `templates:` path matches no file next to the config (warn for a glob). |
| Receivers | [`empty-receiver`](receivers.md#empty-receiver) | warn | | A receiver has no integration, like `slack_configs` (info for the root route's receiver or one named like `null`). |
| Receivers | [`unused-receiver`](receivers.md#unused-receiver) | info | | A receiver no route uses. |
| Inhibition | [`inhibit-no-equal`](inhibition.md#inhibit-no-equal) | warn | | An inhibition rule has no `equal:` field. |
| Inhibition | [`circular-inhibition`](inhibition.md#circular-inhibition) | warn | | Two inhibition rules can mute each other. |
| Inhibition | [`inhibit-same-match`](inhibition.md#inhibit-same-match) | info | | The source and target side of an inhibition rule share a `label="value"`. |
| Timing | [`undefined-time-interval`](timing.md#undefined-time-interval) | error | ✓ | `mute_time_intervals` or `active_time_intervals` names an interval that isn't defined. |
| Timing | [`repeat-before-group`](timing.md#repeat-before-group) | warn | | `repeat_interval` is shorter than `group_interval`. |
| Timing | [`wait-exceeds-interval`](timing.md#wait-exceeds-interval) | warn | | `group_wait` is longer than `group_interval`. |
| Timing | [`global-resolve-timeout-missing`](timing.md#global-resolve-timeout-missing) | info | | `global.resolve_timeout` isn't set, so Alertmanager uses its default of `5m`. |
| Timing | [`deprecated-mute-time-intervals`](timing.md#deprecated-mute-time-intervals) | info | | The top-level list is called `mute_time_intervals:`, not `time_intervals:`. `amlint fix` renames it. |
| Prometheus rules | [`route-unknown-alertname`](rules.md#route-unknown-alertname) | warn | | With `--rules`: a route matches an `alertname` no alerting rule produces. |
| Prometheus rules | [`inhibit-unknown-alertname`](rules.md#inhibit-unknown-alertname) | warn | | With `--rules`: an inhibition rule's source or target matches an `alertname` no alerting rule produces. |
| Prometheus rules | [`alert-hits-default-receiver`](rules.md#alert-hits-default-receiver) | info | | With `--rules`: alerts match no child route, by their static labels, and go to the root route's receiver. |

## Severity levels

| level | meaning |
|-------|---------|
| **error** | Alertmanager refuses the config, or notifications can't be delivered as configured. |
| **warn** | The config loads, but almost certainly doesn't do what you meant. |
| **info** | Worth a look. Often intentional. |

`amlint check` exits `1` on an error finding. Warnings fail it only with `--strict`. See
[exit codes](../usage.md#exit-codes).

## Ignoring checks

**For a whole repo:** list checks to skip, or change a check's level, in `.amlint.yml` in
the directory you run amlint from (`[tool.amlint]` in `pyproject.toml` works too). See
[project config](../usage.md#project-config).

```yaml title=".amlint.yml"
ignore:
  - unused-receiver
severity:
  empty-receiver: info       # warn → info
  deep-nesting: warn         # info → warn
```

**For one run:** `amlint check alertmanager.yml --ignore deep-nesting,unused-receiver`, or
`--only bad-regex` to show nothing else.

**For one spot in the config:** add a comment, so the exception and its reason sit next to
the config. See [inline suppressions](../usage.md#inline-suppressions).

```yaml
# amlint: disable-file=global-resolve-timeout-missing

route:
  receiver: default
  routes:
    - receiver: catch-all   # amlint: disable=unreachable-route
    - matchers: [team="infra"]
      receiver: infra
```

- `# amlint: disable=<code>,<code>` at the end of a line silences those checks on that line.
- The same comment on a line of its own silences them on the next line of YAML.
- `# amlint: disable-file=<code>,<code>` in the comment block at the top of the file, before
  any YAML, silences them for the whole file.

A finding's line is the one amlint prints with it.
