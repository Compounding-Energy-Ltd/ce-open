# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The example toy network exercises the lossy mappings; each must be reported, never silent."""

import filecmp
import math

import numpy as np
import pytest

from ce_pypsa import export_bundle, import_network, read_csv_folder
from conftest import EXAMPLES


def notes(bundle_or_report, component=None, attribute=None, kind=None):
    rep = getattr(bundle_or_report, "report", bundle_or_report)
    return rep.find(component=component, attribute=attribute, kind=kind)


def test_committed_example_matches_its_generator(toy_folder):
    committed = EXAMPLES / "toy_network"
    names = sorted(p.name for p in committed.glob("*.csv"))
    assert names == sorted(p.name for p in toy_folder.glob("*.csv"))
    for name in names:
        assert filecmp.cmp(committed / name, toy_folder / name, shallow=False), name


def test_toy_import_structure(toy_folder):
    b = import_network(toy_folder)
    assert b.validate() == []
    assert list(b.tables["buses"]["name"]) == ["North", "South", "East", "Offshore"]
    assert b.tables["buses"].set_index("name").at["North", "zone"] == "NR"
    blk = b.blocks[0]
    assert blk["weight_h"] == pytest.approx(8760.0) and blk["nperiods"] == 24 and blk["kind"] == "representative"
    assert blk["start"].year == 2030
    assert b.descriptor["x_pypsa"]["annual_scale"] == pytest.approx(1.0)


def test_toy_susceptance_conversion(toy_folder):
    lines = import_network(toy_folder).tables["lines"].set_index("name")
    # line: b = V^2 / (x * 100 MVA) = 400^2 / (40 * 100)
    assert lines.at["North-South", "susceptance_pu"] == pytest.approx(400.0 ** 2 / 40.0 / 100.0)
    # transformer: x is per unit on s_nom, so b = s_nom / (x * 100 MVA)
    assert lines.at["Offshore-East", "susceptance_pu"] == pytest.approx(1200.0 / 0.1 / 100.0)
    # static s_max_pu is folded into the rating
    assert lines.at["North-East", "rating_mw"] == pytest.approx(700.0)
    # an extendable branch with s_nom_min = 0 is a greenfield candidate rated at s_nom_max
    se = lines.loc["South-East"]
    assert not se["existing"] and se["candidate"] and se["rating_mw"] == 2000.0
    assert se["capex_per_yr"] == pytest.approx(30.0 * 2000.0)


def test_toy_fleet_mapping(toy_folder):
    b = import_network(toy_folder)
    gens = b.tables["generators"].set_index("name")
    cands = b.tables["candidates"].set_index("candidate_id")
    sto = b.tables["storage"].set_index("name")
    # efficiency folded into the CO2 intensity: 0.2 t/MWh_th / 0.55
    assert gens.at["South CCGT", "co2_t_per_mwh"] == pytest.approx(0.2 / 0.55)
    assert gens.at["South CCGT", "technology"] == "gas_cc"
    assert gens.at["South CCGT", "ramp_frac_per_h"] == pytest.approx(0.6)
    # non-committable minimum output kept and flagged
    assert gens.at["South nuclear", "pmin_frac"] == pytest.approx(0.5) and gens.at["South nuclear", "x_must_run"]
    # extendable assets with p_nom_min = 0 are pure candidates; unbounded ones get the documented cap
    assert "East OCGT" in cands.index and "East OCGT" not in gens.index
    assert cands.at["East solar", "max_mw"] == pytest.approx(1e5)
    assert notes(b, "generators", "p_nom_max", "defaulted")
    # extendable with p_nom_min > 0: existing part + '__expansion' candidate
    assert sto.at["East battery", "existing_mw"] == 300.0
    assert cands.at["East battery__expansion", "max_mw"] == pytest.approx(1200.0)
    assert cands.at["East battery__expansion", "x_eta_charge"] == pytest.approx(0.95)
    # a reservoir that cannot pump becomes availability-bounded generation
    assert gens.at["North hydro", "kind"] == "renewable"
    assert gens.at["North hydro", "technology"] == "hydro_reservoir"
    av = b.tables["availability"]
    cf = av[av["site_name"] == "North hydro"].sort_values("period")["cf"].to_numpy()
    inflow = read_csv_folder(toy_folder).get_series("storage_units", "inflow")["North hydro"].to_numpy()
    np.testing.assert_allclose(cf, np.clip(0.9 * inflow / 300.0, 0, 1), atol=1e-6)
    assert notes(b, "storage_units", "inflow", "approximated")


