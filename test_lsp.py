"""Tests for `amlint lsp`: a real server process, driven over stdio with LSP messages the
way an editor drives it. Run: python3 -m pytest test_lsp.py -v"""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from amlint.cli import main

ROOT = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 30

CONFIG = """\
global:
  resolve_timeout: 5m
route:
  receiver: default
  group_by: [alertname]
  routes:
    - matchers: [team="db"]
      receiver: db-pager
      group_wait: 10s
      routes:
        - matchers: [severity="critical"]
          receiver: db-oncall
    - matchers: [team="web"]
      receiver: ghost
receivers:
  - name: default
    webhook_configs: [{url: http://example.org/default}]
  - name: db-pager
    webhook_configs: [{url: http://example.org/db}]
  - name: db-oncall
    webhook_configs: [{url: http://example.org/oncall}]
"""
GHOST_LINE = 12  # 0-based line of `- matchers: [team="web"]`, the route with receiver: ghost


class Client:
    """The editor's side: starts `amlint lsp` and speaks the base protocol to it."""

    def __init__(self, cwd) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (ROOT, env.get("PYTHONPATH")) if p)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "amlint.cli", "lsp"], cwd=str(cwd), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.inbox: queue.Queue = queue.Queue()
        self.ids = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            headers = {}
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    self.inbox.put(None)
                    return
                if line == b"\r\n":
                    break
                name, _, value = line.decode("ascii").partition(":")
                headers[name.lower()] = value.strip()
            body = self.proc.stdout.read(int(headers["content-length"]))
            self.inbox.put(json.loads(body.decode("utf-8")))

    def send(self, message: dict) -> None:
        body = json.dumps(dict(message, jsonrpc="2.0")).encode("utf-8")
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def receive(self) -> dict:
        message = self.inbox.get(timeout=TIMEOUT)
        assert message is not None, self.proc.stderr.read().decode()
        assert message["jsonrpc"] == "2.0"
        return message

    def notify(self, method: str, params: dict) -> None:
        self.send({"method": method, "params": params})

    def request(self, method: str, params=None) -> dict:
        """The response to a request; notifications that come first are dropped."""
        self.ids += 1
        self.send({"id": self.ids, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == self.ids:
                return message

    def initialize(self, root=None, encodings=None) -> dict:
        capabilities = {"general": {"positionEncodings": encodings}} if encodings else {}
        result = self.request("initialize", {
            "processId": None, "capabilities": capabilities,
            "rootUri": root.as_uri() if root else None,
        })["result"]
        self.notify("initialized", {})
        return result

    def diagnostics(self, uri: str, version=None) -> list:
        """The diagnostics published for this version of the document."""
        while True:
            message = self.receive()
            if message.get("method") != "textDocument/publishDiagnostics":
                continue
            params = message["params"]
            if params["uri"] == uri and params.get("version") == version:
                return params["diagnostics"]

    def open(self, uri: str, text: str) -> list:
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "yaml", "version": 1, "text": text}})
        return self.diagnostics(uri, 1)

    def change(self, uri: str, text: str, version: int) -> list:
        self.notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": text}]})
        return self.diagnostics(uri, version)

    def hover(self, uri: str, line: int, character: int = 4):
        result = self.request("textDocument/hover", {
            "textDocument": {"uri": uri}, "position": {"line": line, "character": character},
        })["result"]
        if result is None:
            return None
        assert result["contents"]["kind"] == "markdown"
        return result["contents"]["value"]

    def stop(self) -> int:
        assert self.request("shutdown")["result"] is None
        self.notify("exit", {})
        return self.proc.wait(timeout=TIMEOUT)


@pytest.fixture
def lsp(tmp_path):
    clients = []

    def start(**kwargs) -> Client:
        client = Client(tmp_path)
        clients.append(client)
        client.initialize(**kwargs)
        return client

    yield start
    for client in clients:
        if client.proc.poll() is None:
            client.proc.kill()
        client.proc.wait(timeout=TIMEOUT)
        for stream in (client.proc.stdin, client.proc.stdout, client.proc.stderr):
            stream.close()


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _apply(text: str, edit: dict) -> str:
    """Apply a TextEdit (ASCII text, so UTF-16 offsets are character offsets)."""
    lines = text.splitlines(keepends=True)

    def offset(pos):
        return sum(len(line) for line in lines[:pos["line"]]) + pos["character"]

    return (text[:offset(edit["range"]["start"])] + edit["newText"]
            + text[offset(edit["range"]["end"]):])


