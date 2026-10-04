# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Checks against PyPSA itself (skipped when pypsa is not installed).

The optimisation checks solve tiny LPs (3 buses, 24-48 snapshots) with HiGHS.
"""

import numpy as np
import pandas as pd
import pytest

from ce_pypsa import Bundle, bundle_to_tables, export_bundle, import_network, to_network
from synthetic import clean_bundle, write_clean_network


def _same_bundle(a: Bundle, b: Bundle):
    assert a.descriptor["time"] == b.descriptor["time"]
    assert set(a.tables) == set(b.tables)
    for name in a.tables:
        x, y = a.tables[name].reset_index(drop=True), b.tables[name].reset_index(drop=True)
        assert list(x.columns) == list(y.columns), name
        for col in x.columns:
            if pd.api.types.is_numeric_dtype(x[col]) and not pd.api.types.is_bool_dtype(x[col]):
                np.testing.assert_allclose(y[col].astype(float), x[col].astype(float), rtol=1e-9, err_msg=name)
            else:
                assert [str(v) for v in x[col]] == [str(v) for v in y[col]], (name, col)


def test_exported_folder_loads_in_pypsa(pypsa, tmp_path):
    export_bundle(clean_bundle(), tmp_path / "net")
    n = pypsa.Network(str(tmp_path / "net"))
    assert len(n.snapshots) == 48 and len(n.buses) == 3
    assert set(n.generators.index) == {"ccgt1", "nuke1", "wind1", "ocgt_new", "solar_new"}
    assert bool(n.generators.at["solar_new", "p_nom_extendable"])
    assert n.lines.at["l23", "s_nom_max"] == pytest.approx(1000.0)
    assert n.snapshot_weightings["objective"].sum() == pytest.approx(8760.0)
    n.consistency_check()


def test_network_object_netcdf_and_folder_import_alike(pypsa, clean_folder, tmp_path):
    from_folder = import_network(clean_folder)
    n = pypsa.Network(str(clean_folder))
    _same_bundle(from_folder, import_network(n))
    n.export_to_netcdf(str(tmp_path / "net.nc"))
    _same_bundle(from_folder, import_network(tmp_path / "net.nc"))
    # a folder written by PyPSA itself (positional time-series files)
    n.export_to_csv_folder(str(tmp_path / "rewritten"))
    _same_bundle(from_folder, import_network(tmp_path / "rewritten"))


def test_standard_line_types_resolved_through_pypsa(pypsa, clean_folder):
    lines = pd.read_csv(clean_folder / "lines.csv")
    lines.loc[lines["name"] == "A-B", "x"] = np.nan
    lines.loc[lines["name"] == "A-B", "type"] = "Al/St 240/40 4-bundle 380.0"
    lines.loc[lines["name"] == "A-B", "length"] = 120.0
    lines.to_csv(clean_folder / "lines.csv", index=False)
    b = import_network(clean_folder)
    n = pypsa.Network(str(clean_folder))
    n.calculate_dependent_values()
    got = b.tables["lines"].set_index("name").at["A-B", "susceptance_pu"]
    assert got == pytest.approx(1.0 / n.lines.at["A-B", "x_pu_eff"] / 100.0)


def test_to_network_and_netcdf_export(pypsa, tmp_path):
    n = to_network(clean_bundle())
    assert len(n.storage_units) == 2
    export_bundle(clean_bundle(), tmp_path / "net.nc")
    assert len(pypsa.Network(str(tmp_path / "net.nc")).generators) == 5


def _solve(n):
    pytest.importorskip("highspy")
    status, condition = n.optimize(solver_name="highs", include_objective_constant=False,
                                   solver_options={"threads": 1, "output_flag": False})
    assert status == "ok", condition
    return float(n.objective)


def test_round_trip_preserves_the_optimum(pypsa, tmp_path):
    """Same LP optimum before and after PyPSA -> CE -> PyPSA (non-committable variant: an LP)."""
    folder = write_clean_network(tmp_path / "net", committable=False)
    original = _solve(pypsa.Network(str(folder)))
    tables, _ = bundle_to_tables(import_network(folder))
    assert _solve(to_network(tables)) == pytest.approx(original, rel=1e-7)


def test_scaled_horizon_scales_the_objective(pypsa, tmp_path):
    """A 48-hour horizon imported without its snapshot sidecar exports as a full-year system whose optimum is
    the original optimum times s = 8760 / 48, with identical capacity decisions."""
    folder = write_clean_network(tmp_path / "net", committable=False, hours=48, weight=1.0)
    n0 = pypsa.Network(str(folder))
    original = _solve(n0)
    bundle = import_network(folder)
    del bundle.tables["x_pypsa_snapshots"]
    n1 = to_network(bundle)
    assert n1.snapshot_weightings["objective"].sum() == pytest.approx(8760.0)
    assert _solve(n1) == pytest.approx(original * 8760.0 / 48.0, rel=1e-7)
    np.testing.assert_allclose(n1.generators.p_nom_opt.sort_index(), n0.generators.p_nom_opt.sort_index(),
                               rtol=1e-6, atol=1e-6)
