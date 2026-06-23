"""Tests for amlint. Run: python3 -m pytest test_linter.py -v"""

import json
import os
import yaml
import pytest
from amlint.linter import lint, ERROR, WARN, INFO
from amlint.cli import main


def codes(cfg):
    return {f.code for f in lint(cfg)}


def test_undefined_receiver():
    cfg = {
        "route": {"receiver": "ghost"},
        "receivers": [{"name": "real"}],
    }
    assert "undefined-receiver" in codes(cfg)


def test_clean_config_silent():
    cfg = {
        "route": {"receiver": "team"},
        "receivers": [{"name": "team"}],
    }
    assert "undefined-receiver" not in codes(cfg)


def test_unused_receiver():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "unused-receiver" in codes(cfg)


def test_dead_inhibition_no_equal():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"source_match": {"severity": "critical"},
                           "target_match": {"severity": "warning"}}],
    }
    assert "inhibit-no-equal" in codes(cfg)


def test_inhibition_with_equal_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"source_match": {"severity": "critical"},
                           "target_match": {"severity": "warning"},
                           "equal": ["alertname"]}],
    }
    assert "inhibit-no-equal" not in codes(cfg)


def test_bad_regex():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"match_re": {"svc": "auth(["}, "receiver": "a"}]},
        "receivers": [{"name": "a"}],
    }
    assert "bad-regex" in codes(cfg)


def test_unreachable_catch_all():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"receiver": "a"},                        # catch-all, continue:false
            {"match": {"severity": "critical"}, "receiver": "a"},
        ]},
        "receivers": [{"name": "a"}],
    }
    assert "unreachable-route" in codes(cfg)


def test_no_root_route():
    cfg = {"receivers": [{"name": "a"}]}
    assert "no-root-route" in codes(cfg)


def test_groupby_ellipsis():
    cfg = {
        "route": {"receiver": "a", "group_by": ["...", "alertname"]},
        "receivers": [{"name": "a"}],
    }
    assert "groupby-ellipsis" in codes(cfg)


def test_groupby_ellipsis_alone_ok():
    cfg = {
        "route": {"receiver": "a", "group_by": ["..."]},
        "receivers": [{"name": "a"}],
    }
    assert "groupby-ellipsis" not in codes(cfg)


def test_inhibit_same_match():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"source_match": {"severity": "critical"},
                           "target_match": {"severity": "critical"},
                           "equal": ["alertname"]}],
    }
    assert "inhibit-same-match" in codes(cfg)


def test_bad_regex_via_matchers():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"matchers": ["service=~auth(["], "receiver": "a"}]},
        "receivers": [{"name": "a"}],
    }
    assert "bad-regex" in codes(cfg)


def test_matchers_valid_regex_ok():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"matchers": ["severity=~^(critical|warning)$"], "receiver": "a"}]},
        "receivers": [{"name": "a"}],
    }
    assert "bad-regex" not in codes(cfg)


def test_duplicate_receivers():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}, {"name": "a"}],
    }
    assert "duplicate-receiver" in codes(cfg)


def test_empty_receiver_warn():
    cfg = {
        "route": {"receiver": "default"},
        "receivers": [
            {"name": "default", "slack_configs": [{}]},
            {"name": "orphan"},
        ],
    }
    assert "empty-receiver" in codes(cfg)


def test_empty_receiver_default_is_info():
    cfg = {
        "route": {"receiver": "null"},
        "receivers": [{"name": "null"}],
    }
    findings = [f for f in lint(cfg) if f.code == "empty-receiver"]
    assert findings and findings[0].level == "info"


def test_undefined_time_interval():
    cfg = {
        "route": {"receiver": "a", "mute_time_intervals": ["maintenance"]},
        "receivers": [{"name": "a"}],
    }
    assert "undefined-time-interval" in codes(cfg)


def test_defined_time_interval_ok():
    cfg = {
        "route": {"receiver": "a", "mute_time_intervals": ["maintenance"]},
        "receivers": [{"name": "a"}],
        "time_intervals": [{"name": "maintenance"}],
    }
    assert "undefined-time-interval" not in codes(cfg)


