"""
Drive the real Alertmanager for the differential tests: amtool for routing and config
validation, and an alertmanager process for inhibition. Plus the YAML both sides read.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

Labels = Mapping[str, str]


def find_binary(name: str) -> Optional[str]:
    """$AMTOOL / $ALERTMANAGER if set, else the binary on PATH."""
    return os.environ.get(name.upper()) or shutil.which(name)


@lru_cache(maxsize=None)
def binary_version(path: str) -> str:
    out = subprocess.run([path, "--version"], capture_output=True, text=True).stdout
    # "amtool, version 0.34.1 (branch: HEAD, revision: ...)"
    return out.split("version", 1)[1].split()[0] if "version" in out else "unknown"


def version_tuple(version: str) -> Tuple[int, ...]:
    """(0, 34, 1) for "0.34.1"; () if it doesn't parse."""
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(n) for n in match.groups()) if match else ()


# ── YAML ─────────────────────────────────────────────────────────────

class Plain(str):
    """A string written as a plain (unquoted) YAML scalar even when YAML 1.1 types it,
    e.g. `yes` or `0x1F`. PyYAML then reads a bool or an int where Go's yaml.v2, decoding
    into a string field, keeps the text."""


class _Dumper(yaml.SafeDumper):
    pass


def _represent_plain(dumper, value):
    tag = dumper.resolve(yaml.ScalarNode, str(value), (True, False))
    return dumper.represent_scalar(tag, str(value))


_Dumper.add_representer(Plain, _represent_plain)


def dump(cfg: dict) -> str:
    return yaml.dump(cfg, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000)


def amlint_config(path: str) -> dict:
    """The config as `amlint check` / `route` / `test` read the file."""
    from amlint.formats import load_input

    with open(path, encoding="utf-8") as f:
        return load_input(f.read()).cfg


def write_config(cfg: dict, directory: str, name: str = "alertmanager.yml") -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump(cfg))
    return path


def quote(value: str) -> str:
    """A label value as a double-quoted matcher value that the classic and the UTF-8
    matchers parser read the same way."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def format_labels(labels: Labels) -> str:
    return "{" + ", ".join(f"{k}={quote(v)}" for k, v in sorted(labels.items())) + "}"


# ── amtool ───────────────────────────────────────────────────────────

class AmtoolError(RuntimeError):
    pass


_LOG_LINE = re.compile(r"^time=\S+ level=")


class Amtool:
    def __init__(self, path: str) -> None:
        self.path = path
        self.version = binary_version(path)
        self._pool = ThreadPoolExecutor(max_workers=max(4, (os.cpu_count() or 2) * 2))

    def check_config(self, config_path: str) -> Tuple[bool, str]:
        """(accepted, output) of `amtool check-config`."""
        proc = subprocess.run(
            [self.path, "check-config", config_path], capture_output=True, text=True,
        )
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()

    def routes_test_args(self, config_path: str, labels: Labels) -> List[str]:
        return [self.path, "config", "routes", "test", f"--config.file={config_path}",
                *(f"{k}={quote(v)}" for k, v in sorted(labels.items()))]

    def receivers(self, config_path: str, labels: Labels) -> List[str]:
        """Receivers `amtool config routes test` prints for an alert, in order."""
        proc = subprocess.run(
            self.routes_test_args(config_path, labels), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise AmtoolError(proc.stderr.strip() or proc.stdout.strip())
        # amtool logs matcher parser warnings ("time=... level=WARN ...") to stdout too
        lines = [ln for ln in proc.stdout.splitlines() if ln and not _LOG_LINE.match(ln)]
        return lines[-1].split(",") if lines else []

    def receivers_many(self, config_path: str,
                       label_sets: Sequence[Labels]) -> List[List[str]]:
        return list(self._pool.map(lambda ls: self.receivers(config_path, ls), label_sets))


# ── alertmanager ─────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Alertmanager:
    """A throwaway alertmanager process: no clustering, storage in a temp directory."""

    def __init__(self, path: str, config_path: str) -> None:
        self._dir = tempfile.mkdtemp(prefix="amlint-am-")
        self.url = f"http://127.0.0.1:{_free_port()}"
        self._log = open(os.path.join(self._dir, "log"), "w+")
        self._proc = subprocess.Popen(
            [path, f"--config.file={config_path}", f"--storage.path={self._dir}",
             f"--web.listen-address={self.url[len('http://'):]}",
             "--cluster.listen-address=", "--log.level=warn"],
            stdout=self._log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 15
        while True:
            try:
                if self._request("GET", "/-/ready") is not None:
                    return
            except (urllib.error.URLError, ConnectionError):
                pass
            if self._proc.poll() is not None or time.monotonic() > deadline:
                self.close()
                raise RuntimeError(f"alertmanager did not start:\n{self.log()}")
            time.sleep(0.05)

    def _request(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode()
        return json.loads(text) if text.strip().startswith(("[", "{")) else text

    def post_alerts(self, alerts: Sequence[Labels]) -> None:
        self._request("POST", "/api/v2/alerts", [{"labels": dict(a)} for a in alerts])

    def alerts(self) -> List[dict]:
        return self._request("GET", "/api/v2/alerts")

    def settled_alerts(self, expected: int, timeout: float = 10.0) -> List[dict]:
        """GET /api/v2/alerts once all alerts are in and two reads 0.3s apart agree on
        each alert's inhibition, so the inhibitor has seen every firing alert."""
        deadline = time.monotonic() + timeout
        previous: Optional[Dict[str, tuple]] = None
        time.sleep(0.3)
        while True:
            alerts = self.alerts()
            # whether, not by whom: with several valid sources Alertmanager names any one
            state = {a["fingerprint"]: bool(a["status"]["inhibitedBy"]) for a in alerts}
            if len(alerts) == expected and state == previous:
                return alerts
            if time.monotonic() > deadline:
                raise RuntimeError(f"alertmanager did not settle: {alerts}")
            previous = state
            time.sleep(0.3)

    def log(self) -> str:
        self._log.seek(0)
        return self._log.read()

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._log.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> "Alertmanager":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
