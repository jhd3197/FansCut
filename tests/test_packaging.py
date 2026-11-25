"""Packaging and shipped examples stay consistent with the code."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from fanscut import config

ROOT = Path(__file__).resolve().parent.parent


def _names(specs: list[str]) -> set[str]:
    return {re.split(r"[\[<>=!~ ;]", spec.strip(), maxsplit=1)[0].lower() for spec in specs}


def test_requirements_list_every_core_dependency():
    # requirements.txt is what the Docker image installs; pyproject is what pip does.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    required = [ln.split("#")[0] for ln in lines if ln.strip() and not ln.startswith("#")]
    assert _names(required) == _names(pyproject["project"]["dependencies"])


def _unknown_keys(model: type[BaseModel], data: dict[str, Any], prefix: str = "") -> list[str]:
    out = []
    for key, value in data.items():
        field = model.model_fields.get(key)
        if field is None:
            out.append(prefix + key)
            continue
        sub = field.annotation
        if isinstance(sub, type) and issubclass(sub, BaseModel) and isinstance(value, dict):
            out += _unknown_keys(sub, value, f"{prefix}{key}.")
    return out


def test_the_example_configs_are_valid():
    for name in ("config.example.yaml", "config.docker.example.yaml"):
        data = yaml.safe_load((ROOT / name).read_text(encoding="utf-8")) or {}
        config.parse(data)  # raises on a wrong type
        # a misspelt key would otherwise be ignored without a word
        assert _unknown_keys(config.Config, data) == [], name


def test_the_package_includes_every_subpackage():
    from setuptools import find_packages

    found = set(find_packages(str(ROOT), include=["fanscut", "fanscut.*"]))
    on_disk = {".".join(p.relative_to(ROOT).parts[:-1])
               for p in (ROOT / "fanscut").rglob("__init__.py")}
    assert on_disk <= found


def test_relative_links_in_the_docs_resolve():
    docs = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *(ROOT / "docs").glob("*.md")]
    broken = []
    for doc in docs:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (doc.parent / target).exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert broken == []