def test_repeat_before_group():
    cfg = {
        "route": {"receiver": "a", "group_interval": "1h", "repeat_interval": "5m"},
        "receivers": [{"name": "a"}],
    }
    assert "repeat-before-group" in codes(cfg)


def test_timing_ok():
    cfg = {
        "route": {"receiver": "a", "group_interval": "5m", "repeat_interval": "4h"},
        "receivers": [{"name": "a"}],
    }
    assert "repeat-before-group" not in codes(cfg)


def test_circular_inhibition():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [
            {"source_match": {"severity": "critical"}, "target_match": {"severity": "warning"}, "equal": ["alertname"]},
            {"source_match": {"severity": "warning"}, "target_match": {"severity": "critical"}, "equal": ["alertname"]},
        ],
    }
    assert "circular-inhibition" in codes(cfg)


def test_useless_continue():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"match": {"env": "prod"}, "receiver": "a", "continue": True},
        ]},
        "receivers": [{"name": "a"}],
    }
    assert "useless-continue" in codes(cfg)


def test_wait_exceeds_interval():
    cfg = {
        "route": {"receiver": "a", "group_wait": "10m", "group_interval": "2m"},
        "receivers": [{"name": "a"}],
    }
    assert "wait-exceeds-interval" in codes(cfg)


# ── CLI tests ────────────────────────────────────────────────────────

def test_cli_check_clean(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n")
    assert main(["check", str(cfg)]) == 0


def test_cli_check_errors(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: ghost\nreceivers:\n  - name: real\n")
    assert main(["check", str(cfg)]) == 1


def test_cli_check_strict(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n"
                   "inhibit_rules:\n  - source_match: {severity: critical}\n"
                   "    target_match: {severity: warning}\n")
    assert main(["check", "--strict", str(cfg)]) == 1


def test_cli_check_json(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: ghost\nreceivers:\n  - name: real\n")
    main(["check", "--format", "json", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "undefined-receiver" for f in out)


def test_cli_check_stdin(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(
        "route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n"
    ))
    assert main(["check", "-"]) == 0


def test_cli_diff_no_change(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n")
    assert main(["diff", str(cfg), str(cfg)]) == 0


def test_cli_diff_regression(tmp_path):
    good = tmp_path / "good.yml"
    bad  = tmp_path / "bad.yml"
    good.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n")
    bad.write_text("route:\n  receiver: ghost\nreceivers:\n  - name: real\n")
    assert main(["diff", str(good), str(bad)]) == 1


def test_cli_file_not_found():
    with pytest.raises(SystemExit) as exc:
        main(["check", "/nonexistent/path.yml"])
    assert exc.value.code == 2


# ── Integration field checks ─────────────────────────────────────────

def test_webhook_no_url():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "webhook_configs": [{}]}],
    }
    assert "webhook-no-url" in codes(cfg)


def test_webhook_with_url_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "webhook_configs": [{"url": "http://example.com"}]}],
    }
    assert "webhook-no-url" not in codes(cfg)


def test_webhook_url_file_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "webhook_configs": [{"url_file": "/run/secrets/url"}]}],
    }
    assert "webhook-no-url" not in codes(cfg)


def test_pagerduty_no_routing_key():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "pagerduty_configs": [{"severity": "critical"}]}],
    }
    assert "pagerduty-no-routing-key" in codes(cfg)


def test_pagerduty_with_routing_key_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "pagerduty_configs": [{"routing_key": "abc123"}]}],
    }
    assert "pagerduty-no-routing-key" not in codes(cfg)


def test_pagerduty_service_key_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "pagerduty_configs": [{"service_key": "abc123"}]}],
    }
    assert "pagerduty-no-routing-key" not in codes(cfg)


def test_slack_no_api_url():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "slack_configs": [{"channel": "#alerts"}]}],
    }
    assert "slack-no-api-url" in codes(cfg)