def _by_code(diagnostics) -> dict:
    return {d.get("code"): d for d in diagnostics}


# ── Lifecycle and protocol ────────────────────────────────────────────

def test_initialize_announces_capabilities(tmp_path):
    client = Client(tmp_path)
    result = client.initialize()
    caps = result["capabilities"]
    assert caps["positionEncoding"] == "utf-16"
    assert caps["textDocumentSync"] == {"openClose": True, "change": 1}
    assert caps["hoverProvider"] is True
    assert caps["codeActionProvider"] == {"codeActionKinds": ["quickfix"]}
    assert result["serverInfo"]["name"] == "amlint"
    assert client.stop() == 0


def test_exit_without_shutdown_exits_1(tmp_path):
    client = Client(tmp_path)
    client.initialize()
    client.notify("exit", {})
    assert client.proc.wait(timeout=TIMEOUT) == 1


def test_protocol_errors(tmp_path):
    client = Client(tmp_path)
    early = client.request("textDocument/hover", {
        "textDocument": {"uri": "file:///x.yml"}, "position": {"line": 0, "character": 0}})
    assert early["error"]["code"] == -32002  # not initialized yet
    client.initialize()
    assert client.request("workspace/symbol", {"query": ""})["error"]["code"] == -32601
    client.notify("workspace/somethingNew", {})  # unknown notifications are ignored
    assert client.request("textDocument/hover", {
        "textDocument": {"uri": "file:///not-open.yml"},
        "position": {"line": 0, "character": 0}})["result"] is None
    assert client.stop() == 0


def test_amlint_lsp_help(capsys):
    with pytest.raises(SystemExit) as e:
        main(["lsp", "--help"])
    assert e.value.code == 0
    assert "Language Server Protocol" in capsys.readouterr().out


# ── Diagnostics ───────────────────────────────────────────────────────

def test_diagnostics_have_the_check_codes_and_positions(lsp, tmp_path, capsys):
    path = tmp_path / "alertmanager.yml"
    path.write_text(CONFIG)
    diagnostics = lsp().open(_uri(path), CONFIG)

    ghost = _by_code(diagnostics)["undefined-receiver"]
    line = CONFIG.splitlines()[GHOST_LINE]
    assert ghost["range"] == {
        "start": {"line": GHOST_LINE, "character": line.index("matchers")},
        "end": {"line": GHOST_LINE, "character": len(line)},
    }
    assert ghost["severity"] == 1
    assert ghost["source"] == "amlint"
    assert "ghost" in ghost["message"]
    assert ghost["codeDescription"]["href"] == \
        "https://danikdanik2013.github.io/amlint/checks/routing/#undefined-receiver"

    # the same findings, at the same line and column, as `amlint check` prints
    main(["check", "--format", "json", str(path)])
    expected = {(f["code"], f["line"] - 1, f["column"] - 1)
                for f in json.loads(capsys.readouterr().out)}
    assert {(d["code"], d["range"]["start"]["line"], d["range"]["start"]["character"])
            for d in diagnostics} == expected


def test_severity_maps_to_lsp(lsp, tmp_path):
    text = CONFIG.replace("global:\n  resolve_timeout: 5m\n", "") + "  - name: spare\n"
    found = _by_code(lsp().open(_uri(tmp_path / "am.yml"), text))
    assert found["undefined-receiver"]["severity"] == 1          # error
    assert found["empty-receiver"]["severity"] == 2              # warn
    assert found["global-resolve-timeout-missing"]["severity"] == 3  # info


def test_lints_the_buffer_not_the_file(lsp, tmp_path):
    path = tmp_path / "alertmanager.yml"
    path.write_text(CONFIG.replace("ghost", "default"))  # clean on disk
    client = lsp()
    uri = _uri(path)
    assert "undefined-receiver" in _by_code(client.open(uri, CONFIG))
    assert client.change(uri, CONFIG.replace("ghost", "default"), 2) == []
    assert "undefined-receiver" in _by_code(client.change(uri, CONFIG, 3))
    client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    assert client.diagnostics(uri) == []  # closing clears them


