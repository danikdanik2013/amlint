"""Tests for amlint. Run: python3 -m pytest test_linter.py -v"""

import json
import os
import re

import pytest
import yaml

from amlint.cli import main
from amlint.linter import ERROR, WARN, lint, route_cycle


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
            {"source_match": {"severity": "critical"}, "target_match": {"severity": "warning"},
             "equal": ["alertname"]},
            {"source_match": {"severity": "warning"}, "target_match": {"severity": "critical"},
             "equal": ["alertname"]},
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


def test_cli_list_shows_amtool_overlap(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "amtool" in out
    assert "also caught by" in out


def test_amtool_classification_matches_known_codes():
    """Sanity check: ALSO_CAUGHT_BY_AMTOOL must only reference real check codes."""
    from amlint.explains import ALSO_CAUGHT_BY_AMTOOL, EXPLAINS
    assert ALSO_CAUGHT_BY_AMTOOL <= set(EXPLAINS.keys())
    assert len(ALSO_CAUGHT_BY_AMTOOL) > 0
    assert len(ALSO_CAUGHT_BY_AMTOOL) < len(EXPLAINS)


def test_msteams_with_webhook_url_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "msteams_configs": [{"webhook_url": "https://outlook.office.com/..."}]}],
    }
    assert "msteams-no-webhook-url" not in codes(cfg)


def test_slack_global_api_url_ok():
    cfg = {
        "global": {"slack_api_url": "https://hooks.slack.com/global"},
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "slack_configs": [{"channel": "#alerts"}]}],
    }
    assert "slack-no-api-url" not in codes(cfg)


def test_telegram_no_bot_token():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "telegram_configs": [{"chat_id": -100123}]}],
    }
    assert "telegram-no-bot-token" in codes(cfg)


def test_telegram_with_bot_token_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "telegram_configs": [{"bot_token": "123:ABC", "chat_id": -100123}]}],
    }
    assert "telegram-no-bot-token" not in codes(cfg)


def test_telegram_with_bot_token_file_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "telegram_configs": [{"bot_token_file": "/etc/secrets/bot_token"}]}],
    }
    assert "telegram-no-bot-token" not in codes(cfg)


def test_discord_no_webhook_url():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "discord_configs": [{"title": "Alert"}]}],
    }
    assert "discord-no-webhook-url" in codes(cfg)


def test_discord_with_webhook_url_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "discord_configs": [{"webhook_url": "https://discord.com/api/webhooks/..."}]}],
    }
    assert "discord-no-webhook-url" not in codes(cfg)


def test_victorops_no_api_key():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "victorops_configs": [{"routing_key": "team-key"}]}],
    }
    assert "victorops-no-api-key" in codes(cfg)


def test_victorops_with_api_key_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "victorops_configs": [{"api_key": "secret", "routing_key": "team-key"}]}],
    }
    assert "victorops-no-api-key" not in codes(cfg)


def test_victorops_global_api_key_ok():
    cfg = {
        "global": {"victorops_api_key": "secret"},
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "victorops_configs": [{"routing_key": "team-key"}]}],
    }
    assert "victorops-no-api-key" not in codes(cfg)


def test_wechat_no_corp_id():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "wechat_configs": [{"agent_id": "1000002"}]}],
    }
    assert "wechat-no-corp-id" in codes(cfg)


def test_wechat_with_corp_id_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "wechat_configs": [{"corp_id": "abc123", "agent_id": "1000002"}]}],
    }
    assert "wechat-no-corp-id" not in codes(cfg)


def test_wechat_global_corp_id_ok():
    cfg = {
        "global": {"wechat_api_corp_id": "abc123"},
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "wechat_configs": [{"agent_id": "1000002"}]}],
    }
    assert "wechat-no-corp-id" not in codes(cfg)


def test_sns_no_target():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "sns_configs": [{"subject": "Alert"}]}],
    }
    assert "sns-no-target" in codes(cfg)


def test_sns_with_topic_arn_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "sns_configs": [{"topic_arn": "arn:aws:sns:us-east-2:123:My-Topic"}]}],
    }
    assert "sns-no-target" not in codes(cfg)


def test_sns_with_phone_number_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "sns_configs": [{"phone_number": "+17785522312"}]}],
    }
    assert "sns-no-target" not in codes(cfg)


def test_sns_with_target_arn_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a",
                       "sns_configs": [{"target_arn": "arn:aws:sns:us-west-2:123:endpoint/x"}]}],
    }
    assert "sns-no-target" not in codes(cfg)


# ── Severity overrides ────────────────────────────────────────────────

def test_explain_known_code(capsys):
    assert main(["explain", "undefined-receiver"]) == 0


def test_explain_shows_amtool_note_for_redundant_check(capsys):
    assert main(["explain", "undefined-receiver"]) == 0
    out = capsys.readouterr().out
    assert "Also caught by" in out
    assert "amtool check-config" in out


def test_explain_shows_gap_note_for_differentiator_check(capsys):
    assert main(["explain", "unreachable-route"]) == 0
    out = capsys.readouterr().out
    assert "Not caught by" in out
    assert "gap amlint exists for" in out


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
           "templates": ["templates/custom.tmpl"]}
    f = [x for x in lint(cfg, basedir=basedir) if x.code == "template-file-missing"]
    assert f and f[0].level == ERROR
    assert "fail to start" not in f[0].msg  # Alertmanager starts; it just loads nothing


def test_template_literal_exists_ok(tmp_path):
    tpl = tmp_path / "custom.tmpl"
    tpl.write_text("{{ define 'test' }}ok{{ end }}")
    cfg = {"route": {"receiver": "a"}, "receivers": [{"name": "a"}],
           "templates": ["custom.tmpl"]}  # resolved against the config's directory
    found = {f.code for f in lint(cfg, basedir=str(tmp_path))}
    assert "template-file-missing" not in found


def test_template_absolute_paths_not_checked(tmp_path):
    # absolute paths live on the Alertmanager host/container, not where amlint runs
    cfg = {"route": {"receiver": "a"}, "receivers": [{"name": "a"}],
           "templates": ["/etc/alertmanager/config/*.tmpl", "/nonexistent/custom.tmpl"]}
    found = {f.code for f in lint(cfg, basedir=str(tmp_path))}
    assert "template-file-missing" not in found


def test_template_glob_no_match(tmp_path):
    from amlint.linter import lint as _lint
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
           "templates": ["templates/custom.tmpl"]}
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


# ── tree command ──────────────────────────────────────────────────────

def test_tree_clean_config(tmp_path, capsys):
    cfg_path = tmp_path / "clean.yml"
    cfg_path.write_text(yaml.dump({
        "route": {"receiver": "default", "routes": [
            {"match": {"team": "infra"}, "receiver": "default"},
        ]},
        "receivers": [{"name": "default", "webhook_configs": [{"url": "http://x"}]}],
        "global": {"resolve_timeout": "5m"},
    }))
    assert main(["tree", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "default" in out
    assert "team=infra" in out


def test_tree_flags_route_issues(capsys):
    broken = os.path.join(os.path.dirname(__file__), "broken.yml")
    assert main(["tree", broken]) == 0
    out = capsys.readouterr().out
    assert "undefined-receiver" in out
    assert "bad-regex" in out
    assert "unreachable-route" in out
    assert "routing issues flagged above" in out
    assert "more issues outside routing" in out


def test_tree_no_root_route(tmp_path, capsys):
    cfg_path = tmp_path / "noroute.yml"
    cfg_path.write_text(yaml.dump({"receivers": [{"name": "a"}]}))
    assert main(["tree", str(cfg_path)]) == 2
    err = capsys.readouterr().err
    assert "No root 'route' defined" in err


def test_tree_stdin(capsys, monkeypatch):
    import io
    cfg = {"route": {"receiver": "default"}, "receivers": [{"name": "default"}]}
    monkeypatch.setattr("sys.stdin", io.StringIO(yaml.dump(cfg)))
    assert main(["tree", "-"]) == 0
    out = capsys.readouterr().out
    assert "default" in out


def test_tree_ignore_flag(tmp_path, capsys):
    cfg_path = tmp_path / "cfg.yml"
    cfg_path.write_text(yaml.dump({
        "route": {"receiver": "default", "group_by": ["alertname", "..."], "routes": [
            {"match": {"a": "b"}, "receiver": "default"},
        ]},
        "receivers": [{"name": "default", "webhook_configs": [{"url": "http://x"}]}],
        "global": {"resolve_timeout": "5m"},
    }))
    assert main(["tree", str(cfg_path), "--ignore", "groupby-ellipsis"]) == 0
    out = capsys.readouterr().out
    assert "groupby-ellipsis" not in out


# ── routing engine (unit) ────────────────────────────────────────────

def _receivers(route, labels):
    from amlint.routing import Router
    return [m.receiver for m in Router(route).match(labels)]


def test_route_deepest_match_wins():
    """A matched parent with a matched child does NOT also fire — only the
    deepest match does. Verified against amtool config routes test."""
    route = {
        "receiver": "default",
        "routes": [{
            "matchers": ['team="infra"'],
            "receiver": "infra-slack",
            "continue": True,
            "routes": [{"match": {"severity": "critical"}, "receiver": "infra-pager"}],
        }],
    }
    assert _receivers(route, {"team": "infra", "severity": "critical"}) == ["infra-pager"]


def test_route_parent_fires_when_no_child_matches():
    route = {
        "receiver": "default",
        "routes": [{
            "match": {"team": "infra"}, "receiver": "infra-slack",
            "routes": [{"match": {"severity": "critical"}, "receiver": "infra-pager"}],
        }],
    }
    assert _receivers(route, {"team": "infra", "severity": "warning"}) == ["infra-slack"]


def test_route_continue_fans_out_to_siblings():
    route = {
        "receiver": "default",
        "routes": [
            {"match": {"team": "infra"}, "receiver": "infra-slack", "continue": True},
            {"match": {"severity": "critical"}, "receiver": "pager"},
            {"match": {"severity": "critical"}, "receiver": "never"},
        ],
    }
    assert _receivers(route, {"team": "infra", "severity": "critical"}) == ["infra-slack", "pager"]


def test_route_continue_fans_out_inside_nested_routes():
    from amlint.routing import Router
    route = {
        "receiver": "default",
        "routes": [
            {"match": {"team": "infra"}, "receiver": "infra", "continue": True, "routes": [
                {"match": {"severity": "critical"}, "receiver": "infra-pager", "continue": True},
                {"match": {"env": "prod"}, "receiver": "infra-prod"},
            ]},
            {"match": {"env": "prod"}, "receiver": "prod-slack"},
        ],
    }
    matches = Router(route).match({"team": "infra", "severity": "critical", "env": "prod"})
    assert [m.receiver for m in matches] == ["infra-pager", "infra-prod", "prod-slack"]
    assert matches[0].path == ("route", "route.routes[0]", "route.routes[0].routes[0]")
    assert matches[2].path == ("route", "route.routes[1]")


def test_route_without_continue_stops_at_first_match():
    route = {
        "receiver": "default",
        "routes": [
            {"match": {"team": "infra"}, "receiver": "infra-slack"},
            {"match": {"severity": "critical"}, "receiver": "pager"},
        ],
    }
    assert _receivers(route, {"team": "infra", "severity": "critical"}) == ["infra-slack"]


def test_route_unmatched_falls_to_root():
    route = {"receiver": "default", "routes": [{"match": {"team": "infra"}, "receiver": "x"}]}
    assert _receivers(route, {"foo": "bar"}) == ["default"]


def test_route_inherits_receiver_grouping_and_timings():
    from amlint.routing import Router
    route = {
        "receiver": "default", "group_by": ["alertname"], "group_interval": "2m",
        "routes": [{
            "match": {"team": "infra"}, "receiver": "infra-slack", "group_wait": "10s",
            "routes": [{"match": {"severity": "critical"}, "repeat_interval": "1h"}],
        }],
    }
    [m] = Router(route).match({"team": "infra", "severity": "critical"})
    leaf = "route.routes[0].routes[0]"
    assert m.path[-1] == leaf
    assert (m.receiver, m.opts.group_by) == ("infra-slack", ("alertname",))
    assert (m.opts.group_wait, m.opts.group_interval, m.opts.repeat_interval) == ("10s", "2m", "1h")
    assert m.opts.origin == {
        "receiver": "route.routes[0]", "group_by": "route", "group_interval": "route",
        "group_wait": "route.routes[0]", "repeat_interval": leaf,
    }


def test_route_defaults_when_nothing_set():
    from amlint.routing import Router
    [m] = Router({"receiver": "default"}).match({})
    assert (m.opts.group_wait, m.opts.group_interval, m.opts.repeat_interval) == ("30s", "5m", "4h")
    assert m.opts.group_by == ()
    assert m.opts.origin == {"receiver": "route"}


def test_route_group_by_empty_resets_and_ellipsis_groups_by_all():
    from amlint.routing import Router
    route = {"receiver": "d", "group_by": ["alertname"], "routes": [
        {"match": {"a": "1"}, "group_by": []},
        {"match": {"a": "2"}, "group_by": ["..."]},
        {"match": {"a": "3"}},
    ]}
    router = Router(route)
    assert router.match({"a": "1"})[0].opts.group_by == ()
    assert router.match({"a": "2"})[0].opts.group_by == ("...",)
    assert router.match({"a": "3"})[0].opts.group_by == ("alertname",)


def test_route_time_intervals_are_not_inherited():
    from amlint.routing import Router
    route = {"receiver": "d", "routes": [{
        "match": {"team": "infra"}, "mute_time_intervals": ["weekends"],
        "routes": [{"match": {"severity": "critical"}}],
    }]}
    router = Router(route)
    assert router.match({"team": "infra"})[0].opts.mute_time_intervals == ("weekends",)
    child = router.match({"team": "infra", "severity": "critical"})[0]
    assert child.opts.mute_time_intervals == ()
    assert "mute_time_intervals" not in child.opts.origin


@pytest.mark.parametrize("spelling", [
    {"match_re": {"service": "api"}},
    {"matchers": ['service=~"api"']},
    {"matchers": ["service=~api"]},
])
def test_route_regex_is_fully_anchored(spelling):
    route = {"receiver": "default", "routes": [{**spelling, "receiver": "api-team"}]}
    assert _receivers(route, {"service": "api"}) == ["api-team"]
    assert _receivers(route, {"service": "api-gateway"}) == ["default"]
    assert _receivers(route, {"service": "legacy-api"}) == ["default"]


def test_route_regex_alternation_is_anchored_as_a_whole():
    route = {"receiver": "default", "routes": [
        {"matchers": ['env=~"prod|staging"'], "receiver": "envs"},
    ]}
    assert _receivers(route, {"env": "staging"}) == ["envs"]
    assert _receivers(route, {"env": "prod-eu"}) == ["default"]
    assert _receivers(route, {"env": "pre-staging"}) == ["default"]


def test_route_go_regex_syntax_matches_like_go():
    route = {"receiver": "default", "routes": [
        {"matchers": ['severity=~"(?i)crit.*"'], "receiver": "pager"},
        {"matchers": [r'code=~"\\d{3}"'], "receiver": "http"},
        {"matchers": ['host=~"[[:alpha:]]+-[[:digit:]]+"'], "receiver": "hosts"},
    ]}
    assert _receivers(route, {"severity": "CRITICAL"}) == ["pager"]
    assert _receivers(route, {"code": "503"}) == ["http"]
    assert _receivers(route, {"code": "١٢٣"}) == ["default"]  # Go's \d is ASCII-only
    assert _receivers(route, {"host": "web-01"}) == ["hosts"]
    assert _receivers(route, {"host": "web_01"}) == ["default"]


def test_route_negative_matchers():
    route = {"receiver": "default", "routes": [
        {"matchers": ['env!="prod"', 'severity!~"info|debug"'], "receiver": "non-prod"},
    ]}
    assert _receivers(route, {"env": "staging", "severity": "critical"}) == ["non-prod"]
    assert _receivers(route, {"env": "prod", "severity": "critical"}) == ["default"]
    assert _receivers(route, {"env": "staging", "severity": "info"}) == ["default"]
    # !~ is anchored too: "information" is not "info", so it does not exclude
    assert _receivers(route, {"env": "staging", "severity": "information"}) == ["non-prod"]


def test_route_missing_label_matches_as_empty_string():
    route = {"receiver": "default", "routes": [
        {"matchers": ['env=""'], "receiver": "no-env"},
        {"matchers": ['team!="infra"'], "receiver": "not-infra"},
    ]}
    assert _receivers(route, {}) == ["no-env"]
    assert _receivers(route, {"env": ""}) == ["no-env"]
    assert _receivers(route, {"env": "prod"}) == ["not-infra"]
    route = {"receiver": "default", "routes": [
        {"matchers": ['team=~".*"'], "receiver": "any"},
        {"matchers": ['team=~".+"'], "receiver": "some"},
    ]}
    assert _receivers(route, {}) == ["any"]  # .* matches the empty string
    route["routes"].reverse()
    assert _receivers(route, {}) == ["any"]  # .+ does not


def test_route_label_values_are_strings():
    route = {"receiver": "default", "routes": [{"match": {"code": 500}, "receiver": "http"}]}
    assert _receivers(route, {"code": 500}) == ["http"]
    assert _receivers(route, {"code": "500"}) == ["http"]


@pytest.mark.parametrize("route, message", [
    ({"receiver": "d", "routes": [{"matchers": ['a=~"(?=x)"']}]}, "lookahead"),
    ({"receiver": "d", "routes": [{"matchers": ['a=~"\\\\pL+"']}]}, "cannot evaluate"),
    ({"receiver": "d", "routes": [{"group_wait": "soon"}]}, "not a valid duration"),
    ({"receiver": "d", "routes": [{"group_by": ["...", "a"]}]}, "mixes '...'"),
    ({"receiver": "d", "match": {"a": "b"}}, "root route has matchers"),
])
def test_router_rejects_configs_it_cannot_simulate(route, message):
    from amlint.routing import Router, RoutingError
    with pytest.raises(RoutingError, match=re.escape(message)):
        Router(route)


# ── amlint route (CLI) ───────────────────────────────────────────────

def _write_route_config(tmp_path):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({
        "route": {"receiver": "default", "group_by": ["alertname"], "routes": [
            {"matchers": ['team="infra"'], "receiver": "infra-slack", "continue": True,
             "group_wait": "10s",
             "routes": [{"matchers": ['severity="critical"'], "receiver": "infra-pager"}]},
            {"matchers": ['severity="critical"'], "receiver": "pager"},
        ]},
        "receivers": [{"name": n} for n in ("default", "infra-slack", "infra-pager", "pager")],
    }))
    return cfg_path


def test_cli_route_text(tmp_path, capsys):
    cfg_path = _write_route_config(tmp_path)
    assert main(["route", str(cfg_path), "team=infra", "severity=critical"]) == 0
    out = capsys.readouterr().out
    assert "route → route.routes[0] → route.routes[0].routes[0]" in out
    assert "route → route.routes[1]" in out
    assert "inherited from route.routes[0]" in out
    assert "Alertmanager default" in out
    assert "Delivered to: infra-pager, pager" in out


def test_cli_route_json(tmp_path, capsys):
    cfg_path = _write_route_config(tmp_path)
    assert main(["route", str(cfg_path), "team=infra", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["labels"] == {"team": "infra"}
    assert data["receivers"] == ["infra-slack"]
    [r] = data["routes"]
    assert r["path"] == ["route", "route.routes[0]"]
    assert r["group_by"] == ["alertname"]
    assert (r["group_wait"], r["group_interval"], r["repeat_interval"]) == ("10s", "5m", "4h")
    assert r["set_by"]["group_wait"] == "route.routes[0]"
    assert "repeat_interval" not in r["set_by"]


def test_cli_route_value_with_equals_sign(tmp_path, capsys):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({"route": {"receiver": "d", "routes": [
        {"match": {"query": "a=b"}, "receiver": "q"}]}}))
    assert main(["route", str(cfg_path), "query=a=b", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["receivers"] == ["q"]


def test_cli_route_bad_label_argument(tmp_path, capsys):
    cfg_path = _write_route_config(tmp_path)
    assert main(["route", str(cfg_path), "severity"]) == 2
    assert "expected label=value" in capsys.readouterr().err


def test_cli_route_unsimulatable_config(tmp_path, capsys):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({"receivers": [{"name": "x"}]}))
    assert main(["route", str(cfg_path), "a=b"]) == 2
    assert "no root 'route'" in capsys.readouterr().err


# ── amlint test (CLI) ────────────────────────────────────────────────

def _write_test_config(tmp_path):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({
        "route": {"receiver": "default", "routes": [
            {"match": {"team": "infra"}, "receiver": "infra-slack"},
        ]},
        "receivers": [{"name": "default"}, {"name": "infra-slack"}],
    }))
    return cfg_path


def test_cli_test_all_pass(tmp_path, capsys):
    cfg_path = _write_test_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": [
        {"name": "infra routes to slack", "labels": {"team": "infra"}, "receiver": "infra-slack"},
        {"name": "unmatched routes to default", "labels": {"foo": "bar"}, "receiver": "default"},
    ]}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 0
    out = capsys.readouterr().out
    assert "2 passed" in out