def test_slack_with_api_url_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "slack_configs": [{"api_url": "https://hooks.slack.com/xxx"}]}],
    }
    assert "slack-no-api-url" not in codes(cfg)


def test_opsgenie_no_api_key():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "opsgenie_configs": [{"priority": "P1"}]}],
    }
    assert "opsgenie-no-api-key" in codes(cfg)


def test_opsgenie_with_api_key_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "opsgenie_configs": [{"api_key": "secret"}]}],
    }
    assert "opsgenie-no-api-key" not in codes(cfg)


def test_opsgenie_global_api_key_ok():
    cfg = {
        "global": {"opsgenie_api_key": "secret"},
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "opsgenie_configs": [{"priority": "P1"}]}],
    }
    assert "opsgenie-no-api-key" not in codes(cfg)


def test_msteams_no_webhook_url():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "msteams_configs": [{"title": "Alert"}]}],
    }
    assert "msteams-no-webhook-url" in codes(cfg)


def test_global_resolve_timeout_missing():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "webhook_configs": [{"url": "http://fake"}]}],
    }
    assert "global-resolve-timeout-missing" in codes(cfg)


def test_global_resolve_timeout_set_ok():
    cfg = {
        "global": {"resolve_timeout": "5m"},
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "webhook_configs": [{"url": "http://fake"}]}],
    }
    assert "global-resolve-timeout-missing" not in codes(cfg)


def test_cli_check_sarif(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: ghost\nreceivers:\n  - name: real\n")
    main(["check", "--format", "sarif", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == "2.1.0"
    results = out["runs"][0]["results"]
    assert any(r["ruleId"] == "undefined-receiver" for r in results)
    assert all(r["level"] in ("error", "warning", "note") for r in results)


def test_sarif_rules_cover_all_checks(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n"
                   "    webhook_configs: [{url: 'http://fake'}]\n")
    main(["check", "--format", "sarif", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    rule_ids = {r["id"] for r in out["runs"][0]["tool"]["driver"]["rules"]}
    assert "undefined-receiver" in rule_ids
    assert "global-resolve-timeout-missing" in rule_ids


def test_route_match_collision():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"match": {"team": "infra", "severity": "critical"}, "receiver": "a"},
            {"match": {"team": "infra", "severity": "critical"}, "receiver": "b"},
        ]},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "route-match-collision" in codes(cfg)


def test_route_match_collision_matchers_form():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"matchers": ["team=infra", "severity=critical"], "receiver": "a"},
            {"matchers": ["severity=critical", "team=infra"], "receiver": "b"},
        ]},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "route-match-collision" in codes(cfg)


def test_route_match_no_collision_different_matchers():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"match": {"team": "infra"}, "receiver": "a"},
            {"match": {"team": "platform"}, "receiver": "b"},
        ]},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "route-match-collision" not in codes(cfg)


def test_route_match_no_collision_catch_all():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"receiver": "a"},  # catch-all — no matchers, not a collision candidate
            {"receiver": "b"},
        ]},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "route-match-collision" not in codes(cfg)


def test_cli_list():
    assert main(["list"]) == 0


def test_msteams_with_webhook_url_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "msteams_configs": [{"webhook_url": "https://outlook.office.com/..."}]}],
    }
    assert "msteams-no-webhook-url" not in codes(cfg)


def test_slack_global_api_url_ok():
    cfg = {
        "global": {"slack_api_url": "https://hooks.slack.com/global"},
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "slack_configs": [{"channel": "#alerts"}]}],
    }
    assert "slack-no-api-url" not in codes(cfg)


# ── Severity overrides ────────────────────────────────────────────────

def test_explain_known_code(capsys):
    assert main(["explain", "undefined-receiver"]) == 0


def test_explain_unknown_code():
    assert main(["explain", "not-a-real-code"]) == 2


def test_lint_severity_override():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    findings = lint(cfg, severity={"unused-receiver": "error"})
    f = next(f for f in findings if f.code == "unused-receiver")
    assert f.level == "error"


