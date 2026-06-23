"""Load project-level amlint config from .amlint.yml or pyproject.toml [tool.amlint]."""

import os
import sys

import yaml

_YAML_FILENAMES = (".amlint.yml", ".amlint.yaml")


def _load_toml(path: str) -> dict:
    """Read [tool.amlint] from pyproject.toml. Works on Python 3.9+."""
    if sys.version_info >= (3, 11):
        import tomllib
        with open(path, "rb") as f:
            data = tomllib.load(f)
    else:
        try:
            import tomli
            with open(path, "rb") as f:
                data = tomli.load(f)
        except ImportError:
            # tomli not installed and Python < 3.11 — skip pyproject.toml
            return {}
    return data.get("tool", {}).get("amlint", {})


def load_project_config(cwd=None) -> dict:
    """
    Load amlint config. Search order (first found wins):
      1. .amlint.yml / .amlint.yaml
      2. pyproject.toml [tool.amlint]
    """
    search = cwd or os.getcwd()
    for name in _YAML_FILENAMES:
        path = os.path.join(search, name)
        if os.path.exists(path):
            with open(path) as f:
                return yaml.safe_load(f) or {}
    toml_path = os.path.join(search, "pyproject.toml")
    if os.path.exists(toml_path):
        return _load_toml(toml_path)
    return {}