def test_cli_test_failure_exits_nonzero(tmp_path, capsys):
    cfg_path = _write_test_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": [
        {"name": "wrong expectation", "labels": {"team": "infra"}, "receiver": "nonexistent"},
    ]}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 1
    out = capsys.readouterr().out
    assert "expected:" in out
    assert "nonexistent" in out
    assert "infra-slack" in out


def test_cli_test_drop_assertion(tmp_path, capsys):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({
        "route": {"routes": [{"match": {"a": "b"}, "receiver": "x"}]},
        "receivers": [{"name": "x"}],
    }))
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": [
        {"name": "no root receiver, no match -> drop", "labels": {"foo": "bar"}, "drop": True},
    ]}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 0


def test_cli_test_malformed_case(tmp_path, capsys):
    cfg_path = _write_test_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": [
        {"name": "bad case", "labels": {"a": "b"}},  # missing receiver/receivers/drop
    ]}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 1
    out = capsys.readouterr().out
    assert "must set exactly one of" in out


def test_cli_test_no_root_route(tmp_path):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({"receivers": [{"name": "x"}]}))
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": []}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 2


def test_cli_test_invalid_tests_file(tmp_path):
    cfg_path = _write_test_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"not_tests": []}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 2


def test_cli_test_json_format(tmp_path, capsys):
    cfg_path = _write_test_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": [
        {"name": "t1", "labels": {"team": "infra"}, "receiver": "infra-slack"},
    ]}))
    assert main(["test", str(cfg_path), str(tests_path), "--format", "json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data[0]["passed"] is True
    assert data[0]["name"] == "t1"


def test_cli_test_multi_receiver_assertion(tmp_path):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({
        "route": {"receiver": "default", "routes": [
            {"match": {"team": "infra"}, "receiver": "infra-slack", "continue": True},
            {"match": {"severity": "critical"}, "receiver": "pager"},
        ]},
        "receivers": [{"name": "default"}, {"name": "infra-slack"}, {"name": "pager"}],
    }))
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": [
        {"name": "fan-out", "labels": {"team": "infra", "severity": "critical"},
         "receivers": ["infra-slack", "pager"]},
    ]}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 0


def _write_fanout_config(tmp_path):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({
        "route": {"receiver": "default", "routes": [
            {"match": {"team": "infra"}, "receiver": "infra-slack", "continue": True},
            {"match": {"severity": "critical"}, "receiver": "pager"},
            {"match_re": {"service": "api"}, "receiver": "api-team"},
        ]},
        "receivers": [{"name": n} for n in ("default", "infra-slack", "pager", "api-team")],
    }))
    return cfg_path


def test_cli_test_bare_list_with_expect_ignores_order(tmp_path, capsys):
    cfg_path = _write_fanout_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump([
        {"name": "fan-out", "labels": {"team": "infra", "severity": "critical"},
         "expect": ["pager", "infra-slack"]},
        {"name": "single receiver as a string", "labels": {"severity": "critical"},
         "expect": "pager"},
        {"name": "regex is anchored", "labels": {"service": "api-gateway"},
         "expect": ["default"]},
    ]))
    assert main(["test", str(cfg_path), str(tests_path)]) == 0
    assert "3 passed" in capsys.readouterr().out


def test_cli_test_expect_failure_shows_diff(tmp_path, capsys):
    cfg_path = _write_fanout_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump([
        {"name": "infra only", "labels": {"team": "infra", "severity": "critical"},
         "expect": ["infra-slack", "api-team"]},
    ]))
    assert main(["test", str(cfg_path), str(tests_path)]) == 1
    out = capsys.readouterr().out
    assert "- api-team" in out and "expected, not delivered" in out
    assert "+ pager" in out and "delivered, not expected" in out
    assert "route.routes[0], route.routes[1]" in out
    assert "1 failed" in out


def test_cli_test_ordered_receivers_reports_order_only_mismatch(tmp_path, capsys):
    cfg_path = _write_fanout_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump({"tests": [
        {"name": "wrong order", "labels": {"team": "infra", "severity": "critical"},
         "receivers": ["pager", "infra-slack"]},
    ]}))
    assert main(["test", str(cfg_path), str(tests_path)]) == 1
    assert "different order" in capsys.readouterr().out


def test_cli_test_json_has_diff_fields(tmp_path, capsys):
    cfg_path = _write_fanout_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump([
        {"name": "t", "labels": {"team": "infra"}, "expect": ["pager"]},
    ]))
    assert main(["test", str(cfg_path), str(tests_path), "--format", "json"]) == 1
    [r] = json.loads(capsys.readouterr().out)
    assert r["passed"] is False
    # continue: true with no later sibling matching does not fall back to the root
    assert (r["expected"], r["actual"]) == ("pager", "infra-slack")
    assert r["missing"] == ["pager"]
    assert r["unexpected"] == ["infra-slack"]
    assert r["routes"] == ["route.routes[0]"]


@pytest.mark.parametrize("case, message", [
    ("not a mapping", "must be a mapping"),
    ({"labels": ["team=infra"], "expect": ["x"]}, "labels: must be a mapping"),
    ({"labels": {}, "expect": ["x"], "receiver": "x"}, "exactly one of"),
    ({"labels": {}, "drop": False}, "drop: only accepts true"),
])
def test_cli_test_invalid_cases_fail(tmp_path, capsys, case, message):
    cfg_path = _write_fanout_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump([case]))
    assert main(["test", str(cfg_path), str(tests_path)]) == 1
    assert message in capsys.readouterr().out


def test_cli_test_unsimulatable_config_exits_2(tmp_path, capsys):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.dump({"route": {"receiver": "d", "routes": [
        {"matchers": ['a=~"(?<=x)y"'], "receiver": "d"}]}}))
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump([{"labels": {}, "expect": ["d"]}]))
    assert main(["test", str(cfg_path), str(tests_path)]) == 2
    assert "route.routes[0]" in capsys.readouterr().err


# ── route coverage (amlint test --coverage) ──────────────────────────

def _coverage(route, *label_sets):
    from amlint.routing import Router, route_coverage
    router = Router(route)
    return route_coverage(router, [router.match(labels) for labels in label_sets])


_NESTED_ROUTE = {"receiver": "default", "routes": [
    {"match": {"team": "infra"}, "receiver": "infra", "routes": [
        {"match": {"severity": "critical"}, "receiver": "infra-pager", "routes": [
            {"match": {"env": "prod"}, "receiver": "infra-prod-pager"},
        ]},
        {"match": {"severity": "warning"}, "receiver": "infra-slack"},
    ]},
    {"match": {"team": "db"}, "receiver": "db"},
]}


def test_route_coverage_lists_every_route_depth_first():
    cov = _coverage(_NESTED_ROUTE)
    assert cov.routes == [
        "route", "route.routes[0]", "route.routes[0].routes[0]",
        "route.routes[0].routes[0].routes[0]", "route.routes[0].routes[1]", "route.routes[1]",
    ]
    assert cov.uncovered == cov.routes and cov.percent == 0


def test_route_coverage_counts_every_route_on_the_match_path():
    cov = _coverage(_NESTED_ROUTE, {"team": "infra", "severity": "critical", "env": "prod"})
    # the parents the alert went through are covered, not only the route it ended on
    assert cov.covered == {
        "route", "route.routes[0]", "route.routes[0].routes[0]",
        "route.routes[0].routes[0].routes[0]",
    }
    assert cov.uncovered == ["route.routes[0].routes[1]", "route.routes[1]"]
    assert cov.percent == pytest.approx(400 / 6)


def test_route_coverage_parent_match_does_not_cover_its_children():
    cov = _coverage(_NESTED_ROUTE, {"team": "infra", "severity": "info"}, {"team": "unknown"})
    assert cov.covered == {"route", "route.routes[0]"}


def test_route_coverage_follows_continue_fan_out():
    route = {"receiver": "default", "routes": [
        {"match": {"team": "infra"}, "receiver": "infra", "continue": True, "routes": [
            {"match": {"severity": "critical"}, "receiver": "infra-pager", "continue": True},
            {"match": {"env": "prod"}, "receiver": "infra-prod"},
            {"match": {"env": "prod"}, "receiver": "never"},  # previous sibling has no continue
        ]},
        {"match": {"env": "prod"}, "receiver": "prod"},
        {"match": {"env": "prod"}, "receiver": "also-never"},
    ]}
    cov = _coverage(route, {"team": "infra", "severity": "critical", "env": "prod"})
    assert cov.uncovered == ["route.routes[0].routes[2]", "route.routes[2]"]


def test_route_coverage_shadowed_route_is_never_covered():
    route = {"receiver": "default", "routes": [
        {"receiver": "catch-all"},
        {"match": {"team": "db"}, "receiver": "db"},
    ]}
    cov = _coverage(route, {"team": "db"}, {})
    assert cov.uncovered == ["route.routes[1]"]


def test_coverage_percent_is_100_only_when_complete():
    from amlint.cli import _coverage_percent
    from amlint.routing import Coverage
    routes = [str(i) for i in range(10000)]
    assert _coverage_percent(Coverage(routes, set(routes[1:]))) == 99.9
    assert _coverage_percent(Coverage(routes, set(routes))) == 100.0
    assert _coverage_percent(Coverage(routes[:3], set(routes[:2]))) == 66.7


COVERAGE_CONFIG = """\
route:
  receiver: default
  routes:
    - matchers: ['team="infra"']
      receiver: infra
      continue: true
      routes:
        - matchers: ['severity="critical"']
          receiver: infra-pager
    - matchers: ['severity="critical"']
      receiver: pager
    - matchers: ['team="db"']
      receiver: db
      routes:
        - matchers: ['severity="warning"']
          receiver: db-slack
"""


