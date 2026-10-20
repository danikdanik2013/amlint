# Timing checks

These checks catch notification timing that doesn't do what it says, and routes that point
at time intervals that don't exist.

Alertmanager batches alerts into **groups** (alerts on the same route that share its
`group_by` labels) and times each group's notifications with three settings:

- **`group_wait`**: how long to wait after a group's first alert before the first
  notification. Default `30s`.
- **`group_interval`**: how often to notify about changes, such as new or resolved alerts,
  to a group that was already notified. Default `5m`.
- **`repeat_interval`**: how long to wait before sending a notification again for alerts
  that are still firing and unchanged. Default `4h`.

A child route inherits any of these it doesn't set from its parent, and the root route
falls back to the defaults. amlint works out the values the same way, and reports a finding
on the route that sets one of the two values involved.

---

## repeat-before-group

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** `repeat_interval` is shorter than `group_interval`.

```yaml title="Bad"
route:
  receiver: default
  group_interval: 1h
  repeat_interval: 15m   # shorter than group_interval
```

**What Alertmanager does:** Loads the config. After the first notification, Alertmanager
sends a group's notifications only once per `group_interval`, and re-sends still-firing
alerts at the first of those sends after `repeat_interval` has passed. So the reminders
come every hour, not every 15 minutes.

**How to fix:** Make `repeat_interval` at least as long as `group_interval`, ideally a
multiple of it. To get reminders more often, shorten `group_interval` too.

```yaml title="Fixed"
route:
  receiver: default
  group_interval: 5m
  repeat_interval: 15m   # a multiple of group_interval
```

---

## wait-exceeds-interval

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** `group_wait` is longer than `group_interval`. The two values are
usually swapped: `group_wait` is meant to be a short pause that collects the first burst of
alerts.

```yaml title="Bad"
route:
  receiver: default
  group_wait: 10m       # wait 10 minutes before the first notification
  group_interval: 2m    # but report changes every 2 minutes
```

**What Alertmanager does:** Loads the config. A new group waits the full 10 minutes before
its first notification; after that, changes go out every 2 minutes. The first notification
about a new problem is the slowest one.

**How to fix:** Keep `group_wait` shorter than `group_interval`.

```yaml title="Fixed"
route:
  receiver: default
  group_wait: 30s
  group_interval: 5m
```

---

## undefined-time-interval

**Level:** error · **amtool:** also catches it

**What goes wrong:** A route's `mute_time_intervals` (mute the route during these times) or
`active_time_intervals` (send only during these times) names an interval that isn't
defined under `time_intervals:`.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [team="infra"]
      receiver: infra
      mute_time_intervals: [maintenance]   # not defined anywhere
```

**What Alertmanager does:** Refuses to load the config:
`undefined time interval "maintenance" used in route`.

**How to fix:** Define the interval under `time_intervals:`, or fix the name. Intervals
defined under the older top-level `mute_time_intervals:` key count too.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers: [team="infra"]
      receiver: infra
      mute_time_intervals: [maintenance]
time_intervals:
  - name: maintenance
    time_intervals:
      - weekdays: [saturday, sunday]
```

---

## global-resolve-timeout-missing

**Level:** info · **amtool:** doesn't catch it

**What goes wrong:** `global.resolve_timeout` isn't set, so Alertmanager uses its default
of `5m`. Nothing breaks; the value just isn't visible in your config.

```yaml title="Bad"
route:
  receiver: default
# no global.resolve_timeout
```

**What Alertmanager does:** Uses `resolve_timeout` only for alerts that arrive without an
end time (`endsAt`): if such an alert isn't sent again within that time, Alertmanager marks
it resolved. Prometheus always sets an end time, so this setting doesn't change alerts that
come from Prometheus.

