# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Building blocks: TOML writer, cost annualisation, CSV conventions, CLI."""

import datetime as dt
import math
import tomllib

import numpy as np
import pandas as pd
import pytest

from ce_pypsa import Bundle, UnsupportedNetworkError, _toml, import_network, read_csv_folder
from ce_pypsa.cli import main
from ce_pypsa.exporter import crf
from ce_pypsa.importer import _annuity, annual_scale
from synthetic import clean_bundle, write_clean_network


def test_toml_writer_round_trips_nested_documents():
    doc = {"a": 1, "b": [1.5, 2.0], "flag": True, "inf": math.inf,
           "time": {"zone": "UTC", "blocks": [{"id": 1, "start": dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc),
                                               "dt_h": [1.0, 0.5]}, {"id": 2, "dt_h": 1.0}]},
           "x_policy": [{"name": 'quote " and \\ backslash', "value": 1e7}]}
    text = _toml.dumps(doc, header="hello")
    assert text.startswith("# hello")
    assert "start = 2030-01-01T00:00:00Z" in text
    assert tomllib.loads(text) == doc


def test_annuity_matches_capital_recovery_factor():
    for r, n in ((0.07, 25.0), (0.0, 20.0), (0.05, math.inf), (0.12, 3.0)):
        assert _annuity(r, n) == pytest.approx(crf(r, n))
    assert _annuity(0.07, 25) == pytest.approx(0.0858, abs=1e-4)


def test_annual_scale():
    assert annual_scale(8760.0) == (8760.0, 1.0)
    assert annual_scale(8784.0) == (8784.0, 1.0)
    assert annual_scale(168.0) == pytest.approx((8760.0, 8760.0 / 168.0))


def test_overnight_cost_is_annualised(tmp_path):
    folder = write_clean_network(tmp_path / "net")
    g = pd.read_csv(folder / "generators.csv")
    g.loc[g["name"] == "C solar", "overnight_cost"] = 600000.0
    g.loc[g["name"] == "C solar", "discount_rate"] = 0.06
    g.loc[g["name"] == "C solar", "lifetime"] = 30.0
    g.loc[g["name"] == "C solar", "fom_cost"] = 9000.0
    g.to_csv(folder / "generators.csv", index=False)
    b = import_network(folder)
    c = b.tables["candidates"].set_index("candidate_id")
    assert c.at["C solar", "capex_per_mw_yr"] == pytest.approx(600000.0 * crf(0.06, 30.0))
    assert c.at["C solar", "fom_per_mw_yr"] == pytest.approx(9000.0)


def test_positional_and_labelled_series_read_the_same(tmp_path):
    """Recent PyPSA indexes time-series files by position, older versions by snapshot label."""
    folder = write_clean_network(tmp_path / "net")
    a = read_csv_folder(folder)
    f = folder / "loads-p_set.csv"
    df = pd.read_csv(f, index_col=0)
    df.index = range(len(df))
    df.to_csv(f)
    snap = pd.read_csv(folder / "snapshots.csv", index_col=0)
    old = pd.DataFrame({"weightings": snap["objective"].to_numpy()}, index=pd.Index(snap["snapshot"], name="name"))
    old.to_csv(folder / "snapshots.csv")
    b = read_csv_folder(folder)
    pd.testing.assert_frame_equal(a.get_series("loads", "p_set"), b.get_series("loads", "p_set"))
    assert list(a.snapshots.index) == list(b.snapshots.index)
    np.testing.assert_allclose(a.snapshots["objective"], b.snapshots["objective"])
    # the very old single 'weightings' column applies to all three weightings
    np.testing.assert_allclose(b.snapshots["stores"], b.snapshots["objective"])


def test_multi_investment_networks_are_refused(tmp_path):
    folder = write_clean_network(tmp_path / "net")
    pd.DataFrame({"period": [2030] * 24, "timestep": range(24), "objective": 1.0}).to_csv(folder / "snapshots.csv")
    with pytest.raises(UnsupportedNetworkError):
        import_network(folder)