def test_lint_severity_override_downgrade():
    cfg = {
        "route": {"receiver": "ghost"},
        "receivers": [{"name": "real"}],
    }
    findings = lint(cfg, severity={"undefined-receiver": "warn"})
    f = next(f for f in findings if f.code == "undefined-receiver")
    assert f.level == "warn"


def test_lint_severity_invalid_value_ignored():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    findings = lint(cfg, severity={"unused-receiver": "critical"})  # invalid level
    f = next(f for f in findings if f.code == "unused-receiver")
    assert f.level == "info"  # unchanged


def test_pyproject_toml_config(tmp_path, monkeypatch):
    cfg = tmp_path / "am.yml"
    cfg.write_text(
        "route:\n  receiver: a\nreceivers:\n  - name: a\n"
        "  - name: b\n"
    )
    toml = tmp_path / "pyproject.toml"
    toml.write_text("[tool.amlint]\nignore = ['unused-receiver']\n")
    monkeypatch.chdir(tmp_path)
    # unused-receiver is ignored via pyproject.toml — no findings → exit 0
    assert main(["check", str(cfg)]) == 0


def test_amlint_yml_takes_priority_over_pyproject(tmp_path, monkeypatch):
    cfg = tmp_path / "am.yml"
    cfg.write_text(
        "route:\n  receiver: a\nreceivers:\n  - name: a\n"
        "  - name: b\n"
    )
    # pyproject.toml ignores unused-receiver
    toml = tmp_path / "pyproject.toml"
    toml.write_text("[tool.amlint]\nignore = ['unused-receiver']\n")
    # .amlint.yml does NOT ignore it — takes priority
    rc = tmp_path / ".amlint.yml"
    rc.write_text("ignore: []\n")
    monkeypatch.chdir(tmp_path)
    # .amlint.yml wins — unused-receiver is info, exit 0 still (not error)
    assert main(["check", str(cfg)]) == 0


def test_cli_config_severity_override(tmp_path, monkeypatch):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n  - name: b\n")
    rc = tmp_path / ".amlint.yml"
    rc.write_text("severity:\n  unused-receiver: error\n")
    monkeypatch.chdir(tmp_path)
    # unused-receiver upgraded to error → exit 1
    assert main(["check", str(cfg)]) == 1


def test_lint_ignore_single():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "unused-receiver" in codes(cfg)
    filtered = {f.code for f in lint(cfg, ignore={"unused-receiver"})}
    assert "unused-receiver" not in filtered


def test_lint_ignore_multiple():
    cfg = {
        "route": {"receiver": "ghost"},
        "receivers": [{"name": "real"}],
    }
    found = {f.code for f in lint(cfg, ignore={"undefined-receiver", "empty-receiver"})}
    assert "undefined-receiver" not in found


def test_cli_ignore_flag(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: ghost\nreceivers:\n  - name: real\n")
    # Without ignore — exits 1 (undefined-receiver is an error)
    assert main(["check", str(cfg)]) == 1
    # With ignore — the error is suppressed, exits 0
    assert main(["check", "--ignore", "undefined-receiver", str(cfg)]) == 0


def test_cli_ignore_comma_separated(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n"
                   "inhibit_rules:\n  - source_match: {severity: critical}\n"
                   "    target_match: {severity: warning}\n")
    # inhibit-no-equal would trigger --strict failure; ignore it
    assert main(["check", "--strict", "--ignore", "inhibit-no-equal", str(cfg)]) == 0


def test_cli_config_file_ignore(tmp_path, monkeypatch):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n"
                   "inhibit_rules:\n  - source_match: {severity: critical}\n"
                   "    target_match: {severity: warning}\n")
    rc = tmp_path / ".amlint.yml"
    rc.write_text("ignore:\n  - inhibit-no-equal\nstrict: true\n")
    monkeypatch.chdir(tmp_path)
    # strict comes from config file, inhibit-no-equal is ignored — should pass
    assert main(["check", str(cfg)]) == 0