def test_untitled_buffer(lsp):
    assert "undefined-receiver" in _by_code(lsp().open("untitled:Untitled-1", CONFIG))


@pytest.mark.parametrize("text", [
    "services:\n  web:\n    image: nginx\n    ports: ['80:80']\n",
    "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web}\nspec: {replicas: 2}\n",
    "groups:\n  - name: node\n    rules:\n      - alert: NodeDown\n        expr: up == 0\n",
    "services:\n  web: [\n",       # broken, but not an Alertmanager config
    "- just\n- a list\n",
    "",
], ids=["compose", "deployment", "prometheus-rules", "broken-other", "list", "empty"])
def test_silent_on_other_yaml(lsp, tmp_path, text):
    assert lsp().open(_uri(tmp_path / "other.yaml"), text) == []


def test_yaml_error_in_a_config(lsp, tmp_path):
    text = CONFIG.replace('      receiver: ghost\n', '      receiver: ghost\n     bad: [\n')
    [diagnostic] = lsp().open(_uri(tmp_path / "alertmanager.yml"), text)
    assert diagnostic["severity"] == 1
    assert diagnostic["message"].startswith("Failed to parse YAML: ")
    assert diagnostic["range"]["start"]["line"] == GHOST_LINE + 2


def test_reads_the_formats_check_reads(lsp, tmp_path, capsys):
    """A Kubernetes Secret with the config in a literal block: positions are in the file."""
    path = Path(ROOT, "testdata", "formats", "secret.yaml")
    text = path.read_text().replace('receiver: oncall', 'receiver: nobody')
    diagnostics = lsp().open(_uri(tmp_path / "secret.yaml"), text)
    nobody = _by_code(diagnostics)["undefined-receiver"]
    (tmp_path / "secret.yaml").write_text(text)
    main(["check", "--format", "json", str(tmp_path / "secret.yaml")])
    [expected] = [f for f in json.loads(capsys.readouterr().out)
                  if f["code"] == "undefined-receiver"]
    assert nobody["range"]["start"] == {"line": expected["line"] - 1,
                                        "character": expected["column"] - 1}


EMOJI = """\
global: {resolve_timeout: 5m}
route: {receiver: "🚨"}
receivers:
  - {name: "🚨", webhook_configs: [{send_resolved: true}]}
"""


@pytest.mark.parametrize("encodings,emoji_width", [(None, 2), (["utf-32", "utf-16"], 1)])
def test_columns_count_in_the_negotiated_encoding(lsp, tmp_path, encodings, emoji_width):
    client = lsp(encodings=encodings)
    no_url = _by_code(client.open(_uri(tmp_path / "am.yml"), EMOJI))["webhook-no-url"]
    line = EMOJI.splitlines()[3]
    column = line.index("{send_resolved")  # in characters; the emoji comes before it
    assert no_url["range"]["start"] == {"line": 3, "character": column - 1 + emoji_width}


def test_project_config_from_the_workspace_folder(lsp, tmp_path):
    (tmp_path / ".amlint.yml").write_text(
        "ignore: [global-resolve-timeout-missing]\nseverity:\n  undefined-receiver: warn\n")
    sub = tmp_path / "deploy"
    sub.mkdir()
    text = CONFIG.replace("global:\n  resolve_timeout: 5m\n", "")
    found = _by_code(lsp(root=tmp_path).open(_uri(sub / "alertmanager.yml"), text))
    assert "global-resolve-timeout-missing" not in found
    assert found["undefined-receiver"]["severity"] == 2


def test_comment_problems(lsp, tmp_path):
    text = CONFIG.replace("receiver: ghost",
                          "receiver: ghost  # amlint: disable=undefined-reciever")
    found = lsp().open(_uri(tmp_path / "am.yml"), text)
    typo = [d for d in found if "undefined-reciever" in d["message"]]
    assert len(typo) == 1 and typo[0]["severity"] == 2
    assert "Did you mean 'undefined-receiver'?" in typo[0]["message"]
    assert typo[0]["range"]["start"]["line"] == GHOST_LINE + 1
    assert "undefined-receiver" in _by_code(found)


# ── Quick fixes ───────────────────────────────────────────────────────

def _actions(client, uri, diagnostic, only=None):
    context = {"diagnostics": [diagnostic]}
    if only:
        context["only"] = only
    return client.request("textDocument/codeAction", {
        "textDocument": {"uri": uri}, "range": diagnostic["range"], "context": context,
    })["result"]