def test_toy_demand_aggregation_and_co2_cap(toy_folder):
    b = import_network(toy_folder)
    d = b.tables["demand"]
    east = d[d["bus"] == 3].sort_values("period")["mw"].to_numpy()
    loads = read_csv_folder(toy_folder).get_series("loads", "p_set")
    np.testing.assert_allclose(east, loads["East homes"] + loads["East industry"])
    assert notes(b, "loads", "name", "approximated")
    cap = b.descriptor["x_policy"][0]
    # constant over a horizon of 24 snapshots x 365 h -> t/yr
    assert cap["kind"] == "emission_cap_t" and cap["value"] == pytest.approx(4.0e6 * 8760 / (24 * 365))


@pytest.mark.parametrize("component,attribute,kind", [
    ("buses", "carrier", "dropped"),              # the hydrogen bus
    ("links", "bus0/bus1", "dropped"),            # the electrolyser
    ("stores", "component", "dropped"),           # the hydrogen tank
    ("loads", "bus", "dropped"),                  # the hydrogen demand
    ("lines", "r", "dropped"),                    # resistance under DC power flow
    ("transformers", "component", "approximated"),
    ("links", "efficiency", "approximated"),
    ("links", "p_nom_min", "approximated"),
    ("generators", "efficiency", "approximated"),
    ("money", "currency", "defaulted"),
])
def test_toy_lossy_steps_are_reported(toy_folder, component, attribute, kind):
    assert notes(import_network(toy_folder), component, attribute, kind)


def test_toy_report_written_with_bundle(toy_folder, tmp_path):
    import tomllib

    import_network(toy_folder, tmp_path / "b")
    rep = tomllib.loads((tmp_path / "b" / "x_conversion_report.toml").read_text())
    assert rep["direction"] == "pypsa -> ce"
    assert rep["summary"]["dropped"] >= 4
    assert any(n["component"] == "stores" for n in rep["note"])
    prov = (tmp_path / "b" / "buses.parquet.prov").read_text()
    assert "tool=ce-pypsa" in prov and "sha256=" in prov and "rows=4" in prov


def test_toy_round_trip_keeps_the_invertible_parts(toy_folder, tmp_path):
    import_network(toy_folder, tmp_path / "b")
    tables, rep = export_bundle(tmp_path / "b", tmp_path / "back")
    orig, back = read_csv_folder(toy_folder), read_csv_folder(tmp_path / "back")
    assert list(back.get("buses").index) == ["North", "South", "East", "Offshore"]
    assert list(back.snapshots.index) == list(orig.snapshots.index)
    ol, bl = orig.get("lines"), back.get("lines")
    assert math.isclose(float(bl.at["North-South", "x"]), float(ol.at["North-South", "x"]))
    # the transformer comes back as an equivalent line (same susceptance in MW/rad)
    b_line = float(back.get("buses").at["Offshore", "v_nom"]) ** 2 / float(bl.at["Offshore-East", "x"])
    assert b_line == pytest.approx(1200.0 / 0.1)
    sto = back.get("storage_units")
    assert float(sto.at["East battery", "p_nom_min"]) == 300.0 and float(sto.at["East battery", "p_nom_max"]) == 1500.0
    assert (tmp_path / "back" / "x_conversion_report.toml").is_file()
    assert rep.find("money", "currency")
