"""Tests for amlint. Run: python3 -m pytest test_linter.py -v"""

import json
import os
import re

import pytest
import yaml

from amlint.cli import main
from amlint.linter import WARN, lint


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
    assert f.msg.startswith("Based on static rule labels (alertname=QueueBacklog), "
                            "alert 'QueueBacklog' from ")
    assert "rules.yml:14" in f.msg and "root receiver 'default'" in f.msg
    assert "external_labels" in f.msg


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
    assert ["'QueueBacklog'" in f.msg for f in found] == [True]


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


def test_rules_codes_listed_and_explained(capsys):
    from amlint.explains import ALSO_CAUGHT_BY_AMTOOL, EXPLAINS
    assert RULE_CODES <= set(EXPLAINS) and not RULE_CODES & ALSO_CAUGHT_BY_AMTOOL
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert all(code in out for code in RULE_CODES)
    for code in sorted(RULE_CODES):
        assert main(["explain", code]) == 0
        assert "--rules" in capsys.readouterr().out