@pytest.mark.parametrize("title", ["on this line", "in this file"])
def test_quick_fix_disables_the_finding(lsp, tmp_path, title):
    path = tmp_path / "alertmanager.yml"
    client = lsp()
    uri = _uri(path)
    ghost = _by_code(client.open(uri, CONFIG))["undefined-receiver"]
    actions = {a["title"]: a for a in _actions(client, uri, ghost)}
    assert set(actions) == {"Disable undefined-receiver on this line",
                            "Disable undefined-receiver in this file"}
    action = actions[f"Disable undefined-receiver {title}"]
    assert action["kind"] == "quickfix" and action["diagnostics"] == [ghost]
    [edit] = action["edit"]["changes"][uri]
    fixed = _apply(CONFIG, edit)
    if title == "on this line":
        assert fixed.splitlines()[GHOST_LINE].endswith("  # amlint: disable=undefined-receiver")
    else:
        assert fixed.startswith("# amlint: disable-file=undefined-receiver\n")
    assert "undefined-receiver" not in _by_code(client.change(uri, fixed, 2))
    path.write_text(fixed)
    assert main(["check", str(path)]) == 0


def test_quick_fix_extends_an_existing_comment(lsp, tmp_path):
    text = CONFIG.replace('[team="web"]', '[team="web"]  # amlint: disable=deep-nesting')
    client = lsp()
    uri = _uri(tmp_path / "am.yml")
    ghost = _by_code(client.open(uri, text))["undefined-receiver"]
    [on_line] = [a for a in _actions(client, uri, ghost) if a["title"].endswith("line")]
    fixed = _apply(text, on_line["edit"]["changes"][uri][0])
    assert fixed.splitlines()[GHOST_LINE].endswith(
        "# amlint: disable=deep-nesting,undefined-receiver")
    assert _by_code(client.change(uri, fixed, 2)).keys() <= {None}


def test_no_quick_fix_for_other_kinds(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "am.yml")
    ghost = _by_code(client.open(uri, CONFIG))["undefined-receiver"]
    assert _actions(client, uri, ghost, only=["refactor"]) == []
    assert _actions(client, uri, dict(ghost, source="yaml")) == []


# ── Hover ─────────────────────────────────────────────────────────────

def test_hover_on_a_route(lsp, tmp_path, capsys):
    path = tmp_path / "alertmanager.yml"
    path.write_text(CONFIG)
    client = lsp()
    uri = _uri(path)
    client.open(uri, CONFIG)
    text = client.hover(uri, 11)  # receiver: db-oncall
    assert "**Route `route.routes[0].routes[0]`**" in text
    assert "Matchers: `severity=\"critical\"`" in text
    assert "| receiver | `db-oncall` | set here |" in text
    assert "| group_by | `alertname` | inherited from `route` |" in text
    assert "| group_wait | `10s` | inherited from `route.routes[0]` |" in text
    assert "| group_interval | `5m` | Alertmanager default |" in text
    assert "- `route.routes[0]`: `team=\"db\"`" in text
    assert ("For example, an alert with `team=db, severity=critical` ends here and goes to "
            "`db-oncall`.") in text

    # the example is what `amlint route` says about it
    main(["route", "--format", "json", str(path), "team=db", "severity=critical"])
    routed = json.loads(capsys.readouterr().out)
    assert routed["receivers"] == ["db-oncall"]
    assert routed["routes"][0]["path"][-1] == "route.routes[0].routes[0]"


def test_hover_finds_the_innermost_route(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "am.yml")
    client.open(uri, CONFIG)
    assert "The root route: every alert starts here." in client.hover(uri, 2)    # route:
    assert "**Route `route`**" in client.hover(uri, 4)                         # group_by
    assert "**Route `route.routes[0]`**" in client.hover(uri, 9)               # its routes:
    assert "**Route `route.routes[1]`**" in client.hover(uri, GHOST_LINE + 1)
    assert client.hover(uri, 15) is None                                       # receivers
    assert client.hover(uri, 0) is None                                        # global