**How to fix:** Set the value you want. If all your alerts come from Prometheus, you can
ignore this check instead (see [Ignoring checks](index.md#ignoring-checks)).

```yaml title="Fixed"
global:
  resolve_timeout: 5m
route:
  receiver: default
```

---

## deprecated-mute-time-intervals

**Level:** info · **amtool:** doesn't catch it

**What goes wrong:** The list of time intervals at the top level of the config is called
`mute_time_intervals:`. Alertmanager 0.24 renamed it to `time_intervals:`, because a route
can now use an interval to mute (`mute_time_intervals:`) or to send only during it
(`active_time_intervals:`).

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: db
      mute_time_intervals: [nights]
mute_time_intervals:          # the old name of the list
  - name: nights
    time_intervals:
      - times: [{start_time: "00:00", end_time: "06:00"}]
```

**What Alertmanager does:** Still reads the old name. Nothing breaks today.

**How to fix:** Rename the list to `time_intervals:`, or run
[`amlint fix`](../usage.md#fix). A route's own `mute_time_intervals:` key is not deprecated:
keep it.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: db
      mute_time_intervals: [nights]   # this key on a route is fine
time_intervals:
  - name: nights
    time_intervals:
      - times: [{start_time: "00:00", end_time: "06:00"}]
```

---

## bad-time-interval

**Level:** error · **amtool:** also catches it

**What goes wrong:** A time interval has something Alertmanager can't read:

- a time that isn't `HH:MM` between `00:00` and `24:00`, or that doesn't start before it ends;
- a weekday that isn't a name, or a range such as `friday:monday` (ranges can't wrap around);
- a day of the month that is `0` or beyond `±31`, or a range that is always backwards;
- a month or year range that ends before it starts;
- a `location` that isn't in the time zone database, such as `Europe/Kiyv`;
- a missing name, or a name used twice across `time_intervals:` and `mute_time_intervals:`.

```yaml title="Bad"
time_intervals:
  - name: office-hours
    time_intervals:
      - weekdays: ['friday:monday']           # ranges can't wrap around
        times: [{start_time: '17:00', end_time: '09:00'}]
        location: Europe/Kiyv                   # not a time zone
```

**What Alertmanager does:** Refuses to load the config, for example with
`start day cannot be before end day` or `unknown time zone Europe/Kiyv`.

**How to fix:** Write each range from start to end. For a range across midnight or the
weekend, use two entries. amlint suggests the closest time zone name.

```yaml title="Fixed"
time_intervals:
  - name: office-hours
    time_intervals:
      - weekdays: ['monday:friday']
        times: [{start_time: '09:00', end_time: '17:00'}]
        location: Europe/Kyiv
```

amlint checks the location against the time zone database of the machine it runs on.
Alertmanager uses the one on its own host, which may be older.

---

## time-interval-never-active

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** A time interval that a route uses never matches any time from today on.
Common causes: every year it lists is over, a date that doesn't exist (`days_of_month:
['30:31']` with `months: [february]`), an empty list such as `weekdays: []`, or an interval
with no `time_intervals:` entries.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: db-pager
      active_time_intervals: [on-call-2023]
time_intervals:
  - name: on-call-2023
    time_intervals:
      - years: ['2023']
```

**What Alertmanager does:** Loads the config. In `mute_time_intervals`, the interval never
mutes the route. In `active_time_intervals`, the route never sends a notification.

**How to fix:** Update the years or dates, or remove the interval from the route.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: db-pager
      active_time_intervals: [on-call]
time_intervals:
  - name: on-call
    time_intervals:
      - weekdays: ['monday:friday']
```

Only intervals a route uses are checked, from the day amlint runs.

---

## route-always-muted

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** A route's time intervals leave no time when it can send:

- a mute interval with no fields, which matches every minute;
- mute intervals that together cover the whole week;
- mute intervals that cover every time its active intervals allow.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: db-pager
      active_time_intervals: [office-hours]   # 09:00-17:00
      mute_time_intervals: [daytime]          # 08:00-20:00: covers all of it
time_intervals:
  - name: office-hours
    time_intervals: [{times: [{start_time: '09:00', end_time: '17:00'}]}]
  - name: daytime
    time_intervals: [{times: [{start_time: '08:00', end_time: '20:00'}]}]
```

**What Alertmanager does:** Routes alerts to this route as usual, and without
`continue: true` they stop there. It never sends them, so they reach nobody.

**How to fix:** Remove the interval that covers everything. To drop alerts on purpose, send
them to a receiver with no integrations, which says so plainly.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: db-pager
      active_time_intervals: [office-hours]
time_intervals:
  - name: office-hours
    time_intervals: [{times: [{start_time: '09:00', end_time: '17:00'}]}]
```

Child routes don't inherit a parent's time intervals, so each route is checked with its own.
Intervals in different time zones are compared only when one of them covers every minute.
When every active interval of a route is never active,
[`time-interval-never-active`](#time-interval-never-active) reports it instead.
