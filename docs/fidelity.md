# Verified against Alertmanager

amlint simulates Alertmanager in Python: routing (`amlint route`, `amlint test`, the routing
part of `amlint diff`), inhibition, and the config errors Alertmanager refuses to load. It
runs no Go code. The Go tools in this space (amtool, and checkers built on Alertmanager's
packages) behave like Alertmanager because they *are* Alertmanager. amlint has to show
that it does, so a CI job compares it against the real binaries on generated configs.

[![Alertmanager differential](https://github.com/danikdanik2013/amlint/actions/workflows/differential.yml/badge.svg)](https://github.com/danikdanik2013/amlint/actions/workflows/differential.yml)

The job runs against **Alertmanager 0.32.2, 0.33.1 and 0.34.1**, on every pull request that
touches the routing engine, on every push to `main` and weekly with a fresh seed. It
downloads the official release tarballs and checks their sha256 against values pinned in
the workflow.

## What is compared

| | amlint | Alertmanager |
|---|---|---|
| **Routing** | the receivers `Router.match` returns, in order | `amtool config routes test --config.file=… label=value …` |
| **Config errors** | an error-level finding of a code `amlint list` marks ✓ (also caught by amtool) | `amtool check-config` rejects the config |
| **Inhibition** | whether `Inhibitor.inhibited_by` mutes each alert | `status.inhibitedBy` from `GET /api/v2/alerts` on a running `alertmanager`, after `POST /api/v2/alerts` |

The inhibition test also checks each alert's receivers from the API, which makes it a
second, independent routing check against the server itself.

The configs and label sets are generated with [Hypothesis](https://hypothesis.readthedocs.io)
from a fixed seed, so every run of a given seed tests the same cases:

- route trees up to four levels deep, with `continue: true` / `false` fan-out and inherited
  receivers
- `=`, `!=`, `=~` and `!~` matchers in every spelling Alertmanager accepts: `matchers:`
  entries one per matcher or several to an entry, with or without `{}`, quoted, loosely
  escaped (`"a\.b"`) or bare values, spaces around the operator, quoted label names
  (`"service.name"="api"`), and `match:` / `match_re:` maps
- regexes from a pool of real-world patterns and built from parts: alternations, groups,
  classes, quantifiers, `^ $ \A \z \b \B`, `(?i)`, `(?s)`, `(?m)`
- label values that stress quoting and matching: quotes, backslashes, commas, braces,
  newlines, non-ASCII letters (`é`, the Kelvin sign), and plain YAML scalars that YAML 1.1
  types (`NO`, `1.10`, `0x1F`)
- label sets with labels missing, empty, or set to values the matchers look for
- configs with up to two of the mistakes `amtool check-config` rejects, including integration
  fields covered (or not) by a `_file` variant or a `global:` default
- inhibit rules with `equal:`, rules matching both sides, and alerts that match several rules

When amlint and Alertmanager disagree, the test reduces the case to a minimal config and
label set that still disagree, and prints both results with the `amtool` command that
reproduces it. [CONTRIBUTING](contributing.md#differential-tests-against-alertmanager)
explains how to run the tests locally.

## Differences it found, now fixed

Each fix has a regression test in `test_linter.py`.

- **YAML scalars.** Alertmanager reads `match: {country: NO}` as the string `NO`, where
  YAML 1.1 (and amlint, until now) says `false`. `1.10`, `0x1F`, `yes` and the like keep
  their text too, in a plain config and in one held in a Secret or a Mimir config. Helm
  values are different: Helm reads `values.yaml` as YAML 1.1 before it renders the config,
  so there `NO` really is `false`, and amlint reads it that way.
- **Bare regexes with braces.** Alertmanager's classic matcher parser strips one trailing
  `}` from a `matchers:` entry even when it doesn't start with `{`, so a bare
  `code=~[0-9]{3}` is the regex `[0-9]{3`, which matches `1{3` and not `123`. Quote the
  value (`code=~"[0-9]{3}"`) to get what you meant.
- **Which matcher parser reads an entry.** The classic parser reads an entry whenever it
  can, even where the UTF-8 one would read it differently. Only when it fails, for example
  on a quoted label name, does the UTF-8 parser read the whole entry, with Go string
  escapes: there `\t` is a tab and `\.` is an error. Bare values are unescaped like quoted
  ones (`a=x\\y` is `x\y`).
- **Regex details.** `\b` and `\B` look at ASCII word characters only, ignore `(?i)`, and
  `\B` matches an empty value. `$` outside `(?m)` doesn't match before a final newline. A
  quantifier after an anchor (`^*`, `\b+`) is valid Go. `[\b]` is not.
- **Config errors.** `group_by: ['...', alertname]` is an error, since Alertmanager refuses
  it. An empty `api_url: ""` in `slack_configs` is an error, even with
  `global.slack_api_url` set. `match:` keys that aren't classic label names (`k8s.ns`) are
  rejected. Use a quoted name in `matchers:` for those.

## Known differences

These are deliberate, or gaps that depend on how Alertmanager was built, or Alertmanager
bugs. The tests recognize each one narrowly and count it in their statistics, so none of
them is skipped silently.

- **Inhibition in Alertmanager before 0.34.1.** Older releases keep one source alert per
  inhibit rule and `equal:` value. When that alert matches both sides of the rule, they
  miss the inhibition, even if another firing alert would inhibit. So the result depends
  on the order the alerts arrived. 0.34.1 fixed this ("inhibit: Fix several issues related
  to inhibitions that caused alerts to be improperly un-muted", #5449, #5542, #5559).
  amlint follows 0.34.1. On 0.32 and 0.33 the test accepts that Alertmanager misses such an
  inhibition, and only when another firing alert matches both sides of the rule.
- **Regexes amlint declines.** Python can't evaluate Unicode classes (`\pL`, `\p{Greek}`)
  or negated POSIX classes (`[[:^alpha:]]`) the way Go does. For these, `amlint route` and
  `amlint test` exit 2 and point to `amtool config routes test`. They never give a guess.
  `amlint check` still accepts them as valid regexes.
- **Unicode class names.** `amlint check` doesn't report a `\p{...}` class Go doesn't
  know, such as `\p{Foo}`, while Alertmanager rejects it ("invalid character class
  range"). Which names are valid depends on the Go version Alertmanager was built with:
  Go 1.25 added aliases such as `\p{Letter}` and made the lookup case-insensitive, so
  `\p{greek}` works in Alertmanager 0.32 to 0.34 (built with Go 1.26), but not in builds
  with older Go. amlint doesn't simulate `\p` regexes either, see the item above.
- **Simulating configs Alertmanager won't load.** `amlint route` and `amlint test` simulate
  a config even with errors that `amlint check` reports, such as an undefined receiver or
  a root route without a receiver. `amlint diff` relies on this, since it compares a new
  file against an empty route. The config-error test covers these through `amlint check`.

## Not covered yet

- Label names in alerts are limited to a small pool. `group_by`, timings and time intervals
  are not compared, because amtool doesn't print them. The inhibition test reads only
  receivers and inhibitions from the API.
- Only `matchers:` entries, `match:` maps, receivers and inhibit rules are generated.
  Integration settings beyond the fields amlint checks, templates, and YAML features such
  as anchors and merge keys are not.