def test_hover_on_a_route_nothing_reaches(lsp, tmp_path):
    text = CONFIG.replace('[team="web"]', '[team="db"]').replace("receiver: ghost",
                                                                 "receiver: default")
    client = lsp()
    uri = _uri(tmp_path / "am.yml")
    assert "route-match-collision" in _by_code(client.open(uri, text))
    hover = client.hover(uri, GHOST_LINE)
    assert "reaches this route, so it may never get any" in hover


def test_hover_inside_a_secret(lsp, tmp_path):
    text = Path(ROOT, "testdata", "formats", "secret.yaml").read_text()
    client = lsp()
    uri = _uri(tmp_path / "secret.yaml")
    client.open(uri, text)
    lines = text.splitlines()
    line = lines.index("          receiver: oncall")
    hover = client.hover(uri, line)
    assert "**Route `route.routes[1]`**" in hover
    assert "| receiver | `oncall` | set here |" in hover
    assert "| repeat_interval | `12h` | inherited from `route` |" in hover
    assert client.hover(uri, lines.index("kind: Secret")) is None


def test_hover_when_routing_cannot_be_simulated(lsp, tmp_path):
    text = CONFIG.replace('[team="web"]', '[\'team=~"web(?=x)"\']')  # lookahead: not RE2
    client = lsp()
    uri = _uri(tmp_path / "am.yml")
    client.open(uri, text)
    assert "amlint can't simulate this route tree" in client.hover(uri, GHOST_LINE)


# ── rules: in .amlint.yml ────────────────────────────────────────────

RULES_CONFIG = """\
global:
  resolve_timeout: 5m
route:
  receiver: default
  routes:
    - matchers: [alertname="DiskFull"]
      receiver: default
receivers:
  - name: default
    webhook_configs: [{url: http://example.org/default}]
"""


def _rule_file(path: Path, *alerts: str) -> None:
    rules = "".join(f"      - alert: {a}\n        expr: x > 0\n" for a in alerts)
    path.write_text(f"groups:\n  - name: g\n    rules:\n{rules}")


def test_rules_from_project_config_in_the_editor(lsp, tmp_path):
    (tmp_path / "rules").mkdir()
    _rule_file(tmp_path / "rules" / "node.yml", "DiskFul")
    (tmp_path / ".amlint.yml").write_text("rules: ['rules/*.yml']\n")
    config = tmp_path / "alertmanager.yml"
    client = lsp(root=tmp_path)
    diags = _by_code(client.open(_uri(config), RULES_CONFIG))
    assert "did you mean 'DiskFul'?" in diags["route-unknown-alertname"]["message"]

    # the rule file changes: the next lint reads it again
    _rule_file(tmp_path / "rules" / "node.yml", "DiskFull")
    os.utime(tmp_path / "rules" / "node.yml", ns=(1, 2 * 10 ** 18))
    assert "route-unknown-alertname" not in _by_code(
        client.change(_uri(config), RULES_CONFIG + "\n", 2))


def test_project_rules_that_cant_be_read_are_reported(lsp, tmp_path):
    (tmp_path / ".amlint.yml").write_text("rules: ['missing/*.yml']\n")
    client = lsp(root=tmp_path)
    diags = client.open(_uri(tmp_path / "alertmanager.yml"), RULES_CONFIG)
    [warning] = [d for d in diags if "Prometheus rules" in d["message"]]
    assert warning["severity"] == 2 and warning["range"]["start"]["line"] == 0
    assert "'missing/*.yml' matches no files" in warning["message"]


# ── Outline ───────────────────────────────────────────────────────────

def _symbols(client, uri):
    return client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})["result"]


def _contains(outer: dict, inner: dict) -> bool:
    def key(pos):
        return (pos["line"], pos["character"])
    return key(outer["start"]) <= key(inner["start"]) and key(inner["end"]) <= key(outer["end"])


def _check_nesting(symbols):
    for s in symbols:
        assert s["name"]
        assert _contains(s["range"], s["selectionRange"]), s
        for child in s.get("children", []):
            assert _contains(s["range"], child["range"]), (s["name"], child["name"])
        _check_nesting(s.get("children", []))