def test_cli_config_file_strict(tmp_path, monkeypatch):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n    webhook_configs: [{url: 'http://fake'}]\n"
                   "inhibit_rules:\n  - source_match: {severity: critical}\n"
                   "    target_match: {severity: warning}\n")
    rc = tmp_path / ".amlint.yml"
    rc.write_text("strict: true\n")
    monkeypatch.chdir(tmp_path)
    # strict from config file — inhibit-no-equal is WARN, should fail
    assert main(["check", str(cfg)]) == 1


# ── --only flag ────────────────────────────────────────────────────────

def test_lint_only_single():
    cfg = {
        "route": {"receiver": "ghost"},
        "receivers": [{"name": "real"}],
    }
    found = {f.code for f in lint(cfg, only={"undefined-receiver"})}
    assert found == {"undefined-receiver"}


def test_lint_only_excludes_others():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    found = {f.code for f in lint(cfg, only={"undefined-receiver"})}
    assert "unused-receiver" not in found
    assert "global-resolve-timeout-missing" not in found


def test_cli_only_flag(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: ghost\nreceivers:\n  - name: real\n")
    # --only undefined-receiver: only that check runs, no empty-receiver/unused etc.
    main(["check", "--only", "undefined-receiver", str(cfg)])


# ── --exit-zero flag ────────────────────────────────────────────────────

def test_cli_exit_zero_suppresses_error(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: ghost\nreceivers:\n  - name: real\n")
    # would normally exit 1 (undefined-receiver)
    assert main(["check", "--exit-zero", str(cfg)]) == 0


def test_cli_exit_zero_clean_still_zero(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n"
                   "    webhook_configs: [{url: 'http://fake'}]\n")
    assert main(["check", "--exit-zero", str(cfg)]) == 0


# ── template-file-missing ───────────────────────────────────────────────

def test_template_literal_missing(tmp_path):
    basedir = str(tmp_path)
    cfg = {"route": {"receiver": "a"}, "receivers": [{"name": "a"}],
           "templates": ["/nonexistent/path/custom.tmpl"]}
    found = {f.code for f in lint(cfg, basedir=basedir)}
    assert "template-file-missing" in found


def test_template_literal_exists_ok(tmp_path):
    tpl = tmp_path / "custom.tmpl"
    tpl.write_text("{{ define 'test' }}ok{{ end }}")
    cfg = {"route": {"receiver": "a"}, "receivers": [{"name": "a"}],
           "templates": [str(tpl)]}
    found = {f.code for f in lint(cfg, basedir=str(tmp_path))}
    assert "template-file-missing" not in found


def test_template_glob_no_match(tmp_path):
    from amlint.linter import lint as _lint, WARN
    cfg = {"route": {"receiver": "a"}, "receivers": [{"name": "a"}],
           "templates": ["*.tmpl"]}
    findings = _lint(cfg, basedir=str(tmp_path))
    f = [x for x in findings if x.code == "template-file-missing"]
    assert f and f[0].level == WARN


def test_template_glob_matches_ok(tmp_path):
    (tmp_path / "a.tmpl").write_text("x")
    cfg = {"route": {"receiver": "a"}, "receivers": [{"name": "a"}],
           "templates": ["*.tmpl"]}
    found = {f.code for f in lint(cfg, basedir=str(tmp_path))}
    assert "template-file-missing" not in found


def test_template_check_skipped_without_basedir():
    cfg = {"route": {"receiver": "a"}, "receivers": [{"name": "a"}],
           "templates": ["/nonexistent/path/custom.tmpl"]}
    found = {f.code for f in lint(cfg)}
    assert "template-file-missing" not in found


def test_broken_yml_integration():
    """Smoke test: broken.yml must produce the expected set of finding codes."""
    broken = os.path.join(os.path.dirname(__file__), "broken.yml")
    with open(broken) as f:
        cfg = yaml.safe_load(f)
    found = codes(cfg)
    assert found == {
        "undefined-receiver",
        "bad-regex",
        "inhibit-no-equal",
        "unreachable-route",
        "global-resolve-timeout-missing",
        "groupby-ellipsis",
        "unused-receiver",
        "empty-receiver",
    }
