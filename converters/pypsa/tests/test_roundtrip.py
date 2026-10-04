# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Round trips on synthetic systems that use only exactly-representable features."""

import numpy as np
import pandas as pd
import pytest

from ce_pypsa import Bundle, bundle_to_tables, export_bundle, import_network, read_csv_folder
from synthetic import clean_bundle, write_clean_network

ATOL_CF = 1e-6  # availability is stored as float32 by the specification


#: PyPSA defaults for attributes a hand-written CSV may leave blank
DEFAULTS = {"marginal_cost": 0.0, "capital_cost": 0.0, "standing_loss": 0.0, "p_min_pu": 0.0, "efficiency": 1.0,
            "min_up_time": 0, "min_down_time": 0, "start_up_cost": 0.0, "stand_by_cost": 0.0, "location": ""}


def _assert_attrs(orig: pd.DataFrame, back: pd.DataFrame, names, attrs, rtol=1e-9):
    for n in names:
        for a in attrs:
            o, b = orig.at[n, a], back.at[n, a]
            if not isinstance(o, str) and pd.isna(o) and a in DEFAULTS:
                o = DEFAULTS[a]
            if isinstance(o, str) or isinstance(b, str):
                assert str(o) == str(b), (n, a, o, b)
            else:
                assert np.isclose(float(o), float(b), rtol=rtol, atol=1e-9), (n, a, o, b)


def _ext(df, n):
    return str(df.at[n, "p_nom_extendable"] if "p_nom_extendable" in df else df.at[n, "s_nom_extendable"]) == "True"


# --------------------------------------------------------------------------- PyPSA -> CE -> PyPSA
@pytest.mark.parametrize("via_disk", [False, True])
def test_pypsa_ce_pypsa_is_exact(clean_folder, tmp_path, via_disk):
    orig = read_csv_folder(clean_folder)
    if via_disk:
        import_network(clean_folder, tmp_path / "bundle", currency="EUR")
        tables, _ = export_bundle(tmp_path / "bundle", tmp_path / "back")
        back = read_csv_folder(tmp_path / "back")
    else:
        bundle = import_network(clean_folder)
        assert bundle.validate() == []
        back, _ = bundle_to_tables(bundle)
        # DataFrame.map exists from pandas 2.1; the declared floor is pandas 2.0 (applymap there)
        _elementwise = lambda df, f: df.map(f) if hasattr(df, "map") else df.applymap(f)
        back.static = {k: _elementwise(v, lambda x: str(x) if isinstance(x, (bool, np.bool_)) else x) for k, v in
                       back.static.items()}

    # time axis: labels and all three weightings
    assert list(back.snapshots.index) == list(orig.snapshots.index)
    np.testing.assert_allclose(back.snapshots.to_numpy(), orig.snapshots.to_numpy())

    ob, bb = orig.get("buses"), back.get("buses")
    assert list(bb.index) == list(ob.index)
    _assert_attrs(ob, bb, ob.index, ["v_nom", "x", "y", "location"])

    ol, bl = orig.get("lines"), back.get("lines")
    assert set(bl.index) == set(ol.index)
    _assert_attrs(ol, bl, ol.index, ["bus0", "bus1", "x"])
    for n in ol.index:
        assert str(bl.at[n, "s_nom_extendable"]) == str(ol.at[n, "s_nom_extendable"])
        cols = ["s_nom_min", "s_nom_max", "capital_cost"] if str(ol.at[n, "s_nom_extendable"]) == "True" else ["s_nom"]
        _assert_attrs(ol, bl, [n], cols)
    _assert_attrs(ol, bl, ["B-C"], ["length"])

    ok, bk = orig.get("links"), back.get("links")
    _assert_attrs(ok, bk, ok.index, ["bus0", "bus1", "p_nom_min", "p_nom_max", "capital_cost", "efficiency",
                                     "p_min_pu"])

    og, bg = orig.get("generators"), back.get("generators")
    assert set(bg.index) == set(og.index)
    _assert_attrs(og, bg, og.index, ["bus", "carrier", "marginal_cost"])
    for n in og.index:
        if str(og.at[n, "p_nom_extendable"]) == "True":
            _assert_attrs(og, bg, [n], ["p_nom_min", "p_nom_max", "capital_cost"])
            assert str(bg.at[n, "p_nom_extendable"]) == "True"
        else:
            _assert_attrs(og, bg, [n], ["p_nom"])
    _assert_attrs(og, bg, ["B nuclear"], ["p_min_pu", "min_up_time", "min_down_time", "start_up_cost",
                                          "stand_by_cost"])
    assert str(bg.at["B nuclear", "committable"]) == "True"
    assert str(bg.at["A CCGT", "committable"]) == "False"
    _assert_attrs(og, bg, ["A CCGT"], ["ramp_limit_up", "ramp_limit_down"])
    op, bp = orig.get_series("generators", "p_max_pu"), back.get_series("generators", "p_max_pu")
    np.testing.assert_allclose(bp[op.columns].to_numpy(), op.to_numpy(), atol=ATOL_CF)

    os_, bs = orig.get("storage_units"), back.get("storage_units")
    _assert_attrs(os_, bs, os_.index, ["bus", "carrier", "max_hours", "efficiency_store", "efficiency_dispatch",
                                       "marginal_cost"])
    _assert_attrs(os_, bs, ["B battery"], ["p_nom_min", "p_nom_max", "capital_cost", "standing_loss"])
    _assert_attrs(os_, bs, ["A PHS"], ["p_nom"])

    assert list(back.get("loads").index) == list(orig.get("loads").index)
    np.testing.assert_allclose(back.get_series("loads", "p_set")[["A", "B", "C"]].to_numpy(),
                               orig.get_series("loads", "p_set")[["A", "B", "C"]].to_numpy())

    oc, bc = orig.get("carriers"), back.get("carriers")
    for carrier in ("CCGT", "OCGT"):
        assert np.isclose(float(bc.at[carrier, "co2_emissions"]) / float(bg.loc[bg["carrier"] == carrier,
                          "efficiency"].iloc[0]), float(oc.at[carrier, "co2_emissions"]))
    assert np.isclose(float(back.get("global_constraints").at["CO2Limit", "constant"]),
                      float(orig.get("global_constraints").at["CO2Limit", "constant"]))