def test_outline_shows_routes_and_receivers(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, CONFIG + "inhibit_rules:\n  - source_matchers: [severity=critical]\n"
                "    target_matchers: [severity=warning]\n    equal: [alertname]\n")
    symbols = _symbols(client, uri)
    _check_nesting(symbols)
    route, receivers, inhibit = symbols
    assert (route["name"], route["detail"], route["kind"]) == ("route", "→ default", 3)
    db, web = route["children"]
    assert (db["name"], db["detail"]) == ('team="db"', "→ db-pager")
    assert db["range"]["start"]["line"] == 6
    [critical] = db["children"]
    assert (critical["name"], critical["detail"]) == ('severity="critical"', "→ db-oncall")
    assert (web["name"], web["detail"]) == ('team="web"', "→ ghost")
    assert web["range"]["start"]["line"] == GHOST_LINE
    assert (receivers["name"], receivers["detail"]) == ("receivers", "3")
    assert [(r["name"], r["detail"]) for r in receivers["children"]] == [
        ("default", "webhook"), ("db-pager", "webhook"), ("db-oncall", "webhook")]
    [rule] = inhibit["children"]
    assert (rule["name"], rule["detail"]) == ("severity=critical → severity=warning",
                                              "equal: alertname")


@pytest.mark.parametrize("name", sorted(os.listdir(os.path.join(ROOT, "testdata", "formats"))))
def test_outline_for_every_input_format(lsp, tmp_path, name):
    path = Path(ROOT, "testdata", "formats", name)
    client = lsp()
    client.open(path.as_uri(), path.read_text())
    symbols = _symbols(client, path.as_uri())
    _check_nesting(symbols)
    lines = path.read_text().splitlines()
    for s in symbols:  # every symbol starts at something written in the file
        assert lines[s["range"]["start"]["line"]].strip()


def test_outline_of_other_yaml_is_empty(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "docker-compose.yml")
    client.open(uri, "services:\n  web:\n    image: nginx\n")
    assert _symbols(client, uri) == []


# ── Go to definition, find references, rename ─────────────────────────

NAMES = """\
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: db-pager
      mute_time_intervals: [nights, 'weekends']
    - matchers: [team="web"]
      receiver: "db-pager"
      active_time_intervals:
        - nights
receivers:
  - name: default
    webhook_configs: [{url: http://example.org/default}]
  - {name: db-pager, webhook_configs: [{url: http://example.org/db}]}
time_intervals:
  - name: nights
    time_intervals: [{times: [{start_time: "00:00", end_time: "06:00"}]}]
  - name: weekends
    time_intervals: [{weekdays: [saturday, sunday]}]
"""


def _at(client, method, uri, line, character, **extra):
    params = {"textDocument": {"uri": uri}, "position": {"line": line, "character": character},
              **extra}
    return client.request(method, params)


def _spots(locations):
    return [(loc["range"]["start"]["line"], loc["range"]["start"]["character"],
             loc["range"]["end"]["character"]) for loc in locations]