def _write_coverage_suite(tmp_path, cases=None):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(COVERAGE_CONFIG)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump(cases if cases is not None else [
        {"name": "critical infra fans out", "labels": {"team": "infra", "severity": "critical"},
         "expect": ["infra-pager", "pager"]},
    ]))
    return str(cfg_path), str(tests_path)


def test_cli_test_coverage_lists_uncovered_routes_with_locations(tmp_path, capsys):
    cfg_path, tests_path = _write_coverage_suite(tmp_path)
    assert main(["test", cfg_path, tests_path, "--coverage"]) == 0
    out = capsys.readouterr().out
    assert "1 passed" in out
    assert "4 of 6 routes" in out and "66.7%" in out
    lines = [line.split() for line in out.splitlines() if "route.routes" in line]
    assert [(words[0], words[1]) for words in lines] == [
        ("route.routes[2]", f"{cfg_path}:12:7"),
        ("route.routes[2].routes[0]", f"{cfg_path}:15:11"),
    ]
    assert 'team="db"' in out and "db-slack" in out


def test_cli_test_without_coverage_flag_has_no_coverage(tmp_path, capsys):
    cfg_path, tests_path = _write_coverage_suite(tmp_path)
    assert main(["test", cfg_path, tests_path]) == 0
    assert "coverage" not in capsys.readouterr().out.lower()
    assert main(["test", cfg_path, tests_path, "--format", "json"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_cli_test_full_coverage(tmp_path, capsys):
    cfg_path, tests_path = _write_coverage_suite(tmp_path, [
        {"labels": {"team": "infra", "severity": "critical"}, "expect": ["infra-pager", "pager"]},
        {"labels": {"team": "db", "severity": "warning"}, "expect": ["db-slack"]},
    ])
    assert main(["test", cfg_path, tests_path, "--fail-under", "100"]) == 0
    out = capsys.readouterr().out
    assert "6 of 6 routes" in out and "100.0%" in out
    assert "Not matched" not in out


@pytest.mark.parametrize("fail_under, code", [("60", 0), ("66.7", 0), ("66.8", 1), ("90", 1)])
def test_cli_test_fail_under(tmp_path, capsys, fail_under, code):
    cfg_path, tests_path = _write_coverage_suite(tmp_path)
    # --fail-under implies --coverage; all cases pass, so only coverage decides
    assert main(["test", cfg_path, tests_path, "--fail-under", fail_under]) == code
    out = capsys.readouterr().out
    assert "4 of 6 routes" in out
    assert ("below --fail-under" in out) == bool(code)


def test_cli_test_fail_under_and_failing_case_both_exit_1(tmp_path, capsys):
    cfg_path, tests_path = _write_coverage_suite(tmp_path, [
        {"labels": {"team": "db"}, "expect": ["wrong"]},
    ])
    assert main(["test", cfg_path, tests_path, "--fail-under", "0"]) == 1
    # a failing case still covers the routes the alert went through
    assert "2 of 6 routes" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["101", "-1", "abc", "nan"])
def test_cli_test_fail_under_rejects_bad_values(tmp_path, capsys, value):
    cfg_path, tests_path = _write_coverage_suite(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["test", cfg_path, tests_path, "--fail-under", value])
    assert exc.value.code == 2
    assert "percentage from 0 to 100" in capsys.readouterr().err


def test_cli_test_coverage_json(tmp_path, capsys):
    cfg_path, tests_path = _write_coverage_suite(tmp_path, [
        {"labels": {"team": "infra", "severity": "critical"}, "expect": ["infra-pager", "pager"]},
        "not a mapping",  # malformed cases don't cover anything
    ])
    assert main(["test", cfg_path, tests_path, "--format", "json", "--fail-under", "80"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [t["passed"] for t in data["tests"]] == [True, False]
    assert data["coverage"] == {
        "routes": 6, "covered": 4, "percent": 66.7, "fail_under": 80.0,
        "uncovered": [
            {"path": "route.routes[2]", "line": 12, "column": 7},
            {"path": "route.routes[2].routes[0]", "line": 15, "column": 11},
        ],
    }


# ── inhibition (unit) ────────────────────────────────────────────────

def _inhibited_by(rules, labels, *firing):
    from amlint.routing import Inhibitor
    inhibition = Inhibitor(rules).inhibited_by(labels, firing)
    return inhibition and inhibition.rule


_NODE_DOWN = {"source_matchers": ["alertname=NodeDown"],
              "target_matchers": ['severity="warning"'], "equal": ["instance"]}


def test_inhibition_source_target_and_equal():
    from amlint.routing import Inhibition, Inhibitor
    alert = {"alertname": "HighCPU", "severity": "warning", "instance": "a"}
    node_down_a = {"alertname": "NodeDown", "instance": "a"}
    assert Inhibitor([_NODE_DOWN]).inhibited_by(alert, [node_down_a]) == Inhibition(
        "inhibit_rules[0]", node_down_a
    )
    # equal label differs
    assert _inhibited_by([_NODE_DOWN], alert, {"alertname": "NodeDown", "instance": "b"}) is None
    # firing alert doesn't match the source side
    assert _inhibited_by([_NODE_DOWN], alert, {"alertname": "NodeUp", "instance": "a"}) is None
    # the alert doesn't match the target side
    assert _inhibited_by([_NODE_DOWN], {**alert, "severity": "critical"}, node_down_a) is None
    # nothing firing, nothing inhibits
    assert _inhibited_by([_NODE_DOWN], alert) is None


def test_inhibition_equal_label_missing_on_both_sides_counts_as_equal():
    alert = {"alertname": "HighCPU", "severity": "warning"}
    assert _inhibited_by([_NODE_DOWN], alert, {"alertname": "NodeDown"}) == "inhibit_rules[0]"
    # a label set to "" is the same as a missing one
    assert _inhibited_by([_NODE_DOWN], {**alert, "instance": ""},
                         {"alertname": "NodeDown"}) == "inhibit_rules[0]"
    # missing on one side only is not equal
    assert _inhibited_by([_NODE_DOWN], alert, {"alertname": "NodeDown", "instance": "a"}) is None
    assert _inhibited_by([_NODE_DOWN], {**alert, "instance": "a"},
                         {"alertname": "NodeDown"}) is None


def test_inhibition_without_equal_matches_any_source():
    rule = {k: v for k, v in _NODE_DOWN.items() if k != "equal"}
    assert _inhibited_by([rule], {"severity": "warning", "instance": "a"},
                         {"alertname": "NodeDown", "instance": "b"}) == "inhibit_rules[0]"


# Modelled on TestInhibitRuleMatches in Alertmanager's inhibit/inhibit_test.go.
_TWO_SIDED_RULES = [
    {"source_match": {"s1": "1"}, "target_match": {"t1": "1"}, "equal": ["e"]},
    {"source_match": {"s2": "1"}, "target_match": {"t2": "1"}, "equal": ["e"]},
]
_TWO_SIDED_FIRING = [
    {"s1": "1", "t1": "2", "e": "1"},  # source side of rule 0 only
    {"s2": "1", "t2": "1", "e": "1"},  # both sides of rule 1
]


@pytest.mark.parametrize("labels, rule", [
    ({"t1": "1", "e": "1"}, "inhibit_rules[0]"),
    ({"t2": "1", "e": "1"}, "inhibit_rules[1]"),
    ({"t1": "1", "t3": "1", "e": "1"}, "inhibit_rules[0]"),
    ({"t1": "1", "t2": "1", "e": "1"}, "inhibit_rules[0]"),
    ({"t1": "0", "e": "1"}, None),
    # on both sides of rule 0; the firing source alert is only on the source side
    ({"s1": "1", "t1": "1", "e": "1"}, "inhibit_rules[0]"),
    # on both sides of rule 1, and so is the only firing source: no inhibition
    ({"s2": "1", "t2": "1", "e": "1"}, None),
    ({"t1": "1", "e": "0"}, None),
])
def test_inhibition_two_sided_matches_like_alertmanager(labels, rule):
    assert _inhibited_by(_TWO_SIDED_RULES, labels, *_TWO_SIDED_FIRING) == rule


def test_inhibition_alert_does_not_inhibit_itself():
    rule = {"source_matchers": ['severity="critical"'],
            "target_matchers": ['severity=~"critical|warning"'], "equal": ["cluster"]}
    alert = {"alertname": "ClusterDown", "severity": "critical", "cluster": "eu"}
    # the same alert firing, or another alert on both sides of the rule, doesn't inhibit
    assert _inhibited_by([rule], alert, alert) is None
    assert _inhibited_by([rule], alert, {**alert, "alertname": "APIDown"}) is None
    # a warning is on the target side only, so the critical one does inhibit it
    assert _inhibited_by([rule], {**alert, "severity": "warning"}, alert) == "inhibit_rules[0]"


def test_inhibition_regex_matchers_are_anchored():
    rules = [
        {"source_matchers": ['alertname=~"Node(Down|Unreachable)"'],
         "target_matchers": ['alertname!~"Node.*"'], "equal": ["instance"]},
    ]
    alert = {"alertname": "DiskFull", "instance": "a"}
    for source in ("NodeDown", "NodeUnreachable"):
        assert _inhibited_by(rules, alert, {"alertname": source, "instance": "a"})
    # anchored: NodeDownSoon is not NodeDown
    assert _inhibited_by(rules, alert, {"alertname": "NodeDownSoon", "instance": "a"}) is None
    # the target side excludes Node* alerts
    assert _inhibited_by(rules, {"alertname": "NodeLoad", "instance": "a"},
                         {"alertname": "NodeDown", "instance": "a"}) is None


@pytest.mark.parametrize("rule", [
    {"source_match": {"alertname": "NodeDown"}, "target_match": {"severity": "warning"}},
    {"source_match_re": {"alertname": "Node.*"}, "target_match_re": {"severity": "warn(ing)?"}},
    {"source_matchers": ['{alertname="NodeDown"}'], "target_matchers": ["severity=warning"]},
])
def test_inhibition_legacy_and_matchers_syntax(rule):
    rule = {**rule, "equal": ["instance"]}
    alert = {"severity": "warning", "instance": "a"}
    assert _inhibited_by([rule], alert, {"alertname": "NodeDown", "instance": "a"})
    assert _inhibited_by([rule], {**alert, "severity": "warn"},
                         {"alertname": "NodeDown", "instance": "a"}) == (
        "inhibit_rules[0]" if "target_match_re" in rule else None
    )


def test_inhibition_rules_are_checked_in_order():
    rules = [
        {"source_match": {"alertname": "ClusterDown"}, "target_match": {"severity": "warning"},
         "equal": ["cluster"]},
        _NODE_DOWN,
    ]
    alert = {"alertname": "HighCPU", "severity": "warning", "cluster": "eu", "instance": "a"}
    cluster_down = {"alertname": "ClusterDown", "cluster": "eu"}
    node_down = {"alertname": "NodeDown", "instance": "a"}
    assert _inhibited_by(rules, alert, node_down) == "inhibit_rules[1]"
    assert _inhibited_by(rules, alert, node_down, cluster_down) == "inhibit_rules[0]"
    assert _inhibited_by(rules, {**alert, "cluster": "us"}, cluster_down) is None


def test_inhibition_by_an_alert_that_is_itself_inhibited():
    rules = [
        {"source_match": {"alertname": "ClusterDown"}, "target_match": {"alertname": "NodeDown"}},
        {"source_match": {"alertname": "NodeDown"}, "target_match": {"alertname": "HighCPU"}},
    ]
    assert _inhibited_by(rules, {"alertname": "HighCPU"},
                         {"alertname": "ClusterDown"}, {"alertname": "NodeDown"}) == (
        "inhibit_rules[1]"
    )


def test_inhibition_skips_malformed_rules_and_normalizes_values():
    rules = ["not a rule", {"source_match": {"code": 500}, "target_match": {"up": False},
                            "equal": ["port"]}]
    assert _inhibited_by(rules, {"up": "false", "port": 80},
                         {"code": "500", "port": "80"}) == "inhibit_rules[1]"


@pytest.mark.parametrize("rule, message", [
    ({"source_matchers": ["alertname"]}, "inhibit_rules[0]: cannot parse matcher"),
    ({"target_matchers": ['a=~"(?=x)"']}, "inhibit_rules[0]: regex"),
    ({"source_match_re": {"a": "(unclosed"}}, "inhibit_rules[0]: regex"),
    ({"equal": "instance"}, "inhibit_rules[0]: equal must be a list"),
])
def test_inhibitor_rejects_rules_it_cannot_simulate(rule, message):
    from amlint.routing import Inhibitor, RoutingError
    with pytest.raises(RoutingError, match=re.escape(message)):
        Inhibitor([rule])


# ── inhibition in amlint route / amlint test (CLI) ───────────────────

INHIBIT_CONFIG = """\
route:
  receiver: default
  routes:
    - matchers: ['team="db"']
      receiver: db
receivers:
  - name: default
  - name: db
inhibit_rules:
  - source_matchers: ['severity="critical"']
    target_matchers: ['severity="warning"']
    equal: [alertname]
  - source_match: {alertname: NodeDown}
    target_match_re: {alertname: '.+'}
    equal: [instance]
"""


def _write_inhibit_config(tmp_path, text=INHIBIT_CONFIG):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(text)
    return str(cfg_path)


def test_cli_route_firing_shows_the_inhibiting_rule(tmp_path, capsys):
    cfg_path = _write_inhibit_config(tmp_path)
    assert main(["route", cfg_path, "alertname=DiskFull", "instance=a", "team=db",
                 "--firing", "alertname=NodeDown,instance=a"]) == 0
    out = capsys.readouterr().out
    assert "Firing  alertname=NodeDown, instance=a" in out
    assert f"Inhibited by inhibit_rules[1]  ·  {cfg_path}:13:5" in out
    assert "while firing: alertname=NodeDown, instance=a" in out
    assert "Routed to: db, but no notification is sent" in out
    assert "Delivered to" not in out


def test_cli_route_firing_not_inhibited(tmp_path, capsys):
    cfg_path = _write_inhibit_config(tmp_path)
    assert main(["route", cfg_path, "alertname=DiskFull", "instance=a",
                 "--firing", "alertname=NodeDown,instance=b",
                 "--firing", '{alertname="Other", instance="a"}']) == 0
    out = capsys.readouterr().out
    assert "Delivered to: default" in out
    assert "Not inhibited by the 2 firing alerts" in out


def test_cli_route_firing_json(tmp_path, capsys):
    cfg_path = _write_inhibit_config(tmp_path)
    args = ["route", cfg_path, "alertname=HighCPU", "severity=warning", "--format", "json"]
    assert main(args + ["--firing", "alertname=HighCPU,severity=critical"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["receivers"] == ["default"]
    assert data["firing"] == [{"alertname": "HighCPU", "severity": "critical"}]
    assert data["inhibited"] is True
    assert data["inhibited_by"] == {
        "rule": "inhibit_rules[0]", "line": 10, "column": 5,
        "source": {"alertname": "HighCPU", "severity": "critical"},
    }
    assert main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["firing"], data["inhibited"], data["inhibited_by"]) == ([], False, None)


def test_cli_route_node_down_does_not_inhibit_itself(tmp_path, capsys):
    cfg_path = _write_inhibit_config(tmp_path)
    assert main(["route", cfg_path, "alertname=NodeDown", "instance=a", "--format", "json",
                 "--firing", "alertname=NodeDown,instance=a"]) == 0
    assert json.loads(capsys.readouterr().out)["inhibited"] is False


@pytest.mark.parametrize("firing", ["alertname", "a!=b", "a=~b.*", "", "=x"])
def test_cli_route_bad_firing_argument(tmp_path, capsys, firing):
    cfg_path = _write_inhibit_config(tmp_path)
    assert main(["route", cfg_path, "a=b", "--firing", firing]) == 2
    assert "Invalid --firing" in capsys.readouterr().err


def test_cli_route_unsimulatable_inhibit_rule(tmp_path, capsys):
    cfg_path = _write_inhibit_config(tmp_path, INHIBIT_CONFIG + "  - source_matchers: [oops]\n")
    # without --firing the inhibit rules aren't needed
    assert main(["route", cfg_path, "a=b"]) == 0
    capsys.readouterr()
    assert main(["route", cfg_path, "a=b", "--firing", "alertname=NodeDown"]) == 2
    err = capsys.readouterr().err
    assert "Cannot simulate inhibition" in err and "inhibit_rules[2]" in err


def _run_inhibit_tests(tmp_path, cases, *extra):
    cfg_path = _write_inhibit_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump(cases))
    return main(["test", cfg_path, str(tests_path), *extra]), cfg_path


def test_cli_test_inhibited_assertions(tmp_path, capsys):
    code, _ = _run_inhibit_tests(tmp_path, [
        {"name": "disk alert muted while its node is down",
         "labels": {"alertname": "DiskFull", "instance": "a", "team": "db"},
         "firing": [{"alertname": "NodeDown", "instance": "a"}],
         "expect": ["db"], "inhibited": True},
        {"name": "another node's outage doesn't mute it",
         "labels": {"alertname": "DiskFull", "instance": "a"},
         "firing": [{"alertname": "NodeDown", "instance": "b"}],
         "expect": ["default"]},  # firing: without inhibited: expects false
        {"name": "inhibited: on its own",
         "labels": {"alertname": "X", "severity": "warning"},
         "firing": [{"alertname": "X", "severity": "critical"}], "inhibited": True},
        {"name": "nothing firing", "labels": {"alertname": "X"}, "inhibited": False},
    ])
    assert code == 0
    assert "4 passed" in capsys.readouterr().out


def test_cli_test_unexpected_inhibition_fails_with_the_rule(tmp_path, capsys):
    code, cfg_path = _run_inhibit_tests(tmp_path, [
        {"name": "warning should still page",
         "labels": {"alertname": "X", "severity": "warning"},
         "firing": [{"alertname": "X", "severity": "critical"}],
         "expect": ["default"]},
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "firing:    alertname=X, severity=critical" in out
    assert "inhibited: expected no, actual yes" in out
    assert f"by inhibit_rules[0]  ·  {cfg_path}:10:5" in out


def test_cli_test_missing_inhibition_fails(tmp_path, capsys):
    code, _ = _run_inhibit_tests(tmp_path, [
        {"labels": {"alertname": "X", "severity": "warning"},
         "firing": [{"alertname": "Y", "severity": "critical"}],
         "inhibited": True},
    ], "--format", "json")
    assert code == 1
    [r] = json.loads(capsys.readouterr().out)
    assert r["passed"] is False
    assert r["expected"] is None  # no receiver assertion
    assert (r["expected_inhibited"], r["inhibited"], r["inhibited_by"]) == (True, False, None)


def test_cli_test_inhibition_json(tmp_path, capsys):
    code, _ = _run_inhibit_tests(tmp_path, [
        {"labels": {"alertname": "X", "severity": "warning"},
         "firing": [{"alertname": "X", "severity": "critical"}],
         "receiver": "default", "inhibited": True},
    ], "--format", "json")
    assert code == 0
    [r] = json.loads(capsys.readouterr().out)
    assert r["firing"] == [{"alertname": "X", "severity": "critical"}]
    assert r["inhibited_by"] == {"rule": "inhibit_rules[0]", "line": 10, "column": 5,
                                 "source": {"alertname": "X", "severity": "critical"}}


@pytest.mark.parametrize("case, message", [
    ({"labels": {}, "firing": {"alertname": "X"}, "expect": []}, "firing: must be a list"),
    ({"labels": {}, "firing": ["alertname=X"], "expect": []}, "firing: must be a list"),
    ({"labels": {}, "inhibited": "yes"}, "inhibited: must be true or false"),
    ({"labels": {}, "expect": [], "inhibited": None}, "inhibited: must be true or false"),
    ({"labels": {}, "firing": []}, "exactly one of"),
])
def test_cli_test_invalid_inhibition_cases_fail(tmp_path, capsys, case, message):
    code, _ = _run_inhibit_tests(tmp_path, [case])
    assert code == 1
    assert message in capsys.readouterr().out


def test_cli_test_unsimulatable_inhibit_rule_exits_2(tmp_path, capsys):
    lookbehind = "    source_matchers: ['a=~\"(?<=x)\"']\n"  # added to inhibit_rules[1]
    cfg_path = _write_inhibit_config(tmp_path, INHIBIT_CONFIG + lookbehind)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text(yaml.dump([{"labels": {}, "expect": ["default"]}]))
    # only cases with firing: need the inhibit rules
    assert main(["test", cfg_path, str(tests_path)]) == 0
    tests_path.write_text(yaml.dump([{"labels": {}, "firing": [{"a": "b"}], "inhibited": False}]))
    assert main(["test", cfg_path, str(tests_path)]) == 2
    assert "inhibit_rules[1]" in capsys.readouterr().err


# ── Matchers syntax: match / match_re / matchers normalized ──────────

def test_parse_matchers_quoting_and_braces():
    from amlint.linter import _parse_matchers
    assert _parse_matchers(['{team="infra", severity=~"crit|warn"}', 'env!=prod']) == [
        ("team", "=", "infra"), ("severity", "=~", "crit|warn"), ("env", "!=", "prod"),
    ]
    # \. is a regex escape, not a string escape: Alertmanager keeps the backslash
    assert _parse_matchers([r'svc=~"foo\.bar"']) == [("svc", "=~", r"foo\.bar")]
    assert _parse_matchers(['msg="a, b"']) == [("msg", "=", "a, b")]


def test_inhibit_no_equal_matchers_syntax():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"source_matchers": ['severity="critical"'],
                           "target_matchers": ['severity="warning"']}],
    }
    assert "inhibit-no-equal" in codes(cfg)


def test_inhibit_same_match_matchers_syntax():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"source_matchers": ['alertname="Down"', 'severity="critical"'],
                           "target_matchers": ['alertname="Down"'],
                           "equal": ["instance"]}],
    }
    assert "inhibit-same-match" in codes(cfg)


