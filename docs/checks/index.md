# Checks overview

amlint runs **34 checks** grouped into four categories. 20 of them duplicate validation
that `amtool check-config` already does (Alertmanager refuses to start on these — included
for unified output, not because amtool misses them). The other 14 are configs that load
and run fine, and just don't do what you meant — the real reason to run amlint at all.

| code | level | also in amtool | category |
|------|-------|:---:|----------|
| [`undefined-receiver`](routing.md#undefined-receiver) | error | ✓ | Routing |
| [`no-root-route`](routing.md#no-root-route) | error | ✓ | Routing |
| [`invalid-root-route`](routing.md#invalid-root-route) | error | ✓ | Routing |
| [`bad-regex`](routing.md#bad-regex) | error | ✓ | Routing |
| [`bad-matcher`](routing.md#bad-matcher) | error | ✓ | Routing |
| [`bad-match-label`](routing.md#bad-match-label) | error | ✓ | Routing |
| [`groupby-ellipsis`](routing.md#groupby-ellipsis) | error | ✓ | Routing |
| [`unreachable-route`](routing.md#unreachable-route) | warn | | Routing |
| [`route-match-collision`](routing.md#route-match-collision) | warn | | Routing |
| [`stripped-brace`](routing.md#stripped-brace) | warn | | Routing |
| [`useless-continue`](routing.md#useless-continue) | info | | Routing |
| [`deep-nesting`](routing.md#deep-nesting) | info | | Routing |
| [`duplicate-receiver`](receivers.md#duplicate-receiver) | error | ✓ | Receivers |
| [`empty-receiver`](receivers.md#empty-receiver) | warn/info | | Receivers |
| [`email-no-smarthost`](receivers.md#email-no-smarthost) | error | ✓ | Receivers |
| [`webhook-no-url`](receivers.md#webhook-no-url) | error | ✓ | Receivers |
| [`pagerduty-no-routing-key`](receivers.md#pagerduty-no-routing-key) | error | ✓ | Receivers |
| [`slack-no-api-url`](receivers.md#slack-no-api-url) | error | ✓ | Receivers |
| [`opsgenie-no-api-key`](receivers.md#opsgenie-no-api-key) | error | ✓ | Receivers |
| [`msteams-no-webhook-url`](receivers.md#msteams-no-webhook-url) | error | ✓ | Receivers |
| [`telegram-no-bot-token`](receivers.md#telegram-no-bot-token) | error | ✓ | Receivers |
| [`discord-no-webhook-url`](receivers.md#discord-no-webhook-url) | error | ✓ | Receivers |
| [`victorops-no-api-key`](receivers.md#victorops-no-api-key) | error | ✓ | Receivers |
| [`wechat-no-corp-id`](receivers.md#wechat-no-corp-id) | error | ✓ | Receivers |
| [`sns-no-target`](receivers.md#sns-no-target) | error | ✓ | Receivers |
| [`template-file-missing`](receivers.md#template-file-missing) | error/warn | | Receivers |
| [`unused-receiver`](receivers.md#unused-receiver) | info | | Receivers |
| [`inhibit-no-equal`](inhibition.md#inhibit-no-equal) | warn | | Inhibition |
| [`circular-inhibition`](inhibition.md#circular-inhibition) | warn | | Inhibition |
| [`inhibit-same-match`](inhibition.md#inhibit-same-match) | info | | Inhibition |
| [`repeat-before-group`](timing.md#repeat-before-group) | warn | | Timing |
| [`wait-exceeds-interval`](timing.md#wait-exceeds-interval) | warn | | Timing |
| [`undefined-time-interval`](timing.md#undefined-time-interval) | error | ✓ | Timing |
| [`global-resolve-timeout-missing`](timing.md#global-resolve-timeout-missing) | info | | Timing |
| [`route-unknown-alertname`](rules.md#route-unknown-alertname) | warn | | Prometheus rules |
| [`inhibit-unknown-alertname`](rules.md#inhibit-unknown-alertname) | warn | | Prometheus rules |
| [`alert-hits-default-receiver`](rules.md#alert-hits-default-receiver) | info | | Prometheus rules |

The three **Prometheus rules** checks are extra: they run only with `amlint check --rules`,
which reads your Prometheus alerting rules and checks routes and inhibit rules against the
alerts they produce. See [Prometheus rules](rules.md).

Same info from the CLI: `amlint list` shows the same column; `amlint explain <code>` says
it for any individual check.

## Severity levels

| level | meaning |
|-------|---------|
| **error** | Config is broken — alerts will be lost or Alertmanager will reject the config |
| **warn** | Almost certainly not what you intended |
| **info** | Suspicious, worth reviewing — may be intentional |

