# Verified against Alertmanager

amlint simulates Alertmanager in Python instead of running it. This page shows how CI checks
that the simulation matches the real Alertmanager, and lists the few places where it doesn't.

[![Alertmanager differential](https://github.com/danikdanik2013/amlint/actions/workflows/differential.yml/badge.svg)](https://github.com/danikdanik2013/amlint/actions/workflows/differential.yml)

!!! success "The promise"
    On generated configs and alerts, amlint gives **the same receivers, the same inhibitions
    and the same rejected configs** as **Alertmanager 0.32.2, 0.33.1 and 0.34.1**.

    Each pull request that changes the simulation is checked on 300 routing configs (with
    several alerts each), 300 configs for config errors and 40 inhibition scenarios, against
    each of the three versions. A weekly run tries 1,500 + 1,500 + 150 new ones.

What this means for you:

- `amlint route`, `amlint test` and the routing part of `amlint diff` pick the receivers
  your Alertmanager would pick, in the same order.
- `amlint route --firing` and `firing:` in tests mute an alert exactly when Alertmanager
  would.
- When `amlint check` reports an error that it marks as also caught by amtool (`amlint list`
  shows a ✓), Alertmanager refuses to load the config, and the other way round.
- Where amlint can't reproduce Alertmanager exactly, such as Unicode classes in regexes
  (`\pL`), `route` and `test` exit 2 and tell you to use `amtool config routes test`. They
  never guess.
- The exceptions are listed under [Known differences](#known-differences).

## What is compared

| | amlint | Alertmanager |
|---|---|---|
| **Routing** | the receivers amlint's router returns, in order | `amtool config routes test --config.file=… label=value …` |
| **Config errors** | an error-level finding whose code `amlint list` marks ✓ (also caught by amtool) | `amtool check-config` rejects the config |
| **Inhibition** | whether amlint's inhibitor mutes each alert | `status.inhibitedBy` from `GET /api/v2/alerts` on a running `alertmanager`, after `POST /api/v2/alerts` |

The inhibition test also compares each alert's receivers from the API, which is a second,
independent routing check against the server itself.

How the job runs (`.github/workflows/differential.yml`):

- **Versions:** the last two minor releases and the newest, 0.32.2, 0.33.1 and 0.34.1. It
  downloads the official release tarballs and checks their sha256 against values pinned in
  the workflow. A weekly job warns when a newer Alertmanager release is out.
- **When:** on pull requests and pushes to `main` that change the simulation, the checks,
  how configs are read, or the tests, and weekly.
- **Cases:** [Hypothesis](https://hypothesis.readthedocs.io), a property-based testing
  library, generates the configs and alerts from a seed. Pull requests and pushes use seed
  0, so every run tests the same cases. The weekly run uses a fresh seed to find new ones.

## What the generated configs cover

- route trees up to four levels deep, with `continue: true` / `false` fan-out and inherited
  receivers
- `=`, `!=`, `=~` and `!~` matchers in every spelling Alertmanager accepts: `matchers:`
  entries with one or several matchers, with or without `{}`, quoted, loosely escaped
  (`"a\.b"`) or bare values, spaces around the operator, quoted label names
  (`"service.name"="api"`), and `match:` / `match_re:` maps
- regexes from a pool of real-world patterns and built from parts: alternations, groups,
  classes, quantifiers, `^ $ \A \z \b \B`, `(?i)`, `(?s)`, `(?m)`
- label values that stress quoting and matching: quotes, backslashes, commas, braces,
  newlines, non-ASCII letters (`é`, the Kelvin sign), and plain YAML scalars that YAML 1.1
  would type (`NO`, `1.10`, `0x1F`)
- label sets with labels missing, empty, or set to values the matchers look for
- configs with up to two of the mistakes `amtool check-config` rejects, including
  integration fields covered (or not) by a `_file` variant or a `global:` default,
  `matchers:` entries that neither matcher parser reads, `match:` / `match_re:` keys
  that aren't classic label names (`k8s.ns`, `1a`, a null key), and time intervals with
  times, weekdays, days, months, years and locations Alertmanager reads or rejects
- inhibit rules with `equal:`, rules matching both sides, and alerts that match several
  rules

When amlint and Alertmanager disagree, the test reduces the case to a minimal config and
label set that still disagree. It prints both results and the `amtool` command that
reproduces it. To run the tests yourself, see
[Contributing](contributing.md#differential-tests-against-alertmanager).

## Known differences

Each of these is deliberate, depends on how Alertmanager was built, or is an Alertmanager
bug. The tests recognize each one narrowly and count it in their statistics, so none is
skipped silently.

- **Inhibition before Alertmanager 0.34.1.** Older releases keep one source alert per
  inhibit rule and `equal:` value. If that alert matches both sides of the rule, they miss
  the inhibition, even when another firing alert would inhibit. So the result depends on
  the order the alerts arrived. 0.34.1 fixed this (#5449, #5542, #5559), and amlint follows
  0.34.1. If you run 0.32 or 0.33, Alertmanager may not mute an alert that amlint says is
  muted, but only when another firing alert matches both sides of the rule.
- **Regexes amlint declines.** Python can't evaluate Unicode classes (`\pL`, `\p{Greek}`) or
  negated POSIX classes (`[[:^alpha:]]`) the way Go does. For these, `amlint route` and
  `amlint test` exit 2 and point you to `amtool config routes test`. `amlint check` still
  accepts them as valid regexes.
- **Unknown Unicode class names.** `amlint check` doesn't report a `\p{...}` class Go doesn't
  know, such as `\p{Foo}`, but Alertmanager rejects it ("invalid character class range").
  Which names are valid depends on the Go version Alertmanager was built with: Go 1.25 added
  aliases such as `\p{Letter}` and made names case-insensitive. So `\p{greek}` works in
  Alertmanager 0.32 to 0.34 (built with Go 1.26), but not in builds with older Go.
- **Configs Alertmanager won't load.** `amlint route` and `amlint test` still simulate a
  config with errors that `amlint check` reports, such as an undefined receiver or a root
  route without a receiver. `amlint diff` relies on this to compare a new file against an
  empty config. The config-error test covers these errors through `amlint check`.

## Alertmanager behavior amlint matches { #differences-it-found-now-fixed }

The differential tests found these surprising Alertmanager behaviors. amlint now behaves the
same way, and `amlint check` reports the cases that are probably not what you meant.

- **YAML scalars.** Alertmanager reads `match: {country: NO}` as the string `NO`, although
  YAML 1.1 says `false`. `1.10`, `0x1F`, `yes` and the like keep their text too, in a plain
  config and in one held in a Secret or a Mimir config. Helm values are different: Helm
  reads `values.yaml` as YAML 1.1 before it renders the config, so there `NO` really is
  `false`, and amlint reads it that way.
- **Bare regexes with braces.** Alertmanager strips one trailing `}` from a `matchers:` entry
  even when the entry doesn't start with `{`. So a bare `code=~[0-9]{3}` is the regex
  `[0-9]{3`, which matches `1{3` and not `123`. Quote the value (`code=~"[0-9]{3}"`) to get
  what you meant. `amlint check` warns about it (`stripped-brace`).
- **Which matcher parser reads an entry.** Alertmanager has two matcher parsers: the classic
  one and a newer UTF-8 one. The classic parser reads an entry whenever it can, even where
  the UTF-8 one would read it differently. Only when it fails, for example on a quoted label
  name, does the UTF-8 parser read the whole entry, with Go string escapes: there `\t` is a
  tab and `\.` is an error. Bare values are unescaped like quoted ones (`a=x\\y` is `x\y`).
  `amlint check` reports an entry neither parser reads as `bad-matcher`.
- **Regex details.** `\b` and `\B` look at ASCII word characters only and ignore `(?i)`, and
  `\B` matches an empty value. `$` outside `(?m)` doesn't match before a final newline. A
  quantifier after an anchor (`^*`, `\b+`) is valid in Go; `[\b]` is not.
- **Config errors.** `group_by: ['...', alertname]` is an error, since Alertmanager refuses
  it. An empty `api_url: ""` in `slack_configs` is an error, even with
  `global.slack_api_url` set. `match:` keys that aren't classic label names (`k8s.ns`) are
  rejected (`bad-match-label`); use a quoted name in `matchers:` for those.

## Not covered yet

- Alerts use label names from a small pool.
- `group_by`, timings and when time intervals are active aren't compared, because amtool
  doesn't print them. Whether amtool accepts a time interval is.
  The inhibition test reads only receivers and inhibitions from the API.
- Only `matchers:` entries, `match:` maps, receivers and inhibit rules are generated.
  Integration settings beyond the fields amlint checks, templates, and YAML features such as
  anchors and merge keys are not. The template parser is tested against Go's own parser
  tests instead.