def test_circular_inhibition_matchers_syntax():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [
            {"source_matchers": ["severity=critical"], "target_matchers": ["severity=warning"],
             "equal": ["alertname"]},
            {"source_match": {"severity": "warning"}, "target_matchers": ['severity="critical"'],
             "equal": ["alertname"]},
        ],
    }
    assert "circular-inhibition" in codes(cfg)


def test_circular_inhibition_one_shared_label_is_not_enough():
    # B's source (ZoneDown) is never A's target (PodDown); the shared namespace=x doesn't
    # change that
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [
            {"source_matchers": ["alertname=NodeDown", "namespace=x"],
             "target_matchers": ["alertname=PodDown", "namespace=x"], "equal": ["node"]},
            {"source_matchers": ["alertname=ZoneDown", "namespace=x"],
             "target_matchers": ["alertname=NodeDown", "namespace=x"], "equal": ["zone"]},
        ],
    }
    assert "circular-inhibition" not in codes(cfg)


def test_circular_inhibition_with_a_shared_extra_label():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [
            {"source_matchers": ["severity=critical", "namespace=x"],
             "target_matchers": ["severity=warning", "namespace=x"], "equal": ["alertname"]},
            {"source_matchers": ["severity=warning", "namespace=x"],
             "target_matchers": ["severity=critical", "namespace=x"], "equal": ["alertname"]},
        ],
    }
    found = [f for f in lint(cfg) if f.code == "circular-inhibition"]
    assert [f.where for f in found] == ["inhibit_rules[0]"]


def test_circular_inhibition_source_equal_cannot_agree():
    # the critical alert that mutes ZoneDown is a NodeDown, so A (equal: [alertname]) never
    # mutes ZoneDown while it fires
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [
            {"source_matchers": ["severity=critical", "namespace=x"],
             "target_matchers": ["severity=warning", "namespace=x"], "equal": ["alertname"]},
            {"source_matchers": ["alertname=ZoneDown", "namespace=x"],
             "target_matchers": ["alertname=NodeDown", "namespace=x"], "equal": ["zone"]},
        ],
    }
    assert "circular-inhibition" not in codes(cfg)


def test_circular_inhibition_target_equal_cannot_agree():
    # the warning alert A mutes is in cluster b, its critical source in cluster a, so B
    # (equal: [cluster]) never mutes that source while the warning fires
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [
            {"source_matchers": ["severity=critical", "cluster=a"],
             "target_matchers": ["severity=warning", "cluster=b"], "equal": ["alertname"]},
            {"source_matchers": ["severity=warning"],
             "target_matchers": ["severity=critical"], "equal": ["cluster"]},
        ],
    }
    assert "circular-inhibition" not in codes(cfg)


@pytest.mark.parametrize("warning_matchers", [
    ["severity=warning"],  # alertname free on one side
    ["severity=warning", "alertname=NodeDown"],  # same value on both sides
])
def test_circular_inhibition_equal_can_agree(warning_matchers):
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [
            {"source_matchers": ["severity=critical", "alertname=NodeDown"],
             "target_matchers": warning_matchers, "equal": ["alertname"]},
            {"source_matchers": warning_matchers,
             "target_matchers": ["severity=critical"], "equal": ["alertname"]},
        ],
    }
    assert "circular-inhibition" in codes(cfg)


# ── bad-regex: Go RE2 semantics, inhibit rules ───────────────────────

def _route_regex(pattern):
    return {
        "route": {"receiver": "a", "routes": [
            {"matchers": [f'svc=~"{pattern}"'], "receiver": "a"},
        ]},
        "receivers": [{"name": "a"}],
    }


@pytest.mark.parametrize("pattern", ["(?!test).*", "(?<=a)b", r"(a)\\1", "a++", "(?>a)"])
def test_bad_regex_rejected_by_re2(pattern):
    assert "bad-regex" in codes(_route_regex(pattern))


@pytest.mark.parametrize("pattern", [r"\\pL+", r"\\p{Greek}", "(?<name>x)", r"foo\\z",
                                     "foo(?i)bar", "[[:alpha:]]+", r"\\x{1F600}"])
def test_go_only_regex_syntax_ok(pattern):
    assert "bad-regex" not in codes(_route_regex(pattern))


def test_bad_regex_in_inhibit_rule():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"source_match_re": {"svc": "auth(["},
                           "target_matchers": ['svc=~"(?=x)"'],
                           "equal": ["svc"]}],
    }
    found = [f for f in lint(cfg) if f.code == "bad-regex"]
    assert len(found) == 2
    assert all(f.where == "inhibit_rules[0]" for f in found)


# ── Timing: inheritance and Alertmanager defaults ────────────────────

def test_parse_duration():
    from amlint.linter import _parse_duration
    assert _parse_duration("1h30m") == 5400
    assert _parse_duration("1y") == 365 * 86400
    assert _parse_duration("0") == 0
    assert _parse_duration("500ms") == 0.5
    assert _parse_duration("30sec") is None
    assert _parse_duration("5 m") is None
    assert _parse_duration(None) is None


def test_repeat_before_group_inherited_from_parent():
    cfg = {
        "route": {"receiver": "a", "group_interval": "10m", "routes": [
            {"matchers": ["team=x"], "receiver": "a", "repeat_interval": "1m"},
        ]},
        "receivers": [{"name": "a"}],
    }
    found = [f for f in lint(cfg) if f.code == "repeat-before-group"]
    assert [f.where for f in found] == ["route.routes[0]"]
    assert "inherited from route" in found[0].msg


def test_repeat_before_group_against_default():
    cfg = {
        "route": {"receiver": "a", "repeat_interval": "1m"},  # default group_interval is 5m
        "receivers": [{"name": "a"}],
    }
    found = [f for f in lint(cfg) if f.code == "repeat-before-group"]
    assert len(found) == 1
    assert "Alertmanager default" in found[0].msg


def test_timing_reported_once_not_on_inheriting_children():
    cfg = {
        "route": {"receiver": "a", "group_interval": "1h", "repeat_interval": "5m", "routes": [
            {"matchers": ["team=x"], "receiver": "a"},
            {"matchers": ["team=y"], "receiver": "a"},
        ]},
        "receivers": [{"name": "a"}],
    }
    found = [f for f in lint(cfg) if f.code == "repeat-before-group"]
    assert [f.where for f in found] == ["route"]


def test_child_override_fixes_inherited_timing():
    cfg = {
        "route": {"receiver": "a", "group_interval": "1h", "repeat_interval": "5m", "routes": [
            {"matchers": ["team=x"], "receiver": "a", "repeat_interval": "4h"},
        ]},
        "receivers": [{"name": "a"}],
    }
    found = [f for f in lint(cfg) if f.code == "repeat-before-group"]
    assert [f.where for f in found] == ["route"]


def test_wait_exceeds_interval_inherited():
    cfg = {
        "route": {"receiver": "a", "group_wait": "4m", "routes": [  # fine vs default 5m
            {"matchers": ["team=x"], "receiver": "a", "group_interval": "2m"},
        ]},
        "receivers": [{"name": "a"}],
    }
    found = [f for f in lint(cfg) if f.code == "wait-exceeds-interval"]
    assert [f.where for f in found] == ["route.routes[0]"]


# ── Receivers and time intervals ─────────────────────────────────────

@pytest.mark.parametrize("key", ["pushover_configs", "webex_configs", "jira_configs",
                                 "rocketchat_configs", "msteamsv2_configs",
                                 "incidentio_configs"])
def test_empty_receiver_newer_integrations_ok(key):
    cfg = {
        "route": {"receiver": "a", "routes": [{"matchers": ["x=y"], "receiver": "b"}]},
        "receivers": [{"name": "a"}, {"name": "b", key: [{"some": "setting"}]}],
    }
    assert not any(f.code == "empty-receiver" and "'b'" in f.msg for f in lint(cfg))


def test_empty_receiver_empty_list():
    cfg = {
        "route": {"receiver": "a", "routes": [{"matchers": ["x=y"], "receiver": "b"}]},
        "receivers": [{"name": "a"}, {"name": "b", "webhook_configs": []}],
    }
    assert any(f.code == "empty-receiver" and "'b'" in f.msg for f in lint(cfg))


def test_slack_global_api_url_file_ok():
    cfg = {
        "global": {"slack_api_url_file": "/etc/am/slack"},
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "slack_configs": [{"channel": "#alerts"}]}],
    }
    assert "slack-no-api-url" not in codes(cfg)


def test_legacy_top_level_mute_time_intervals_ok():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"matchers": ["x=y"], "receiver": "a", "mute_time_intervals": ["night"]},
        ]},
        "receivers": [{"name": "a"}],
        "mute_time_intervals": [{"name": "night"}],
    }
    assert "undefined-time-interval" not in codes(cfg)


# ── route-match-collision ────────────────────────────────────────────

