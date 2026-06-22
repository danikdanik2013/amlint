# Timing checks

## repeat-before-group

**Level:** warn

`repeat_interval` is shorter than `group_interval`. This means Alertmanager will send repeat notifications before the grouped batch has had a chance to fire again — generating more noise than expected.

=== "Bad"
    ```yaml
    route:
      receiver: default
      group_interval: 1h
      repeat_interval: 5m     # repeats every 5m, but group fires every 1h
    ```

=== "Fixed"
    ```yaml
    route:
      receiver: default
      group_interval: 5m
      repeat_interval: 4h     # repeat_interval should be >= group_interval
    ```

**Typical values:**

| field | recommended |
|-------|-------------|
| `group_wait` | `30s` – `2m` |
| `group_interval` | `5m` – `15m` |
| `repeat_interval` | `1h` – `24h` |

---

## wait-exceeds-interval

**Level:** warn

`group_wait` is longer than `group_interval`. This means subsequent alerts in the group will be sent before the initial `group_wait` has expired.

=== "Bad"
    ```yaml
    route:
      receiver: default
      group_wait: 10m      # wait 10 minutes before first notification
      group_interval: 2m   # but send updates every 2 minutes
    ```

=== "Fixed"
    ```yaml
    route:
      receiver: default
      group_wait: 30s
      group_interval: 5m   # group_interval should be > group_wait
    ```

---

## undefined-time-interval

**Level:** error

A route references a time interval name in `mute_time_intervals` or `active_time_intervals` that is not defined in `time_intervals`. Alertmanager will reject this config at runtime.

=== "Bad"
    ```yaml
    route:
      receiver: default
      mute_time_intervals: [maintenance]   # not defined below!

    receivers:
      - name: default
        slack_configs: [...]
    ```

=== "Fixed"
    ```yaml
    route:
      receiver: default
      mute_time_intervals: [maintenance]

    receivers:
      - name: default
        slack_configs: [...]

    time_intervals:
      - name: maintenance
        time_intervals:
          - weekdays: [saturday, sunday]
            times:
              - start_time: '00:00'
                end_time: '24:00'
    ```