def test_block_hours_chunks_the_axis(clean_folder):
    b = import_network(clean_folder, block_hours=6)
    assert [blk["nperiods"] for blk in b.blocks] == [6, 6, 6, 6]
    assert sum(blk["weight_h"] for blk in b.blocks) == pytest.approx(8760.0)
    assert b.validate() == []
    with pytest.raises(ValueError):
        import_network(clean_folder, block_hours=7)


def test_full_year_hourly_network_is_chronological(tmp_path):
    folder = write_clean_network(tmp_path / "net", hours=8760, weight=1.0)
    b = import_network(folder)
    blk = b.blocks[0]
    assert blk["kind"] == "chronological" and blk["weight_h"] == 8760.0 and blk["nperiods"] == 8760
    assert blk["start"] == dt.datetime(2030, 3, 1, tzinfo=dt.timezone.utc)


def test_validate_catches_problems():
    b = clean_bundle()
    b.tables["generators"].loc[0, "bus"] = 99
    b.tables["availability"] = b.tables["availability"][b.tables["availability"]["site_name"] != "wind1"]
    problems = b.validate()
    assert any("unknown bus" in p for p in problems)
    assert any("availability" in p for p in problems)


def test_write_refuses_to_clobber(tmp_path):
    b = clean_bundle()
    b.write(tmp_path / "b")
    with pytest.raises(FileExistsError):
        b.write(tmp_path / "b")
    (tmp_path / "b" / "notes.txt").write_text("keep me")
    b.write(tmp_path / "b", overwrite=True)
    assert (tmp_path / "b" / "notes.txt").read_text() == "keep me"
    assert Bundle.read(tmp_path / "b").validate() == []


def test_cli_import_and_export(clean_folder, tmp_path, capsys):
    assert main(["import", str(clean_folder), str(tmp_path / "b"), "--currency", "USD", "--money-year", "2024"]) == 0
    assert tomllib.loads((tmp_path / "b" / "bundle.toml").read_text())["money"] == {"currency": "USD",
                                                                                    "dollar_year": 2024}
    assert main(["export", str(tmp_path / "b"), str(tmp_path / "p"), "--no-unit-commitment"]) == 0
    gens = pd.read_csv(tmp_path / "p" / "generators.csv", index_col=0)
    assert not gens["committable"].astype(str).eq("True").any()
    out = capsys.readouterr().out
    assert "pypsa -> ce" in out and "ce -> pypsa" in out


def test_no_json_written(clean_folder, tmp_path):
    import_network(clean_folder, tmp_path / "b")
    from ce_pypsa import export_bundle

    export_bundle(tmp_path / "b", tmp_path / "p")
    written = [p.name for p in (tmp_path / "b").iterdir()] + [p.name for p in (tmp_path / "p").iterdir()]
    assert not [n for n in written if n.endswith(".json")]


@pytest.mark.parametrize("table,column", [("candidates", "capex_gbp_per_mw"), ("candidates", "capex_gbp_per_mwh"),
                                          ("hvdc", "capex_gbp_per_mw")])
def test_ambiguous_legacy_cost_columns_are_refused_on_read(table, column):
    from ce_pypsa.bundle import ALIASES, _normalise_read

    assert column not in ALIASES.get(table, {})
    with pytest.raises(ValueError, match="ambiguous_legacy_cost"):
        _normalise_read(table, pd.DataFrame({column: [1.0]}))


def test_a_link_named_like_a_line_gets_a_distinct_corridor_name(toy_folder, tmp_path):
    """CE lines and hvdc corridors share one name space (flows.line names either)."""
    import shutil

    src = tmp_path / "net"
    shutil.copytree(toy_folder, src)
    links = (src / "links.csv").read_text().replace("North-South HVDC,", "North-South,", 1)
    (src / "links.csv").write_text(links)
    import_network(src, tmp_path / "b", fmt="csv")
    lines = pd.read_csv(tmp_path / "b" / "lines.csv")["name"].tolist()
    hvdc = pd.read_csv(tmp_path / "b" / "hvdc.csv")["name"].tolist()
    assert "North-South" in lines and not set(lines) & set(hvdc)
    assert "North-South_2" in hvdc
