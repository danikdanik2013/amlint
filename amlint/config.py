"""Load project-level amlint config from .amlint.yml or pyproject.toml [tool.amlint]."""

import glob
import os
import sys
from typing import List, Optional

import yaml

_YAML_FILENAMES = (".amlint.yml", ".amlint.yaml")


class ConfigError(ValueError):
    """A project config value amlint can't use."""


def tests_for_config(proj: dict, config: str, base: str) -> Optional[List[str]]:
    """The `amlint test` files that `tests:` in the project config maps `config` to, or
    None when it maps nothing to it. `tests:` maps config files to a glob or a list of
    globs; both are relative to `base`, the directory the project config is in:

        tests:
          alertmanager.yml: tests/routing.yml
    """
    mapping = proj.get("tests") if isinstance(proj, dict) else None
    if mapping is None:
        return None
    if not isinstance(mapping, dict):
        raise ConfigError("tests: in .amlint.yml must map config files to test files, "
                          "e.g. 'alertmanager.yml: tests/routing.yml'")
    target = os.path.normcase(os.path.abspath(config))
    for key, value in mapping.items():
        path = os.path.join(base, os.path.expanduser(str(key)))
        if os.path.normcase(os.path.abspath(path)) != target:
            continue
        patterns = [value] if isinstance(value, str) else value
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise ConfigError(f"tests: in .amlint.yml: {key!r} must map to a glob or a list "
                              f"of globs")
        files: List[str] = []
        for pattern in patterns:
            full = os.path.join(base, os.path.expanduser(pattern))
            matched = sorted(f for f in glob.glob(full, recursive=True) if os.path.isfile(f))
            if not matched:
                raise ConfigError(f"tests: in .amlint.yml: {pattern!r} matches no files")
            files += [f for f in matched if f not in files]
        return files
    return None


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