def test_short_horizon_scales_to_a_year_and_back(tmp_path):
    """A 48-hour horizon (weights 1) is scaled to a 8760-hour year on import and restored on export."""
    folder = write_clean_network(tmp_path / "net", hours=48, weight=1.0)
    bundle = import_network(folder)
    s = 8760.0 / 48.0
    assert bundle.blocks[0]["weight_h"] == pytest.approx(8760.0)
    assert bundle.descriptor["x_pypsa"]["annual_scale"] == pytest.approx(s)
    cands = bundle.tables["candidates"].set_index("candidate_id")
    assert cands.at["C solar", "capex_per_mw_yr"] == pytest.approx(50000.0 * s)
    lines = bundle.tables["lines"].set_index("name")
    assert lines.at["B-C", "capex_per_yr"] == pytest.approx(25.0 * s * 1000.0)
    assert bundle.descriptor["x_policy"][0]["value"] == pytest.approx(2.5e6 * s)
    assert any("scaled by" in n.message for n in bundle.report.notes)

    back, _ = bundle_to_tables(bundle)
    np.testing.assert_allclose(back.snapshots["objective"], 1.0)
    gens = back.get("generators")
    assert gens.at["C solar", "capital_cost"] == pytest.approx(50000.0)
    assert back.get("global_constraints").at["CO2Limit", "constant"] == pytest.approx(2.5e6)


