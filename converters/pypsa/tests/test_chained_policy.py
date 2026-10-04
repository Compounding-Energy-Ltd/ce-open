# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A CO2 cap survives GenX -> CE -> PyPSA: both converters use one ``[[x_policy]]`` shape, and the exporter
reports every extension section it does not apply (nothing is lost silently)."""

from pathlib import Path

import pytest

from ce_pypsa import Bundle
from ce_pypsa.exporter import bundle_to_tables

GENX_SRC = Path(__file__).resolve().parents[2] / "genx" / "src"


@pytest.fixture()
def genx_bundle(tmp_path):
    from conftest import import_sibling

    ce_genx = import_sibling("ce_genx", GENX_SRC)
    from ce_genx.samples import write_sample_case

    case = write_sample_case(tmp_path / "genx_case", "modular")
    ce_genx.import_case(case, tmp_path / "bundle")
    return tmp_path / "bundle"


def test_genx_co2_cap_reaches_pypsa_global_constraints(genx_bundle):
    b = Bundle.read(genx_bundle)
    assert b.descriptor["x_policy"][0]["kind"] == "emission_cap_t"
    tables, rep = bundle_to_tables(b)
    gc = tables.static.get("global_constraints")
    assert gc is not None and "genx_co2_cap_1" in gc.index
    assert gc.at["genx_co2_cap_1", "carrier_attribute"] == "co2_emissions"
    assert not [n for n in rep.notes if n.component == "x_policy"]


def test_unapplied_extension_sections_are_reported(genx_bundle):
    tables, rep = bundle_to_tables(Bundle.read(genx_bundle))
    dropped = {n.component for n in rep.notes if n.kind == "dropped"}
    assert "x_genx" in dropped


def test_zones_naming_every_zone_count_as_system_wide(genx_bundle):
    b = Bundle.read(genx_bundle)
    zones = sorted(set(b.tables["buses"]["zone"].astype(str)))
    b.descriptor["x_policy"][0]["zones"] = zones
    tables, _ = bundle_to_tables(b)
    assert "genx_co2_cap_1" in tables.static["global_constraints"].index
    b.descriptor["x_policy"][0]["zones"] = zones[:1]
    tables, rep = bundle_to_tables(b)
    assert "global_constraints" not in tables.static
    assert any(n.component == "x_policy" and n.kind == "dropped" for n in rep.notes)
