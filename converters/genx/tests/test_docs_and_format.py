# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Technology codes, README coverage of every note code, and the format validator (when present)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from ce_genx import import_case
from ce_genx.convert import NOTES, technology

COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[1]


@pytest.mark.parametrize(
    "name, kind, expected",
    [
        ("MA_natural_gas_combined_cycle", "thermal", "gas_cc"),
        ("CT_natural_gas_combustion_turbine", "thermal", "gas_ct"),
        ("ME_nuclear", "thermal", "nuclear"),
        ("unknown_plant", "thermal", "thermal"),
        ("MA_solar_photovoltaic", "vre", "solar_pv"),
        ("CT_offshore_wind", "vre", "wind_offshore"),
        ("ME_onshore_wind_turbine", "vre", "wind_onshore"),
        ("MA_battery", "storage", "battery"),
        ("CT_pumped_hydro", "storage", "pumped_hydro"),
        ("big_store", "storage", "battery"),
        ("ME_conventional_hydroelectric", "hydro", "hydro_reservoir"),
        ("MA_small_hydroelectric", "must_run", "hydro_ror"),
        ("CT_geothermal", "must_run", "geothermal"),
        ("CT_industrial_chp", "must_run", "must_run"),
    ],
)
def test_technology_codes(name, kind, expected):
    assert technology(pd.Series({"resource": name}), kind, {}) == expected


@pytest.mark.parametrize(
    "name, kind, expected",
    [
        ("natural_gas_steam_turbine", "thermal", "gas_steam"),
        ("WA_gas_steam_unit", "thermal", "gas_steam"),
        ("ID_biomass_steam_turbine", "thermal", "biomass"),
        ("OR_hydrogen_combustion_turbine", "thermal", "hydrogen_ct"),
        ("NV_lignite_unit", "thermal", "coal"),
        ("z2_hydrogen_storage", "storage", "hydrogen_storage"),
    ],
)
def test_technology_keyword_order(name, kind, expected):
    row = pd.Series({"resource": name})
    assert technology(row, kind, {}) == expected


def _spec_recommended_codes() -> set[str]:
    import re

    text = (REPO / "spec" / "SPEC.md").read_text(encoding="utf-8")
    section = text[text.index("### 6.4"):text.index("## 7.")]
    return set(re.findall(r"`([a-z][a-z0-9_]*)`", section.split("A code says")[0]))


def test_sample_case_uses_only_recommended_technology_codes(modular_case, tmp_path):
    b = import_case(modular_case, tmp_path / "b")
    codes = set()
    for t in ("generators", "storage", "candidates"):
        df = b.tables.get(t)
        if df is not None and "technology" in df:
            codes |= set(df["technology"])
    recommended = _spec_recommended_codes()
    assert {"gas_cc", "gas_ct", "solar_pv", "hydrogen_storage"} <= recommended
    assert codes <= recommended | {"thermal"}, codes - recommended


def test_resource_type_column_wins_over_name():
    row = pd.Series({"resource": "unit_7", "resource_type": "onshore_wind"})
    assert technology(row, "vre", {}) == "wind_onshore"


def test_readme_documents_every_note_code():
    readme = (COMPONENT / "README.md").read_text()
    missing = [code for code in NOTES if f"`{code}`" not in readme]
    assert not missing, f"README does not document: {missing}"


def _spec_validator():
    """The repository's format validator; skipped when absent, a failure under CE_OPEN_STRICT=1 (CI)."""
    import os

    src = REPO / "spec" / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        import ce_spec.bundle as module
    except ImportError as exc:
        if os.environ.get("CE_OPEN_STRICT") == "1":
            pytest.fail(f"the CE format validator (spec/) is required in strict mode: {exc}")
        pytest.skip(f"the CE format validator is not available: {exc}")
    return module.validate_bundle


@pytest.mark.parametrize("network", ["transport", "dc"])
@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_output_passes_the_format_validator(modular_case, tmp_path, network, fmt):
    validate_bundle = _spec_validator()
    out = tmp_path / "bundle"
    import_case(modular_case, out, network=network, fmt=fmt)
    report = validate_bundle(out)
    assert [(i.code, i.message) for i in report.issues] == []