# --------------------------------------------------------------------------- CE -> PyPSA -> CE
def test_ce_pypsa_ce_is_exact(tmp_path):
    src = clean_bundle()
    assert src.validate() == []
    export_bundle(src, tmp_path / "pypsa")
    back = import_network(tmp_path / "pypsa", block_hours=24, currency="GBP", money_year=2025)
    assert back.validate() == []

    assert back.descriptor["money"] == src.descriptor["money"]
    assert back.years == src.years
    for a, b in zip(src.blocks, back.blocks):
        for key in ("id", "nperiods", "dt_h", "weight_h", "kind", "start"):
            assert a[key] == pytest.approx(b[key]) if isinstance(a[key], float) else a[key] == b[key], key
    assert back.descriptor["x_policy"][0]["value"] == pytest.approx(1.5e6)

    def same(name, cols, key, rtol=1e-9):
        a = src.tables[name].set_index(key)
        b = back.tables[name].set_index(key)
        assert list(b.index) == list(a.index), name
        for col in cols:
            x, y = a[col], b[col]
            if x.dtype == object or isinstance(x.iloc[0], (str, bool, np.bool_)):
                assert list(x) == list(y), (name, col)
            else:
                np.testing.assert_allclose(y.to_numpy(float), x.to_numpy(float), rtol=rtol, err_msg=f"{name}.{col}")

    same("buses", ["id", "zone", "lat", "lon", "voltage_kv"], "name")
    same("lines", ["id", "from_bus", "to_bus", "susceptance_pu", "rating_mw", "existing", "candidate",
                   "delta_rating_mw", "capex_per_yr"], "name")
    same("hvdc", ["from_bus", "to_bus", "existing_mw", "candidate_max_mw", "converter_efficiency",
                  "capex_per_mw_yr"], "name")
    same("generators", ["id", "bus", "technology", "kind", "existing_mw", "varcost_per_mwh", "fom_per_mw_yr",
                        "co2_t_per_mwh"], "name")
    same("storage", ["bus", "technology", "existing_mw", "duration_h", "eta_charge", "eta_discharge",
                     "self_discharge_frac_per_h", "soc_init_frac", "varcost_per_mwh"], "name")
    same("candidates", ["technology", "kind", "bus", "max_mw", "capex_per_mw_yr", "varcost_per_mwh",
                        "co2_t_per_mwh"], "candidate_id")
    key = ["bus", "scenario", "block", "period"]
    a = src.tables["demand"].sort_values(key).reset_index(drop=True)
    b = back.tables["demand"].sort_values(key).reset_index(drop=True)
    np.testing.assert_allclose(b["mw"], a["mw"])
    assert (b[key].to_numpy() == a[key].to_numpy()).all()
    key = ["site_name", "scenario", "block", "period"]
    a = src.tables["availability"].sort_values(key).reset_index(drop=True)
    b = back.tables["availability"].sort_values(key).reset_index(drop=True)
    np.testing.assert_allclose(b["cf"], a["cf"], atol=ATOL_CF)


@pytest.mark.parametrize("fmt", ["parquet", "arrow", "csv"])
def test_bundle_formats_read_back_identically(clean_folder, tmp_path, fmt):
    bundle = import_network(clean_folder, tmp_path / fmt, fmt=fmt)
    again = Bundle.read(tmp_path / fmt)
    assert again.descriptor["time"]["blocks"][0]["weight_h"] == bundle.blocks[0]["weight_h"]
    for name, df in bundle.tables.items():
        got = again.tables[name]
        assert len(got) == len(df), name
        for col in df.columns:
            if col.startswith("x_") and fmt == "csv":
                continue
            x, y = df[col].reset_index(drop=True), got[col].reset_index(drop=True)
            if pd.api.types.is_numeric_dtype(x) and not pd.api.types.is_bool_dtype(x):
                np.testing.assert_allclose(y.astype(float), x.astype(float), atol=ATOL_CF, err_msg=f"{name}.{col}")
            else:
                def txt(v):
                    return "<missing>" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)

                assert [txt(v) for v in x] == [txt(v) for v in y], (name, col)
    assert again.validate() == []