def test_route_match_collision_match_vs_matchers():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"match": {"severity": "critical"}, "receiver": "a"},
            {"matchers": ['severity="critical"'], "receiver": "b"},
        ]},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "route-match-collision" in codes(cfg)


def test_route_match_no_collision_after_continue():
    cfg = {
        "route": {"receiver": "a", "routes": [
            {"matchers": ["severity=critical"], "receiver": "a", "continue": True},
            {"matchers": ["severity=critical"], "receiver": "b"},
        ]},
        "receivers": [{"name": "a"}, {"name": "b"}],
    }
    assert "route-match-collision" not in codes(cfg)


# ── invalid-root-route ───────────────────────────────────────────────

@pytest.mark.parametrize("route", [
    {"group_by": ["alertname"]},
    {"receiver": "a", "matchers": ["team=infra"]},
    {"receiver": "a", "match": {"team": "infra"}},
    {"receiver": "a", "continue": True},
    {"receiver": "a", "mute_time_intervals": ["night"]},
])
def test_invalid_root_route(route):
    cfg = {"route": route, "receivers": [{"name": "a"}], "time_intervals": [{"name": "night"}]}
    assert "invalid-root-route" in codes(cfg)


def test_valid_root_route_ok():
    cfg = {"route": {"receiver": "a", "group_by": ["alertname"]}, "receivers": [{"name": "a"}]}
    assert "invalid-root-route" not in codes(cfg)


def test_invalid_root_route_has_explain():
    from amlint.explains import EXPLAINS
    assert "invalid-root-route" in EXPLAINS


# ── shadowed-route ───────────────────────────────────────────────────

def _sibling_routes(*routes):
    return {
        "route": {"receiver": "a", "routes": [dict(r, receiver="a") for r in routes]},
        "receivers": [{"name": "a"}],
    }


def _where(cfg, code):
    return [f.where for f in lint(cfg) if f.code == code]


def test_shadowed_route_strict_subset():
    cfg = _sibling_routes(
        {"matchers": ['severity="critical"']},
        {"matchers": ['severity="critical"', 'team="infra"']},
    )
    found = [f for f in lint(cfg) if f.code == "shadowed-route"]
    assert [f.where for f in found] == ["route.routes[1]"]
    assert found[0].level == WARN
    assert "route.routes[0]" in found[0].msg


def test_shadowed_route_match_vs_matchers_spelling():
    cfg = _sibling_routes(
        {"match": {"severity": "critical"}},
        {"matchers": ['{severity="critical", team="infra"}']},
    )
    assert _where(cfg, "shadowed-route") == ["route.routes[1]"]


def test_shadowed_route_nested_siblings():
    cfg = _sibling_routes({"matchers": ["env=prod"], "routes": [
        {"matchers": ["team=infra"], "receiver": "a"},
        {"matchers": ["team=infra", "severity=critical"], "receiver": "a"},
    ]})
    assert _where(cfg, "shadowed-route") == ["route.routes[0].routes[1]"]


@pytest.mark.parametrize("first, second", [
    ({"matchers": ['severity="critical"'], "continue": True},
     {"matchers": ['severity="critical"', 'team="infra"']}),
    ({"matchers": ['severity="critical"', 'team="infra"']},
     {"matchers": ['severity="critical"']}),
    ({"matchers": ['severity="critical"']},
     {"matchers": ['severity="warning"', 'team="infra"']}),
    ({"matchers": ['severity=~"critical"']},
     {"matchers": ['severity="critical"', 'team="infra"']}),
    ({"matchers": ['severity="critical"']},
     {"matchers": ['severity=~"critical"', 'team="infra"']}),
], ids=["continue-true", "earlier-more-specific", "different-value",
        "regex-before-equality", "equality-before-regex"])
def test_shadowed_route_not_reported(first, second):
    assert "shadowed-route" not in codes(_sibling_routes(first, second))


def test_shadowed_route_leaves_identical_to_route_match_collision():
    cfg = _sibling_routes(
        {"matchers": ['severity="critical"']},
        {"match": {"severity": "critical"}},
    )
    found = codes(cfg)
    assert "route-match-collision" in found
    assert "shadowed-route" not in found


def test_shadowed_route_leaves_catch_all_to_unreachable_route():
    cfg = _sibling_routes(
        {"matchers": ['team=~".*"']},
        {"matchers": ['team=~".*"', 'severity="critical"']},
    )
    found = codes(cfg)
    assert "unreachable-route" in found
    assert "shadowed-route" not in found


def test_dead_routes_reported_once():
    cfg = _sibling_routes(
        {"matchers": ['severity="critical"']},
        {"matchers": ['severity="critical"', 'team="infra"']},   # shadowed by [0]
        {"matchers": ['team="infra"', 'severity="critical"']},   # identical to [1]
    )
    found = sorted((f.code, f.where) for f in lint(cfg)
                   if f.code in ("shadowed-route", "route-match-collision"))
    assert found == [
        ("route-match-collision", "route.routes[2]"),
        ("shadowed-route", "route.routes[1]"),
    ]


@pytest.mark.parametrize("first, second", [
    ({"matchers": ['severity="critical"']},
     {"matchers": ['severity="critical"', 'team="infra"']}),
    ({"match": {"severity": "critical"}},
     {"matchers": ['{severity="critical", team="infra"}']}),
])
def test_shadowed_route_agrees_with_routing(first, second):
    from amlint.routing import Router
    cfg = _sibling_routes(first, second)
    assert _where(cfg, "shadowed-route") == ["route.routes[1]"]
    alert = {"severity": "critical", "team": "infra"}
    assert [m.path[-1] for m in Router(cfg["route"]).match(alert)] == ["route.routes[0]"]


def test_shadowed_route_has_explain():
    from amlint.explains import EXPLAINS
    assert EXPLAINS["shadowed-route"]["level"] == "warn"


# ── unreachable-route: =~".*" counts as catch-all ────────────────────

def test_unreachable_route_match_all_regex():
    cfg = _sibling_routes(
        {"match_re": {"team": ".*"}},
        {"matchers": ['severity="critical"']},
    )
    found = [f for f in lint(cfg) if f.code == "unreachable-route"]
    assert [f.where for f in found] == ["route.routes[0]"]
    assert '=~".*"' in found[0].msg


def test_unreachable_route_non_empty_regex_is_not_catch_all():
    cfg = _sibling_routes(
        {"matchers": ['team=~".+"']},
        {"matchers": ['severity="critical"']},
    )
    assert "unreachable-route" not in codes(cfg)


def test_route_match_collision_leaves_match_all_regex_to_unreachable_route():
    cfg = _sibling_routes(
        {"match_re": {"team": ".*"}},
        {"matchers": ['team=~".*"']},
    )
    found = codes(cfg)
    assert "unreachable-route" in found
    assert "route-match-collision" not in found


# ── Robustness and CLI ───────────────────────────────────────────────

def test_lint_tolerates_malformed_entries():
    cfg = {
        "route": {"receiver": "a", "routes": ["oops", {"matchers": "x=y", "receiver": "a"}]},
        "receivers": ["oops", {"name": "a", "webhook_configs": "oops"}],
        "inhibit_rules": ["oops"],
        "templates": [42],
    }
    lint(cfg, basedir=".")  # must not raise


def test_cli_non_mapping_yaml(tmp_path):
    cfg = tmp_path / "am.yml"
    cfg.write_text("- a\n- b\n")
    with pytest.raises(SystemExit) as exc:
        main(["check", str(cfg)])
    assert exc.value.code == 2


RECURSIVE_ROUTE = "route: &r\n  receiver: a\n  routes: [*r]\nreceivers: [{name: a}]\n"


@pytest.mark.parametrize("argv", [["check"], ["tree"], ["route", "{cfg}", "a=b"],
                                  ["test", "{cfg}", "{tests}"], ["diff", "{cfg}", "{cfg}"]])
def test_cli_recursive_route_exits_2(tmp_path, capsys, argv):
    """A route aliased inside itself makes yaml.safe_load build a recursive dict, which
    every route walk would follow until RecursionError."""
    cfg = tmp_path / "am.yml"
    cfg.write_text(RECURSIVE_ROUTE)
    tests = tmp_path / "tests.yml"
    tests.write_text("- labels: {a: b}\n  receiver: a\n")
    argv = [a.format(cfg=cfg, tests=tests) for a in argv]
    if len(argv) == 1:
        argv.append(str(cfg))
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Route contains itself" in err
    assert "route.routes[0] is a YAML alias of route, which contains it" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("text, cycle", [
    (RECURSIVE_ROUTE, ("route.routes[0]", "route")),
    # an anchor deeper in the tree, aliased two levels further down
    ("route:\n  receiver: a\n  routes:\n    - matchers: [t=x]\n      routes:\n"
     "        - &x\n          matchers: [s=y]\n          routes:\n"
     "            - receiver: a\n              routes: [*x]\n",
     ("route.routes[0].routes[0].routes[0].routes[0]", "route.routes[0].routes[0]")),
    # the routes list aliased inside one of its own entries
    ("route:\n  receiver: a\n  routes: &l\n    - matchers: [x=1]\n      routes: *l\n",
     ("route.routes[0].routes[0]", "route.routes[0]")),
])
def test_route_cycle_names_the_alias_and_its_ancestor(text, cycle):
    assert route_cycle(yaml.safe_load(text)["route"]) == cycle


SHARED_ROUTES = """\
route:
  receiver: a
  routes:
    - matchers: [team="db"]
      routes: &common
        - matchers: [severity="critical"]
          receiver: pager
        - receiver: a
    - matchers: [team="web"]
      routes: *common
receivers:
  - name: a
    webhook_configs: [{url: 'http://a'}]
  - name: pager
    webhook_configs: [{url: 'http://p'}]
"""


def test_cli_shared_route_alias_still_works(tmp_path, capsys):
    """The same routes reused under two parents is a tree, not a cycle."""
    cfg = tmp_path / "am.yml"
    cfg.write_text(SHARED_ROUTES)
    assert route_cycle(yaml.safe_load(SHARED_ROUTES)["route"]) is None
    assert main(["check", str(cfg), "--format", "json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert [f["code"] for f in found] == ["global-resolve-timeout-missing"]
    assert main(["tree", str(cfg)]) == 0
    assert capsys.readouterr().out.count("pager") == 2
    assert main(["route", str(cfg), "team=web", "severity=critical", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["receivers"] == ["pager"]
    tests = tmp_path / "tests.yml"
    tests.write_text("- labels: {team: db, severity: critical}\n  receiver: pager\n"
                     "- labels: {team: web}\n  receiver: a\n")
    assert main(["test", str(cfg), str(tests)]) == 0
    assert "2 passed" in capsys.readouterr().out


def test_route_cycle_walks_shared_routes_once():
    """Routes shared at every level double the paths per level; each is checked once."""
    node = {"receiver": "a"}
    for _ in range(64):
        node = {"routes": [node, node]}
    assert route_cycle(node) is None


def test_cli_unknown_ignore_code_warns(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n"
                   "    webhook_configs: [{url: 'http://fake'}]\n")
    main(["check", str(cfg), "--ignore", "unused-reciever"])
    err = capsys.readouterr().err
    assert "unknown check code 'unused-reciever'" in err
    assert "unused-receiver" in err


def test_cli_diff_runs_template_check(tmp_path, capsys):
    base = "route:\n  receiver: a\nreceivers:\n  - name: a\n"
    old = tmp_path / "old.yml"
    new = tmp_path / "new.yml"
    old.write_text(base)
    new.write_text(base + "templates: ['missing.tmpl']\n")
    assert main(["diff", str(old), str(new), "--format", "json"]) == 1
    added = json.loads(capsys.readouterr().out)["added"]
    assert [f["code"] for f in added] == ["template-file-missing"]


def test_victorops_api_key_file_ok():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "victorops_configs": [
            {"api_key_file": "/etc/am/victorops", "routing_key": "team"},
        ]}],
    }
    assert "victorops-no-api-key" not in codes(cfg)


def test_receiver_field_checks_tolerate_malformed_configs():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a", "telegram_configs": "oops", "discord_configs": ["oops"],
                       "victorops_configs": [None], "wechat_configs": 1, "sns_configs": ["x"]}],
    }
    lint(cfg)  # must not raise


def test_cli_test_non_mapping_tests_file(tmp_path, capsys):
    cfg_path = _write_test_config(tmp_path)
    tests_path = tmp_path / "tests.yml"
    tests_path.write_text("just a string\n")
    assert main(["test", str(cfg_path), str(tests_path)]) == 2
    err = capsys.readouterr().err
    assert "Invalid test file" in err
    assert "Not an Alertmanager config" not in err


