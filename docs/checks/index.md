# Checks overview

amlint runs **25 checks** grouped into four categories.

| code | level | category |
|------|-------|----------|
| [`undefined-receiver`](routing.md#undefined-receiver) | error | Routing |
| [`no-root-route`](routing.md#no-root-route) | error | Routing |
| [`bad-regex`](routing.md#bad-regex) | error | Routing |
| [`unreachable-route`](routing.md#unreachable-route) | warn | Routing |
| [`route-match-collision`](routing.md#route-match-collision) | warn | Routing |
| [`groupby-ellipsis`](routing.md#groupby-ellipsis) | warn | Routing |
| [`useless-continue`](routing.md#useless-continue) | info | Routing |
| [`deep-nesting`](routing.md#deep-nesting) | info | Routing |
| [`duplicate-receiver`](receivers.md#duplicate-receiver) | error | Receivers |
| [`empty-receiver`](receivers.md#empty-receiver) | warn/info | Receivers |
| [`email-no-smarthost`](receivers.md#email-no-smarthost) | error | Receivers |
| [`webhook-no-url`](receivers.md#webhook-no-url) | error | Receivers |
| [`pagerduty-no-routing-key`](receivers.md#pagerduty-no-routing-key) | error | Receivers |
| [`slack-no-api-url`](receivers.md#slack-no-api-url) | error | Receivers |
| [`opsgenie-no-api-key`](receivers.md#opsgenie-no-api-key) | error | Receivers |
| [`msteams-no-webhook-url`](receivers.md#msteams-no-webhook-url) | error | Receivers |
| [`template-file-missing`](receivers.md#template-file-missing) | error/warn | Receivers |
| [`unused-receiver`](receivers.md#unused-receiver) | info | Receivers |
| [`inhibit-no-equal`](inhibition.md#inhibit-no-equal) | warn | Inhibition |
| [`circular-inhibition`](inhibition.md#circular-inhibition) | warn | Inhibition |
| [`inhibit-same-match`](inhibition.md#inhibit-same-match) | info | Inhibition |
| [`repeat-before-group`](timing.md#repeat-before-group) | warn | Timing |
| [`wait-exceeds-interval`](timing.md#wait-exceeds-interval) | warn | Timing |
| [`undefined-time-interval`](timing.md#undefined-time-interval) | error | Timing |
| [`global-resolve-timeout-missing`](timing.md#global-resolve-timeout-missing) | info | Timing |

## Severity levels

| level | meaning |
|-------|---------|
| **error** | Config is broken — alerts will be lost or Alertmanager will reject the config |
| **warn** | Almost certainly not what you intended |
| **info** | Suspicious, worth reviewing — may be intentional |
