"""Load project-level amlint config from .amlint.yml / .amlint.yaml."""

import os

import yaml

_FILENAMES = (".amlint.yml", ".amlint.yaml")


def load_project_config(cwd=None) -> dict:
    search = cwd or os.getcwd()
    for name in _FILENAMES:
        path = os.path.join(search, name)
        if os.path.exists(path):
            with open(path) as f:
                return yaml.safe_load(f) or {}
    return {}