def test_definition_of_a_receiver_and_a_time_interval(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    result = _at(client, "textDocument/definition", uri, 4, 16)["result"]  # receiver: db-pager
    assert _spots(result) == [(13, 11, 19)]                                # {name: db-pager
    result = _at(client, "textDocument/definition", uri, 9, 11)["result"]  # - nights
    assert _spots(result) == [(15, 10, 16)]
    result = _at(client, "textDocument/definition", uri, 5, 40)["result"]  # 'weekends'
    assert _spots(result) == [(17, 10, 18)]
    assert _at(client, "textDocument/definition", uri, 3, 20)["result"] is None  # a matcher


def test_definition_of_an_undefined_receiver_is_empty(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, CONFIG)
    assert _at(client, "textDocument/definition", uri, GHOST_LINE + 1, 16)["result"] is None


def test_references_of_a_receiver(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    every = _at(client, "textDocument/references", uri, 13, 12,
                context={"includeDeclaration": True})["result"]
    assert _spots(every) == [(4, 16, 24), (7, 17, 25), (13, 11, 19)]
    uses = _at(client, "textDocument/references", uri, 13, 12,
               context={"includeDeclaration": False})["result"]
    assert _spots(uses) == [(4, 16, 24), (7, 17, 25)]


def test_rename_a_receiver_everywhere(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    prepared = _at(client, "textDocument/prepareRename", uri, 4, 18)["result"]
    assert prepared["placeholder"] == "db-pager"
    edit = _at(client, "textDocument/rename", uri, 4, 18, newName="db-oncall")["result"]
    text = NAMES
    for change in sorted(edit["changes"][uri], key=lambda e: (e["range"]["start"]["line"],
                                                             e["range"]["start"]["character"]),
                         reverse=True):
        text = _apply(text, change)
    assert "db-pager" not in text
    assert 'receiver: "db-oncall"' in text and "{name: db-oncall," in text  # quotes kept
    assert "undefined-receiver" not in _by_code(client.change(uri, text, 2))


def test_rename_quotes_a_name_that_needs_it(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    # every `nights` is plain: "yes" would read as a boolean, so it gets quotes
    edits = _at(client, "textDocument/rename", uri, 15, 12, newName="yes")["result"]
    assert [e["newText"] for e in edits["changes"][uri]] == ['"yes"'] * 3
    # 'weekends' keeps its single quotes; the plain definition gets double ones
    edits = _at(client, "textDocument/rename", uri, 17, 12, newName="it's")["result"]
    assert sorted(e["newText"] for e in edits["changes"][uri]) == ['"it\'s"', "it''s"]


def test_rename_to_an_existing_name_fails(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    error = _at(client, "textDocument/rename", uri, 4, 18, newName="default")["error"]
    assert error["code"] == -32803
    assert "already a receiver called 'default'" in error["message"]
    assert _at(client, "textDocument/prepareRename", uri, 3, 20)["result"] is None


# ── Completion ────────────────────────────────────────────────────────

def _complete(client, uri, line, character):
    result = client.request("textDocument/completion", {
        "textDocument": {"uri": uri}, "position": {"line": line, "character": character},
    })["result"]
    return {item["label"]: item for item in result}


def test_complete_receiver_names(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    text = NAMES.replace("      receiver: db-pager\n", "      receiver: db\n", 1)
    client.change(uri, text, 2)
    items = _complete(client, uri, 4, 18)  # after "receiver: db"
    assert set(items) == {"default", "db-pager"}
    edit = items["db-pager"]["textEdit"]
    assert edit["range"] == {"start": {"line": 4, "character": 16},
                             "end": {"line": 4, "character": 18}}
    assert items["db-pager"]["detail"] == "receiver"


def test_complete_while_the_yaml_is_broken(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    # half-typed: the document doesn't parse, so names come from the last version that did
    broken = NAMES.replace("      receiver: db-pager\n", "      receiver: \n  - [\n", 1)
    client.change(uri, broken, 2)
    assert set(_complete(client, uri, 4, 16)) == {"default", "db-pager"}


def test_complete_time_intervals_in_flow_and_block_lists(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    assert set(_complete(client, uri, 5, 29)) == {"nights", "weekends"}  # [n|ights
    assert set(_complete(client, uri, 9, 12)) == {"nights", "weekends"}  # - n|ights


def test_complete_labels_and_values_in_matchers(lsp, tmp_path):
    (tmp_path / "rules").mkdir()
    _rule_file(tmp_path / "rules" / "node.yml", "NodeDown")
    (tmp_path / ".amlint.yml").write_text("rules: ['rules/*.yml']\n")
    client = lsp(root=tmp_path)
    uri = _uri(tmp_path / "alertmanager.yml")
    text = NAMES.replace('[team="web"]', '[team="web", te]', 1)
    client.open(uri, text)
    labels = _complete(client, uri, 6, 31)  # [team="web", te|
    assert {"team", "alertname"} <= set(labels)
    assert labels["alertname"]["detail"] == "label · from rules"
    values = _complete(client, uri, 3, 22)  # [team="d|b"]
    assert values["db"]["detail"] == "team · in the config" and "web" in values
    text = NAMES.replace('[team="db"]', '[team="db", alertname="N"]', 1)
    client.change(uri, text, 2)
    assert set(_complete(client, uri, 3, 40)) == {"NodeDown"}  # alertname="N|
    assert {"team", "alertname"} <= set(_complete(client, uri, 3, 37))  # alertname|=


def test_no_completion_outside_known_places(lsp, tmp_path):
    client = lsp()
    uri = _uri(tmp_path / "alertmanager.yml")
    client.open(uri, NAMES)
    assert _complete(client, uri, 1, 5) == {}   # in the key "receiver"
    other = _uri(tmp_path / "docker-compose.yml")
    client.open(other, "services:\n  web:\n    receiver: \n")
    assert _complete(client, other, 2, 14) == {}
