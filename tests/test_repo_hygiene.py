# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Repository-wide checks: every Python file carries the licence header and
every installable component ships the licence text in its distributions.

Run from the repository root: ``python -m pytest``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADER = (
    "# Copyright 2026 Compounding Energy Ltd",
    "# SPDX-License-Identifier: Apache-2.0",
)
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "__pycache__", ".pytest_cache"}


def _walk(pattern: str) -> list[Path]:
    out = []
    for p in ROOT.rglob(pattern):
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        out.append(p)
    return sorted(out)


PY_FILES = _walk("*.py")
COMPONENTS = sorted(p.parent for p in _walk("pyproject.toml"))


def test_files_were_found():
    assert len(PY_FILES) > 50
    assert {c.relative_to(ROOT).as_posix() for c in COMPONENTS} >= {
        "spec", "client", "results", "converters/genx", "converters/pypsa", "reference/papers"}


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_python_file_has_licence_header(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]  # a shebang may precede the header
    assert tuple(lines[:2]) == HEADER, "first two lines must be the copyright and SPDX lines"


@pytest.mark.parametrize("component", COMPONENTS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_component_ships_licence_and_notice(component: Path):
    for name in ("LICENSE", "NOTICE"):
        f = component / name
        assert f.is_file(), f"{name} missing"
        assert f.read_bytes() == (ROOT / name).read_bytes(), f"{name} differs from the root copy"
    project = tomllib.loads((component / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project.get("license") == "Apache-2.0"
    assert set(project.get("license-files", ())) >= {"LICENSE", "NOTICE"}


def test_readme_component_table_matches_the_tree():
    import re

    text = (ROOT / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([^`]+)/` \|", text, flags=re.M))
    assert listed == {c.relative_to(ROOT).as_posix() for c in COMPONENTS}


@pytest.mark.parametrize("component", COMPONENTS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_package_readme_has_no_relative_links(component: Path):
    """A package README is its PyPI page, where links relative to the repository do not resolve."""
    import re

    readme = tomllib.loads((component / "pyproject.toml").read_text(encoding="utf-8"))["project"]["readme"]
    text = (component / readme).read_text(encoding="utf-8")
    relative = [t for t in re.findall(r"\]\(([^)\s]+)\)", text) if not t.startswith(("http://", "https://", "#"))]
    assert relative == [], relative
