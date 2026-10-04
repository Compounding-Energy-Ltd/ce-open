# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: the synthetic sample case written into a temporary folder."""

from __future__ import annotations

import pytest

from ce_genx.samples import write_sample_case


@pytest.fixture
def modular_case(tmp_path):
    return write_sample_case(tmp_path / "modular_case", "modular")


@pytest.fixture
def legacy_case(tmp_path):
    return write_sample_case(tmp_path / "legacy_case", "legacy")


def set_setting(case_dir, key: str, value) -> None:
    """Change one line of the sample case's settings file."""
    for sub in ("settings", "Settings"):
        path = case_dir / sub / "genx_settings.yml"
        if path.exists():
            lines = [ln for ln in path.read_text().splitlines() if not ln.startswith(f"{key}:")]
            lines.append(f"{key}: {value}")
            path.write_text("\n".join(lines) + "\n")
            return
    raise FileNotFoundError("no settings file in the sample case")
