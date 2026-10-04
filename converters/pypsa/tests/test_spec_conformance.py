# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Bundles written by the importer pass the repository's reference validator (spec/), when present."""

import pytest

from ce_pypsa import Bundle, import_network
from synthetic import clean_bundle


def _errors(report):
    return [i for i in report.issues if getattr(i, "severity", "error") == "error"]


@pytest.mark.parametrize("fmt", ["parquet", "arrow", "csv"])
def test_imported_toy_bundle_is_valid(spec_validator, toy_folder, tmp_path, fmt):
    import_network(toy_folder, tmp_path / "b", fmt=fmt)
    report = spec_validator(tmp_path / "b")
    assert _errors(report) == [], report.issues


def test_imported_clean_bundle_is_valid(spec_validator, clean_folder, tmp_path):
    import_network(clean_folder, tmp_path / "b", block_hours=6)
    assert spec_validator(tmp_path / "b").issues == []


def test_synthetic_ce_bundle_is_valid(spec_validator, tmp_path):
    clean_bundle().write(tmp_path / "b")
    assert spec_validator(tmp_path / "b").issues == []
    Bundle.read(tmp_path / "b")