def test_cli_tree_unknown_ignore_code_warns(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n")
    assert main(["tree", str(cfg), "--ignore", "unreachable-rout"]) == 0
    assert "did you mean 'unreachable-route'" in capsys.readouterr().err


# ── routing: same matcher semantics as the linter ────────────────────

def _routed(route_matchers, labels):
    route = {"receiver": "default", "routes": [dict(route_matchers, receiver="hit")]}
    return _receivers(route, labels)


def test_routing_regex_is_anchored_like_linter_matchers():
    # Alertmanager anchors regexes: "crit" must not match "critical"
    assert _routed({"matchers": ['severity=~"crit"']}, {"severity": "critical"}) == ["default"]
    assert _routed({"matchers": ['severity=~"crit"']}, {"severity": "crit"}) == ["hit"]
    assert _routed({"match_re": {"service": "db"}}, {"service": "db-payments"}) == ["default"]
    assert _routed({"matchers": ['env!~"prod"']}, {"env": "prod-eu"}) == ["hit"]


def test_routing_multi_matcher_string():
    m = {"matchers": ['{team="infra", severity="critical"}']}
    assert _routed(m, {"team": "infra"}) == ["default"]
    assert _routed(m, {"team": "infra", "severity": "critical"}) == ["hit"]


def test_routing_quoted_value_escapes():
    m = {"matchers": [r'msg="say \"hi\""']}
    assert _routed(m, {"msg": 'say "hi"'}) == ["hit"]


def test_routing_missing_label_is_empty_string():
    assert _routed({"match": {"env": ""}}, {}) == ["hit"]
    assert _routed({"matchers": ['env!=""']}, {}) == ["default"]


def test_routing_non_string_values():
    assert _routed({"match": {"code": 500}}, {"code": "500"}) == ["hit"]
    assert _routed({"match": {"code": "500"}}, {"code": 500}) == ["hit"]


def test_routing_unparseable_matcher_is_rejected_not_catch_all():
    from amlint.routing import Router, RoutingError
    route = {"receiver": "default", "routes": [{"matchers": ["this is not a matcher"]}]}
    with pytest.raises(RoutingError, match="cannot parse matcher"):
        Router(route)


def test_routing_skips_malformed_routes():
    route = {"receiver": "default", "routes": ["oops", {"match": {"a": "b"}, "receiver": "x"}]}
    assert _receivers(route, {"a": "b"}) == ["x"]


# ── Source locations: logical path -> line:column ───────────────────

LOCATED = """\
global:
  resolve_timeout: 5m
route:
  receiver: default
  routes:
    - matchers: [team="infra"]
      receiver: infra
      routes:
        - matchers: [severity="critical"]
          receiver: ghost
    - receiver: default
receivers:
  - name: default
    webhook_configs:
      - url: http://example
  - name: infra
    webhook_configs:
      - {}
inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers: [severity="warning"]
"""


def test_source_map_routes():
    from amlint.source import SourceMap
    smap = SourceMap(LOCATED)
    assert smap.locate("route") == (3, 1)
    assert smap.locate("route.routes[0]") == (6, 7)
    assert smap.locate("route.routes[1]") == (11, 7)


def test_source_map_nested_routes():
    from amlint.source import SourceMap
    assert SourceMap(LOCATED).locate("route.routes[0].routes[0]") == (9, 11)


def test_source_map_receivers_by_name_and_index():
    from amlint.source import SourceMap
    smap = SourceMap(LOCATED)
    assert smap.locate("receivers") == (12, 1)
    assert smap.locate("receivers[infra]") == (16, 5)
    assert smap.locate("receivers[1]") == (16, 5)
    assert smap.locate("receivers[infra].webhook_configs[0]") == (18, 9)


def test_source_map_inhibit_rules():
    from amlint.source import SourceMap
    assert SourceMap(LOCATED).locate("inhibit_rules[0]") == (20, 5)


def test_source_map_falls_back_to_nearest_parent():
    from amlint.source import SourceMap
    smap = SourceMap(LOCATED)
    assert smap.locate("route.routes[0].routes[7]") == (8, 7)   # the routes: key
    assert smap.locate("receivers[nope].email_configs[0]") == (12, 1)
    assert smap.locate("templates") == (1, 1)                   # nothing to anchor to


def test_source_map_merge_keys_and_empty_doc():
    from amlint.source import SourceMap
    smap = SourceMap("defaults: &d\n  receiver: a\nroute:\n  <<: *d\n  routes:\n    - {}\n")
    assert smap.locate("route.receiver") == (2, 3)  # defined at the anchor
    assert smap.locate("route.routes[0]") == (6, 7)
    assert SourceMap("").locate("route") == (1, 1)


def test_cli_json_has_line_and_column(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text(LOCATED)
    main(["check", "--format", "json", str(cfg)])
    by_code = {f["code"]: f for f in json.loads(capsys.readouterr().out)}
    def pos(code):
        return by_code[code]["line"], by_code[code]["column"]
    assert pos("undefined-receiver") == (9, 11)
    assert pos("webhook-no-url") == (18, 9)
    assert pos("inhibit-no-equal") == (20, 5)


def test_cli_receiver_findings_point_at_the_receiver(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n"
                   "    webhook_configs: [{url: 'http://x'}]\n  - name: spare\n  - name: a\n")
    main(["check", "--format", "json", str(cfg)])
    found = {(f["code"], f["where"], f["line"]) for f in json.loads(capsys.readouterr().out)}
    assert ("empty-receiver", "receivers[spare]", 6) in found
    assert ("unused-receiver", "receivers[spare]", 6) in found
    assert ("duplicate-receiver", "receivers[2]", 7) in found  # the second definition


def test_cli_sarif_region_has_real_line(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text(LOCATED)
    main(["check", "--format", "sarif", str(cfg)])
    results = json.loads(capsys.readouterr().out)["runs"][0]["results"]
    hit = next(r for r in results if r["ruleId"] == "undefined-receiver")
    region = hit["locations"][0]["physicalLocation"]["region"]
    assert region == {"startLine": 9, "startColumn": 11}


def test_cli_text_shows_file_line_col(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text(LOCATED)
    main(["check", str(cfg)])
    assert f"{cfg}:9:11" in capsys.readouterr().out


def test_cli_stdin_has_locations(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(LOCATED))
    assert main(["check", "-", "--format", "json"]) == 1
    out = json.loads(capsys.readouterr().out)
    hit = next(f for f in out if f["code"] == "undefined-receiver")
    assert (hit["file"], hit["line"], hit["column"]) == ("-", 9, 11)


# ── Inline suppressions: # amlint: disable=... ──────────────────────

SUPPRESS_BASE = """\
route:
  receiver: a
  routes:
    - receiver: a{same_line}
    - matchers: [team="x"]
      receiver: a
receivers:
  - name: a
    webhook_configs: [{{url: 'http://x'}}]
"""


def _check_json(tmp_path, capsys, text, *extra):
    cfg = tmp_path / "am.yml"
    cfg.write_text(text)
    main(["check", "--format", "json", str(cfg), *extra])
    captured = capsys.readouterr()
    return {f["code"] for f in json.loads(captured.out)}, captured.err


def test_suppression_on_same_line(tmp_path, capsys):
    text = SUPPRESS_BASE.format(same_line="")
    found, _ = _check_json(tmp_path, capsys, text)
    assert "unreachable-route" in found
    text = SUPPRESS_BASE.format(same_line="  # amlint: disable=unreachable-route")
    found, err = _check_json(tmp_path, capsys, text)
    assert "unreachable-route" not in found
    assert err == ""


def test_suppression_on_line_above(tmp_path, capsys):
    text = SUPPRESS_BASE.format(same_line="").replace(
        "    - receiver: a\n",
        "    # amlint: disable=unreachable-route, useless-continue\n"
        "    # (catch-all on purpose)\n"
        "    - receiver: a\n",
    )
    found, err = _check_json(tmp_path, capsys, text)
    assert "unreachable-route" not in found
    assert err == ""


def test_trailing_suppression_does_not_cover_next_line(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n"
                   "    webhook_configs: [{url: 'http://x'}]\n"
                   "  - name: b    # amlint: disable=empty-receiver\n"
                   "  - name: c\n")
    main(["check", "--format", "json", "--only", "empty-receiver", str(cfg)])
    assert [f["where"] for f in json.loads(capsys.readouterr().out)] == ["receivers[c]"]


def test_suppression_file_wide(tmp_path, capsys):
    text = ("# Team alerting config\n"
            "# amlint: disable-file=global-resolve-timeout-missing,unreachable-route\n"
            "---\n" + SUPPRESS_BASE.format(same_line=""))
    found, err = _check_json(tmp_path, capsys, text)
    assert "global-resolve-timeout-missing" not in found
    assert "unreachable-route" not in found
    assert err == ""


def test_disable_file_below_content_is_ignored(tmp_path, capsys):
    text = SUPPRESS_BASE.format(same_line="") + "# amlint: disable-file=unreachable-route\n"
    found, err = _check_json(tmp_path, capsys, text)
    assert "unreachable-route" in found
    assert "only works in the comment block at the top" in err


def test_suppression_unknown_code_warns(tmp_path, capsys):
    text = SUPPRESS_BASE.format(same_line="  # amlint: disable=unreachable-rout")
    found, err = _check_json(tmp_path, capsys, text)
    assert "unreachable-route" in found
    assert "unknown check code 'unreachable-rout'" in err
    assert "am.yml:4" in err
    assert "did you mean 'unreachable-route'" in err


def test_suppression_malformed_comment_warns(tmp_path, capsys):
    text = SUPPRESS_BASE.format(same_line="  # amlint: disabel=unreachable-route")
    found, err = _check_json(tmp_path, capsys, text)
    assert "unreachable-route" in found
    assert "unrecognized '# amlint:' comment" in err


def test_suppression_inside_string_is_not_a_comment(tmp_path, capsys):
    # the would-be directive sits on the finding's own line, but inside a quoted string
    text = SUPPRESS_BASE.format(same_line="").replace(
        "    - receiver: a\n",
        "    - {receiver: a, group_by: ['x # amlint: disable=unreachable-route']}\n", 1,
    )
    found, err = _check_json(tmp_path, capsys, text)
    assert "unreachable-route" in found
    assert err == ""


def test_suppression_via_stdin(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(
        SUPPRESS_BASE.format(same_line="  # amlint: disable=unreachable-route")
    ))
    main(["check", "-", "--format", "json"])
    assert "unreachable-route" not in {f["code"] for f in json.loads(capsys.readouterr().out)}


def test_suppression_applies_to_diff(tmp_path, capsys):
    old = tmp_path / "old.yml"
    new = tmp_path / "new.yml"
    old.write_text(SUPPRESS_BASE.format(same_line="").replace("    - receiver: a\n", "", 1))
    new.write_text(SUPPRESS_BASE.format(same_line="  # amlint: disable=unreachable-route"))
    assert main(["diff", str(old), str(new), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["added"] == []


# ── --format github ─────────────────────────────────────────────────

def test_cli_github_format(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    cfg = tmp_path / "am.yml"
    cfg.write_text(LOCATED)
    assert main(["check", "--format", "github", str(cfg)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert (f"::error file={cfg},line=9,col=11,title=amlint undefined-receiver::"
            f"Route references receiver 'ghost'") in lines[0]
    assert lines[0].endswith("(route.routes[0].routes[0])")
    assert any(ln.startswith(f"::warning file={cfg},line=20,col=5,"
                             f"title=amlint inhibit-no-equal::") for ln in lines)
    assert all(ln.startswith(("::error ", "::warning ", "::notice ")) for ln in lines)


def test_cli_github_format_notice_and_escaping(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    cfg = tmp_path / "odd,name.yml"
    cfg.write_text("route:\n  receiver: a\nreceivers:\n  - name: a\n"
                   "    webhook_configs: [{url: 'http://x'}]\n  - name: '100%'\n")
    main(["check", "--format", "github", "--only", "unused-receiver", str(cfg)])
    out = capsys.readouterr().out
    assert out.startswith("::notice file=")
    assert "odd%2Cname.yml,line=6,col=5,title=amlint unused-receiver::" in out
    assert "Receiver '100%25'" in out


def test_cli_github_format_relative_to_workspace(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    (tmp_path / "deploy").mkdir()
    cfg = tmp_path / "deploy" / "am.yml"
    cfg.write_text(LOCATED)
    main(["check", "--format", "github", str(cfg)])
    assert "::error file=deploy/am.yml,line=9,col=11," in capsys.readouterr().out
    # a step with `working-directory: deploy` passes a path relative to that directory;
    # the runner reads relative file= paths from the repo root, so it must be rewritten
    monkeypatch.chdir(tmp_path / "deploy")
    main(["check", "--format", "github", "am.yml"])
    assert "::error file=deploy/am.yml,line=9,col=11," in capsys.readouterr().out


def test_cli_github_format_stdin_has_no_file(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(LOCATED))
    main(["check", "-", "--format", "github"])
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("::error title=amlint undefined-receiver::")


def test_unused_receivers_with_mixed_name_types():
    cfg = {"route": {"receiver": "a"},
           "receivers": [{"name": "a"}, {"name": 123}, {"name": "b"}]}
    found = [f.where for f in lint(cfg) if f.code == "unused-receiver"]
    assert found == ["receivers[123]", "receivers[b]"]


def test_cli_github_format_cannot_inject_commands(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text('route:\n  receiver: a\nreceivers:\n  - name: a\n'
                   '    webhook_configs: [{url: "http://x"}]\n'
                   '  - name: "x\\n::error::injected\\r\\n::add-mask::y"\n')
    main(["check", "--format", "github", "--only", "unused-receiver", str(cfg)])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "x%0A::error::injected%0D%0A::add-mask::y" in lines[0]


def test_source_map_receiver_names_as_loaded():
    from amlint.source import SourceMap
    smap = SourceMap("receivers:\n  - name: true\n  - name: 0x1F\n  - name: '007'\n")
    assert smap.locate("receivers[True]") == (2, 5)
    assert smap.locate("receivers[31]") == (3, 5)
    assert smap.locate("receivers[007]") == (4, 5)


# ── routing diff (amlint diff) ───────────────────────────────────────

def _rdiff(old_route, new_route, **kw):
    from amlint.routing import Router, diff_routing
    return diff_routing(Router(old_route), Router(new_route), **kw)


def _changes(rdiff):
    return {
        tuple(sorted(c.labels.items())): [(d["subject"], d["setting"], d["old"], d["new"])
                                          for d in c.differences()]
        for c in rdiff.changes
    }


_DB_ROUTE = {"receiver": "default", "group_by": ["alertname"], "routes": [
    {"matchers": ['team="db"'], "receiver": "slack-db", "routes": [
        {"matchers": ['severity="critical"'], "receiver": "pagerduty-db"}]},
    {"matchers": ['team="infra"'], "receiver": "infra-slack"},
]}


def test_routing_diff_identical_configs_have_no_changes():
    rdiff = _rdiff(_DB_ROUTE, _DB_ROUTE)
    assert rdiff.changes == []
    assert rdiff.checked > 0 and not rdiff.truncated


def test_routing_diff_receiver_change_from_removed_child():
    new = {**_DB_ROUTE, "routes": [{"matchers": ['team="db"'], "receiver": "slack-db"},
                                   _DB_ROUTE["routes"][1]]}
    assert _changes(_rdiff(_DB_ROUTE, new)) == {
        (("severity", "critical"), ("team", "db")):
            [("receivers", "receivers", "pagerduty-db", "slack-db")],
    }


def test_routing_diff_reports_timing_and_group_by_changes():
    new = {**_DB_ROUTE, "routes": [
        _DB_ROUTE["routes"][0],
        {"matchers": ['team="infra"'], "receiver": "infra-slack", "group_wait": "10s",
         "group_by": ["alertname", "cluster"]},
    ]}
    assert _changes(_rdiff(_DB_ROUTE, new)) == {
        (("team", "infra"),): [
            ("infra-slack", "group_by", "alertname", "alertname, cluster"),
            ("infra-slack", "group_wait", "30s", "10s"),
        ],
    }


def test_routing_diff_folds_same_change_into_smallest_label_set():
    new = {**_DB_ROUTE, "group_interval": "1m"}  # inherited by every route
    rdiff = _rdiff(_DB_ROUTE, new)
    by_receiver = {c.differences()[0]["subject"]: c for c in rdiff.changes}
    assert set(by_receiver) == {"default", "slack-db", "pagerduty-db", "infra-slack"}
    assert by_receiver["default"].labels == {}
    assert by_receiver["slack-db"].labels == {"team": "db"}
    assert by_receiver["default"].similar > 0  # e.g. severity=critical alone


def test_routing_diff_sibling_order_matters_only_for_overlapping_labels():
    old = {"receiver": "d", "routes": [
        {"matchers": ['team="db"'], "receiver": "db"},
        {"matchers": ['severity="critical"'], "receiver": "pager"},
    ]}
    new = {"receiver": "d", "routes": list(reversed(old["routes"]))}
    assert _changes(_rdiff(old, new)) == {
        (("severity", "critical"), ("team", "db")): [("receivers", "receivers", "db", "pager")],
    }


def test_routing_diff_regex_and_negative_matchers():
    old = {"receiver": "d", "routes": [{"matchers": ['service=~"api|web"'], "receiver": "fe"}]}
    new = {"receiver": "d", "routes": [
        {"matchers": ['service=~"api"'], "receiver": "fe"},
        {"matchers": ['env!="prod"'], "receiver": "non-prod"},
    ]}
    changes = _changes(_rdiff(old, new))
    # web no longer matches the regex and, having no env label, lands on env!="prod"
    assert changes[(("service", "web"),)] == [("receivers", "receivers", "fe", "non-prod")]
    # an alert without any of the labels: missing env is != "prod"
    assert changes[()] == [("receivers", "receivers", "d", "non-prod")]
    assert (("env", "prod"),) not in changes


def test_routing_diff_continue_fan_out_change():
    old = {"receiver": "d", "routes": [
        {"matchers": ['team="infra"'], "receiver": "slack", "continue": True},
        {"matchers": ['severity="critical"'], "receiver": "pager"},
    ]}
    new = {"receiver": "d", "routes": [{**old["routes"][0], "continue": False}, old["routes"][1]]}
    assert _changes(_rdiff(old, new)) == {
        (("severity", "critical"), ("team", "infra")):
            [("receivers", "receivers", "pager, slack", "slack")],
    }


def test_routing_diff_capped_on_large_configs_still_reaches_parent_routes():
    def tree(rename):
        routes = [
            {"matchers": [f'l{i % 9}="v{i}"', f'k{i % 7}=~"x{i}|y.*"'], "receiver": f"r{i}",
             "routes": [{"matchers": ['sev!="info"'], "receiver": f"r{i}-child"}]}
            for i in range(40)
        ]
        routes[25]["receiver"] = rename
        return {"receiver": "default", "routes": routes}
    rdiff = _rdiff(tree("r25"), tree("renamed"), limit=300)
    assert rdiff.truncated and rdiff.checked == 300
    # only an alert with sev=info stops on routes[25] itself instead of its child
    [change] = rdiff.changes
    assert change.labels["sev"] == "info"
    assert change.differences() == [{"subject": "receivers", "setting": "receivers",
                                     "old": "r25", "new": "renamed"}]


@pytest.mark.parametrize("pattern, expected", [
    ("prod|staging|dev", ["prod", "staging", "dev"]),
    ("db-.*", ["db-", "db-x"]),
    ("(?i)crit.*", ["crit", "critx"]),
    ("[a-z]+-[0-9]{2}", ["a-00"]),
])
def test_routing_regex_examples_fully_match(pattern, expected):
    from amlint.routing import _regex_examples, compile_regex
    assert _regex_examples(compile_regex(pattern)) == expected


def _write_diff_pair(tmp_path, new_route):
    old_path, new_path = tmp_path / "old.yml", tmp_path / "new.yml"
    receivers = [{"name": n} for n in ("default", "slack-db", "pagerduty-db", "infra-slack")]
    old_path.write_text(yaml.dump({"route": _DB_ROUTE, "receivers": receivers}))
    new_path.write_text(yaml.dump({"route": new_route, "receivers": receivers}))
    return str(old_path), str(new_path)


def test_cli_diff_shows_routing_changes_without_failing(tmp_path, capsys):
    # swap two receivers: no finding changes, so routing changes alone don't fail diff
    new = {**_DB_ROUTE, "routes": [
        {**_DB_ROUTE["routes"][0], "routes": [
            {"matchers": ['severity="critical"'], "receiver": "infra-slack"}]},
        {"matchers": ['team="infra"'], "receiver": "pagerduty-db"},
    ]}
    old_path, new_path = _write_diff_pair(tmp_path, new)
    assert main(["diff", old_path, new_path]) == 0
    out = capsys.readouterr().out
    assert "No changes in findings" in out
    assert "Routing" in out
    assert "team=db, severity=critical" in out
    assert "pagerduty-db → infra-slack" in out
    assert "infra-slack → pagerduty-db" in out
    assert "2 routing changes" in out


def test_cli_diff_no_routing_flag(tmp_path, capsys):
    new = {**_DB_ROUTE, "group_wait": "1s"}
    old_path, new_path = _write_diff_pair(tmp_path, new)
    main(["diff", old_path, new_path, "--no-routing"])
    assert "Routing" not in capsys.readouterr().out
    main(["diff", old_path, new_path, "--no-routing", "--format", "json"])
    assert "routing" not in json.loads(capsys.readouterr().out)


def test_cli_diff_routing_json(tmp_path, capsys):
    new = {**_DB_ROUTE, "routes": [_DB_ROUTE["routes"][0],
                                   {**_DB_ROUTE["routes"][1], "repeat_interval": "1h"}]}
    old_path, new_path = _write_diff_pair(tmp_path, new)
    assert main(["diff", old_path, new_path, "--format", "json"]) == 0
    routing = json.loads(capsys.readouterr().out)["routing"]
    assert routing["truncated"] is False and routing["checked"] > 0
    [change] = routing["changes"]
    assert change["labels"] == {"team": "infra"}
    assert change["differences"] == [{"subject": "infra-slack", "setting": "repeat_interval",
                                      "old": "4h", "new": "1h"}]
    assert change["old"][0]["repeat_interval"] == "4h"
    assert change["new"][0]["set_by"]["repeat_interval"] == "route.routes[1]"


def test_cli_diff_routing_skipped_when_config_cannot_be_simulated(tmp_path, capsys):
    old_path = tmp_path / "old.yml"
    old_path.write_text(yaml.dump({"receivers": [{"name": "x"}]}))
    new_path = tmp_path / "new.yml"
    new_path.write_text(yaml.dump({"route": {"receiver": "x"}, "receivers": [{"name": "x"}]}))
    main(["diff", str(old_path), str(new_path)])
    assert "Routing not compared" in capsys.readouterr().out
    main(["diff", str(old_path), str(new_path), "--format", "json"])
    assert "old config" in json.loads(capsys.readouterr().out)["routing"]["skipped"]


# ── amlint diff --format markdown ────────────────────────────────────

def _markdown_diff(*argv):
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["diff", *argv, "--format", "markdown"])
    return rc, out.getvalue()


def test_cli_diff_markdown_changed_routes(tmp_path):
    new = {**_DB_ROUTE, "routes": [
        {**_DB_ROUTE["routes"][0], "routes": [
            {"matchers": ['severity="critical"'], "receiver": "infra-slack"}]},
        {"matchers": ['team="infra"'], "receiver": "infra-slack", "group_wait": "10s",
         "group_by": ["alertname", "cluster"]},
    ]}
    old_path, new_path = _write_diff_pair(tmp_path, new)
    rc, out = _markdown_diff(old_path, new_path, "--only", "undefined-receiver")
    assert rc == 0  # routing changes alone don't fail diff
    assert out.startswith("### amlint diff\n\n**2 routing changes** · no new findings · "
                          "0 fixed · 0 unchanged · ")
    assert "#### Receivers\n\n| labels | before | after |\n| --- | --- | --- |\n" \
           "| `team=db, severity=critical` | `pagerduty-db` | `infra-slack` |\n" in out
    assert "| labels | receiver | setting | before | after |" in out
    assert "| `team=infra` (+1 similar) | `infra-slack` | `group_by` | `alertname` " \
           "| `alertname, cluster` |" in out
    assert "| `team=infra` (+1 similar) | `infra-slack` | `group_wait` | `30s` | `10s` |" in out
    assert "findings" not in out.split("\n", 3)[3]  # no findings sections
    assert out.endswith("|\n") and not out.endswith("\n\n")


def test_cli_diff_markdown_no_changes_is_one_line(tmp_path):
    old_path, _ = _write_diff_pair(tmp_path, _DB_ROUTE)
    rc, out = _markdown_diff(old_path, old_path)
    assert rc == 0
    assert out == ("### amlint diff\n\n"
                   "No routing changes · no new findings · 0 fixed · 5 unchanged · "
                   "6 label sets checked\n")
    assert _markdown_diff(old_path, old_path) == (rc, out)  # deterministic


def test_cli_diff_markdown_caps_tables(tmp_path):
    def config(suffix):
        routes = [{"matchers": [f'team="t{i}"'], "receiver": f"r{i}{suffix}"}
                  for i in range(60)]
        receivers = [{"name": "default"}] + [{"name": r["receiver"]} for r in routes]
        return yaml.dump({"route": {"receiver": "default", "routes": routes},
                          "receivers": receivers})
    old_path, new_path = tmp_path / "old.yml", tmp_path / "new.yml"
    old_path.write_text(config(""))
    new_path.write_text(config("-renamed"))
    _, out = _markdown_diff(str(old_path), str(new_path), "--only", "undefined-receiver")
    assert out.splitlines()[2].startswith("**60 routing changes**")
    rows = [line for line in out.splitlines() if line.startswith("| `team=")]
    assert len(rows) == 50
    assert rows[0] == "| `team=t0` | `r0` | `r0-renamed` |"
    assert out.endswith("-renamed` |\n\n…and 10 more label sets\n")


def test_cli_diff_markdown_findings_with_locations(tmp_path):
    old_path, new_path = tmp_path / "old.yml", tmp_path / "new.yml"
    old_path.write_text("route:\n  receiver: default\n  routes:\n"
                        "    - matchers: ['team=\"db\"']\n      receiver: ghost\n"
                        "receivers:\n  - name: default\n")
    new_path.write_text("route:\n  receiver: default\n  routes:\n"
                        "    - matchers: ['team=\"db\"']\n      receiver: default\n"
                        "    - matchers: ['team=\"infra\"']\n      receiver: phantom\n"
                        "receivers:\n  - name: default\n")
    rc, out = _markdown_diff(str(old_path), str(new_path), "--only", "undefined-receiver")
    assert rc == 1
    assert out.splitlines()[2] == ("**2 routing changes** · **1 new finding** · 1 fixed · "
                                   "0 unchanged · 3 label sets checked")
    new_section = out.split("#### New findings\n\n")[1].split("#### Fixed findings")[0]
    assert new_section.splitlines()[0] == "| level | check | location | message |"
    assert new_section.splitlines()[2] == (
        f"| ✖ error | `undefined-receiver` | `{new_path}:6:7` (`route.routes[1]`) "
        "| Route references receiver 'phantom' which is not defined in receivers. "
        "Alerts matched here will be dropped. |")
    fixed_section = out.split("#### Fixed findings\n\n")[1]
    assert f"| ✖ error | `undefined-receiver` | `{old_path}:4:7` (`route.routes[0]`) " \
           in fixed_section
    assert out.index("#### Receivers") < out.index("#### New findings")


def test_cli_diff_markdown_new_file_against_empty_config(tmp_path):
    empty = tmp_path / "empty.yml"
    empty.write_text("")
    _, new_path = _write_diff_pair(tmp_path, _DB_ROUTE)
    rc, out = _markdown_diff(str(empty), new_path)
    assert "0 fixed" in out and "#### Fixed findings" not in out  # no-root-route isn't "fixed"
    assert "| `team=db, severity=critical` | `(none)` | `pagerduty-db` |" in out
    assert "| `(no labels)` (+1 similar) | `(none)` | `default` |" in out
    assert rc == (1 if "#### New findings" in out else 0)


def test_cli_diff_markdown_routing_skipped_or_disabled(tmp_path):
    _, new_path = _write_diff_pair(tmp_path, _DB_ROUTE)
    old_path = tmp_path / "unparseable.yml"
    old_path.write_text(yaml.dump({"route": {"receiver": "x", "routes": [
        {"matchers": ["oops"], "receiver": "x"}]}, "receivers": [{"name": "x"}]}))
    _, out = _markdown_diff(str(old_path), new_path, "--only", "undefined-receiver")
    assert out.splitlines()[2].startswith("Routing not compared · no new findings")
    assert "Routing not compared: old config: route.routes\\[0\\]: cannot parse matcher" in out
    _, out = _markdown_diff(str(old_path), new_path, "--no-routing", "--only", "undefined-receiver")
    assert out == "### amlint diff\n\nNo new findings · 0 fixed · 0 unchanged\n"


def test_markdown_escapes_config_text():
    from amlint.markdown import code, text
    assert code("a|b") == "`a\\|b`"
    assert code("x`y") == "``x`y``"
    assert code("`edge`") == "`` `edge` ``"
    assert code("line\nbreak") == "`line break`"
    assert code("<b>@team</b>") == "`<b>@team</b>`"  # literal inside a code span
    assert text("<img src=x> *bold* [l](u) a|b") == \
        "\\<img src=x\\> \\*bold\\* \\[l\\](u) a\\|b"
    assert text("ping @org/team & co") == "ping @&#8203;org/team \\& co"


def test_cli_diff_markdown_escapes_receiver_names(tmp_path):
    new = {**_DB_ROUTE, "routes": [_DB_ROUTE["routes"][0],
                                   {"matchers": ['team="infra"'], "receiver": "@ops|`x`"}]}
    old_path, new_path = _write_diff_pair(tmp_path, new)
    _, out = _markdown_diff(old_path, new_path, "--only", "undefined-receiver")
    assert "| `team=infra` (+1 similar) | `infra-slack` | `` @ops\\|`x` `` |" in out
    assert "receiver '@&#8203;ops\\|\\`x\\`' which is not defined" in out


# ── Configs modeled on widely used public setups must lint clean ─────

_REAL_WORLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata", "real-world")


@pytest.mark.parametrize("name", sorted(f for f in os.listdir(_REAL_WORLD) if f.endswith(".yml")))
def test_real_world_configs_lint_clean(name):
    """Anything above INFO on these is noise: it would fail `--strict` out of the box."""
    path = os.path.join(_REAL_WORLD, name)
    with open(path) as f:
        cfg = yaml.safe_load(f)
    noisy = [(x.level, x.code, x.where, x.msg) for x in lint(cfg, basedir=_REAL_WORLD)
             if x.level in (ERROR, WARN)]
    assert noisy == []
    assert main(["check", "--strict", path]) == 0


def test_inhibit_target_only_rule_is_not_flagged():
    # kube-prometheus's "always mute InfoInhibitor" idiom: no source matchers, no equal
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"target_matchers": ["alertname = InfoInhibitor"]}],
    }
    assert "inhibit-no-equal" not in codes(cfg)


def test_inhibit_source_only_rule_without_equal_still_warns():
    # no target matchers = every alert is a target: muting everything is never the idea
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        "inhibit_rules": [{"source_matchers": ['severity="critical"']}],
    }
    assert "inhibit-no-equal" in codes(cfg)


@pytest.mark.parametrize("name", ["null", "Blackhole", "devnull"])
def test_empty_receiver_named_as_blackhole_is_info(name):
    cfg = {
        "route": {"receiver": "a", "routes": [{"matchers": ["alertname=Watchdog"],
                                               "receiver": name}]},
        "receivers": [{"name": "a", "webhook_configs": [{"url": "http://x"}]}, {"name": name}],
    }
    found = [f for f in lint(cfg) if f.code == "empty-receiver"]
    assert [f.level for f in found] == ["info"]


def test_empty_receiver_placeholder_still_warns():
    # kube-prometheus ships "Critical" without integrations: critical alerts would be dropped
    cfg = {
        "route": {"receiver": "a", "routes": [{"matchers": ["severity=critical"],
                                               "receiver": "Critical"}]},
        "receivers": [{"name": "a", "webhook_configs": [{"url": "http://x"}]},
                      {"name": "Critical"}],
    }
    found = [f for f in lint(cfg) if f.code == "empty-receiver"]
    assert [f.level for f in found] == [WARN]


# ── --rules: checks against Prometheus alerting rules ────────────────

RULE_CODES = {"route-unknown-alertname", "inhibit-unknown-alertname",
              "alert-hits-default-receiver"}

NODE_RULES = """\
groups:
  - name: node
    rules:
      - record: node:cpu_busy:ratio
        expr: avg(rate(node_cpu_seconds_total{mode!="idle"}[5m]))
      - alert: HighCPUUsage
        expr: node:cpu_busy:ratio > 0.9
        labels:
          severity: warning
      - alert: DiskFull
        expr: node_filesystem_free_ratio < 0.05
        labels:
          severity: critical
      - alert: QueueBacklog
        expr: queue_depth > 1000
"""


def _rule_alerts(tmp_path, text, name="rules.yml"):
    from amlint.rules import load_rules
    path = tmp_path / name
    path.write_text(text)
    alerts, _ = load_rules([str(path)])
    return alerts


def _rules_lint(tmp_path, route, rules=NODE_RULES, inhibit_rules=None, code=None):
    cfg = {"route": route, "inhibit_rules": inhibit_rules or []}
    return [f for f in lint(cfg, rules=_rule_alerts(tmp_path, rules))
            if f.code in RULE_CODES and (code is None or f.code == code)]


def _routes(*matchers):
    return {"receiver": "default",
            "routes": [{"matchers": [m], "receiver": "team"} for m in matchers]}


def test_rules_loader_keeps_alerting_rules_only(tmp_path):
    alerts = _rule_alerts(tmp_path, NODE_RULES)
    assert [a.name for a in alerts] == ["HighCPUUsage", "DiskFull", "QueueBacklog"]
    assert alerts[0].labels == {"alertname": "HighCPUUsage", "severity": "warning"}
    assert alerts[2].labels == {"alertname": "QueueBacklog"}
    assert alerts[2].location.endswith("rules.yml:14")


def test_rules_recording_rule_name_is_not_an_alert(tmp_path):
    [f] = _rules_lint(tmp_path, _routes('alertname="node:cpu_busy:ratio"'),
                      code="route-unknown-alertname")
    assert "'node:cpu_busy:ratio'" in f.msg


def test_rules_label_values_read_like_prometheus(tmp_path):
    [alert] = _rule_alerts(tmp_path, """\
groups:
  - name: g
    rules:
      - alert: Weird
        expr: up == 0
        labels: {code: 0x1F, enabled: yes, empty: "", gone: null, alertname: Other,
                 owner: "{{ $labels.team }}", nested: [a]}
""")
    # Go reads scalars as their text; an empty value drops the label; alertname is the rule's
    assert alert.labels == {"alertname": "Weird", "code": "0x1F", "enabled": "yes"}
    assert alert.unknown == {"owner", "nested"}


def test_rules_prometheus_operator_shapes(tmp_path):
    alerts = _rule_alerts(tmp_path, """\
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata: {name: k8s}
spec:
  groups:
    - name: k8s
      rules:
        - alert: KubePodCrashLooping
          expr: kube_pod_restarts > 3
          labels: {severity: warning}
---
apiVersion: v1
kind: List
items:
  - apiVersion: monitoring.coreos.com/v1
    kind: PrometheusRule
    spec:
      groups:
        - name: nodes
          rules:
            - alert: KubeNodeNotReady
              expr: kube_node_ready == 0
""", name="prometheusrule.yaml")
    assert [a.name for a in alerts] == ["KubePodCrashLooping", "KubeNodeNotReady"]
    assert alerts[0].labels["severity"] == "warning"
    assert alerts[0].line == 8 and alerts[1].line == 21


def test_route_unknown_alertname_typo_has_hint(tmp_path):
    route = _routes('alertname="HighCpuUsage"', 'alertname="DiskFull"')
    [f] = _rules_lint(tmp_path, route, code="route-unknown-alertname")
    assert f.level == WARN and f.where == "route.routes[0]"
    assert "'HighCpuUsage'" in f.msg and "did you mean 'HighCPUUsage'?" in f.msg
    assert "this branch is dead" in f.msg


def test_route_unknown_alertname_match_spellings(tmp_path):
    route = {"receiver": "default", "routes": [
        {"match": {"alertname": "DiskFul"}, "receiver": "a"},
        {"match_re": {"alertname": "Disk.*"}, "receiver": "a"},
        {"match": {"severity": "critical"}, "receiver": "a"},
    ]}
    found = _rules_lint(tmp_path, route, code="route-unknown-alertname")
    assert [(f.where, "did you mean 'DiskFull'?" in f.msg) for f in found] == [
        ("route.routes[0]", True)]


@pytest.mark.parametrize("matcher, expected", [
    ('alertname=~"HighCPUUsage|DiskFull"', []),
    ('alertname=~"Disk.+|Queue.+"', []),
    ('alertname=~"(?i)highcpu.*"', []),
    ('alertname=~"HighCPUUsage|DiskFul"',
     ["'DiskFul' (in alertname=~\"HighCPUUsage|DiskFul\")", "that part of the regex never"]),
    ('alertname=~"^(?:DiskFul)$"', ["'DiskFul'", "did you mean 'DiskFull'?", "branch is dead"]),
    ('alertname=~"Node.*"', ["matches none of the 3 alert names", "branch is dead"]),
])
def test_route_unknown_alertname_regex(tmp_path, matcher, expected):
    found = _rules_lint(tmp_path, _routes(matcher), code="route-unknown-alertname")
    if not expected:
        assert found == []
    else:
        [f] = found
        for text in expected:
            assert text in f.msg


def test_route_unknown_alertname_negative_matchers(tmp_path):
    route = _routes('alertname!="Watchdgo"', 'alertname!="DiskFull"',
                    'alertname!~"DiskFull|Watchdgo"')
    found = _rules_lint(tmp_path, route, code="route-unknown-alertname")
    assert [f.where for f in found] == ["route.routes[0]", "route.routes[2]"]
    assert "excludes alertname 'Watchdgo'" in found[0].msg
    assert "the matcher excludes nothing" in found[0].msg
    assert "that part of the regex excludes nothing" in found[1].msg


def test_inhibit_unknown_alertname(tmp_path):
    inhibit = [
        {"source_matchers": ['alertname="DiskFul"'],
         "target_matchers": ['alertname="HighCPUUsage"'], "equal": ["instance"]},
        {"source_match": {"alertname": "DiskFull"},
         "target_match_re": {"alertname": "Node.*"}, "equal": ["instance"]},
        {"source_match": {"alertname": "DiskFull"},
         "target_match": {"severity": "warning"}, "equal": ["instance"]},
    ]
    found = _rules_lint(tmp_path, {"receiver": "default"}, inhibit_rules=inhibit,
                        code="inhibit-unknown-alertname")
    assert [f.where for f in found] == ["inhibit_rules[0]", "inhibit_rules[1]"]
    assert found[0].msg.startswith("Inhibit rule source matches alertname 'DiskFul'")
    assert "did you mean 'DiskFull'?" in found[0].msg
    assert found[1].msg.startswith('Inhibit rule target matches alertname=~"Node.*"')
    assert "never mutes anything" in found[1].msg
    assert all(f.level == WARN for f in found)


def test_alert_hits_default_receiver(tmp_path):
    route = _routes('severity="critical"', 'severity="warning"')
    [f] = _rules_lint(tmp_path, route, code="alert-hits-default-receiver")
    assert f.level == "info" and f.where == "route"
    assert f.msg.startswith("Based on static rule labels, 1 alert with no severity label "
                            "matches no child route and goes to the root receiver 'default': "
                            "QueueBacklog (")
    assert "rules.yml:14)" in f.msg and "route it elsewhere" in f.msg


def test_alert_hits_default_receiver_groups_by_routing_labels(tmp_path):
    # warning has no route: one finding for all of them, another for the rule without severity
    rules = NODE_RULES + "".join(
        f"      - alert: Warn{i}\n        expr: x > {i}\n        labels: {{severity: warning}}\n"
        for i in range(6))
    found = _rules_lint(tmp_path, _routes('severity="critical"'), rules=rules,
                        code="alert-hits-default-receiver")
    assert [f.msg.split(": ")[0] for f in found] == [
        "Based on static rule labels, 7 alerts with severity=warning match no child route and "
        "go to the root receiver 'default'",
        "Based on static rule labels, 1 alert with no severity label matches no child route and "
        "goes to the root receiver 'default'",
    ]
    assert "HighCPUUsage (" in found[0].msg and "Warn3 (" in found[0].msg
    assert "Warn4" not in found[0].msg and "and 2 more." in found[0].msg


def test_alert_hits_default_receiver_skips_templated_labels(tmp_path):
    rules = """\
groups:
  - name: g
    rules:
      - alert: DiskFull
        expr: disk_free_ratio < 0.05
        labels:
          severity: '{{ if lt $value 0.01 }}critical{{ else }}info{{ end }}'
      - alert: QueueBacklog
        expr: queue_depth > 1000
        labels: {severity: info}
"""
    found = _rules_lint(tmp_path, _routes('severity="critical"'), rules=rules,
                        code="alert-hits-default-receiver")
    assert [("QueueBacklog (" in f.msg, "DiskFull" in f.msg) for f in found] == [(True, False)]


def test_alert_hits_default_receiver_skips_labels_no_rule_sets(tmp_path):
    # namespace comes from the PromQL series: it may route HighCPUUsage anywhere
    route = _routes('severity="critical"', 'namespace="prod"')
    assert _rules_lint(tmp_path, route, code="alert-hits-default-receiver") == []


def test_alert_hits_default_receiver_quiet_without_child_routes(tmp_path):
    assert _rules_lint(tmp_path, {"receiver": "default"}) == []


def test_alert_hits_default_receiver_not_for_alerts_reaching_a_child(tmp_path):
    route = {"receiver": "default", "routes": [
        {"matchers": ['alertname=~"HighCPUUsage|DiskFull|QueueBacklog"'], "continue": True,
         "receiver": "log"},
        {"matchers": ['severity="critical"'], "receiver": "pager"},
    ]}
    assert _rules_lint(tmp_path, route) == []


def test_rules_checks_skip_regexes_amlint_cannot_evaluate(tmp_path):
    # \pL: Router refuses the tree and the matcher can't be tested; amlint says nothing
    route = _routes('alertname=~"\\pL+"', 'severity="critical"')
    assert _rules_lint(tmp_path, route) == []


def test_rules_checks_off_without_rules():
    cfg = {"route": _routes('alertname="Nope"'), "receivers": [{"name": "default"}]}
    assert not codes(cfg) & RULE_CODES
    assert not {f.code for f in lint(cfg, rules=[])} & RULE_CODES


def test_router_match_partial():
    from amlint.routing import Router
    router = Router({"receiver": "d", "routes": [
        {"matchers": ['team="infra"', 'env="prod"'], "receiver": "a"},
        {"matchers": ['severity="critical"'], "receiver": "b"},
    ]})
    # team decides the first child whatever env is; only then does severity count
    assert [m.path for m in router.match_partial({"team": "db"}, {"env"})] == [("route",)]
    assert router.match_partial({"team": "infra"}, {"env"}) is None
    [m] = router.match_partial({"severity": "critical"}, {"env"})
    assert m.receiver == "b"
    assert router.match_partial({"team": "db"}, {"severity"}) is None


RULES_AM = """\
global:
  resolve_timeout: 5m
route:
  receiver: default
  routes:
    - matchers: [alertname="HighCpuUsage"]{suppress}
      receiver: default
receivers:
  - name: default
    webhook_configs: [{{url: 'http://fake'}}]
"""


def _rules_dir(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "node.yml").write_text(NODE_RULES)
    (rules / "k8s.yaml").write_text(
        "spec:\n  groups:\n    - name: k8s\n      rules:\n"
        "        - alert: KubePodCrashLooping\n          expr: x > 0\n")
    return rules


def test_cli_check_rules_json(tmp_path, capsys):
    rules = _rules_dir(tmp_path)
    cfg = tmp_path / "am.yml"
    cfg.write_text(RULES_AM.format(suppress=""))
    args = ["check", str(cfg), "--format", "json",
            "--rules", str(rules / "*.yml"), "--rules", str(rules / "*.yaml")]
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    [f] = [f for f in out if f["code"] == "route-unknown-alertname"]
    assert (f["file"], f["line"], f["column"], f["where"]) == (
        str(cfg), 6, 7, "route.routes[0]")
    assert "did you mean 'HighCPUUsage'?" in f["message"]
    assert main([*args, "--strict"]) == 1


def test_cli_check_rules_suppression(tmp_path, capsys):
    rules = _rules_dir(tmp_path)
    cfg = tmp_path / "am.yml"
    cfg.write_text(RULES_AM.format(suppress="  # amlint: disable=route-unknown-alertname"))
    main(["check", str(cfg), "--format", "json", "--rules", str(rules / "*.yml")])
    captured = capsys.readouterr()
    assert "route-unknown-alertname" not in {f["code"] for f in json.loads(captured.out)}
    assert captured.err == ""


def test_cli_check_rules_glob_without_matches_exits_2(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text(RULES_AM.format(suppress=""))
    with pytest.raises(SystemExit) as exc:
        main(["check", str(cfg), "--rules", str(tmp_path / "missing" / "*.yml")])
    assert exc.value.code == 2
    assert "matches no files" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["check", str(cfg), "--rules", str(tmp_path)])
    assert exc.value.code == 2
    assert "is a directory; use a glob like" in capsys.readouterr().err


def test_cli_check_rules_unparsable_exits_2(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text(RULES_AM.format(suppress=""))
    bad = tmp_path / "bad.yml"
    bad.write_text("groups: [\n")
    with pytest.raises(SystemExit) as exc:
        main(["check", str(cfg), "--rules", str(bad)])
    assert exc.value.code == 2
    assert "failed to parse YAML" in capsys.readouterr().err


def test_cli_check_rules_without_alerting_rules_warns(tmp_path, capsys):
    cfg = tmp_path / "am.yml"
    cfg.write_text(RULES_AM.format(suppress=""))
    recording = tmp_path / "recording.yml"
    recording.write_text("groups:\n  - name: r\n    rules:\n      - record: a:b\n"
                         "        expr: sum(up)\n")
    empty = tmp_path / "empty.yml"
    empty.write_text("")
    main(["check", str(cfg), "--format", "json", "--rules", str(recording),
          "--rules", str(empty)])
    captured = capsys.readouterr()
    assert not {f["code"] for f in json.loads(captured.out)} & RULE_CODES
    assert "empty.yml: no rule groups found" in captured.err
    assert "no alerting rules in the --rules files" in captured.err


def test_cli_check_rules_hints_at_shell_expanded_glob(tmp_path, capsys):
    rules = _rules_dir(tmp_path)
    cfg = tmp_path / "am.yml"
    cfg.write_text(RULES_AM.format(suppress=""))
    # what `--rules rules/*.y*ml am.yml` becomes unquoted: the second rule file is a config
    main(["check", "--rules", str(rules / "k8s.yaml"), str(rules / "node.yml"), str(cfg)])
    err = capsys.readouterr().err
    assert "node.yml looks like a Prometheus rule file" in err
    assert "Quote the --rules glob" in err


@pytest.mark.parametrize("name, infos", [("kube-prometheus-style", 2), ("team-routing", 0)])
def test_real_world_configs_with_their_rules(name, infos, capsys):
    """--rules adds no WARN on these, and a deliberate fallback is one finding per severity."""
    config = os.path.join(_REAL_WORLD, f"{name}.yml")
    rules = os.path.join(_REAL_WORLD, "rules", f"{name}.y*ml")
    assert main(["check", "--strict", "--format", "json", config, "--rules", rules]) == 0
    found = [f for f in json.loads(capsys.readouterr().out) if f["code"] in RULE_CODES]
    assert [f["level"] for f in found] == ["info"] * infos


def test_rules_codes_listed_and_explained(capsys):
    from amlint.explains import ALSO_CAUGHT_BY_AMTOOL, EXPLAINS
    assert RULE_CODES <= set(EXPLAINS) and not RULE_CODES & ALSO_CAUGHT_BY_AMTOOL
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert all(code in out for code in RULE_CODES)
    for code in sorted(RULE_CODES):
        assert main(["explain", code]) == 0
        assert "--rules" in capsys.readouterr().out
