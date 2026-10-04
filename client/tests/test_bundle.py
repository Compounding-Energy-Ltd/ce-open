# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from ce_studio import DeltaBundle, LocalValidationError, delta_tables
from ce_studio.bundle import MAX_BODY_BYTES, coerce_cell, technology_to_wire

from studio_helpers import delta_toml, write_delta

NAMES = {"north": 1, "south": 2, "coast": 3}


def codes(found):
    return [r.code for r in found]


def resolve(name):
    return NAMES[name]


def test_toy_example_is_clean(toy_delta):
    b = DeltaBundle.load(toy_delta)
    assert b.files == ["delta.toml", "add_candidates.csv", "add_loads.csv"]
    assert b.check() == []
    assert b.name == "toy_flat_load_with_battery"
    assert b.scope_id == "toy_region"
    assert b.buses == ["north", "south", "coast"]
    assert b.rigor_tier == "interactive"
    assert not b.uses_bus_ids()
    assert b.size_bytes > 0


def test_toy_example_wire_body(toy_delta):
    """The request body is the service's wire shape: canonical names renamed, codes mapped, names resolved."""
    body = DeltaBundle.load(toy_delta).to_wire(resolve, submission_ref="ref-00000001")
    assert body == {
        "scope_id": "toy_region",
        "name": "toy_flat_load_with_battery",
        "scope": {"bus_set": [1, 2, 3]},
        "rigor_tier": "interactive",
        "tables": {
            "add_candidates": [{"site_id": "toy-bess-1", "technology": "storage", "bus_id": 2, "max_mw": 50.0,
                                "duration_h": 4.0, "capex_per_mw_annualised": 90000.0, "fom_per_mw": 10000.0,
                                "in_service_year": 2030}],
            "add_loads": [{"load_id": "toy-load-1", "bus_id": 3, "archetype": "flat_95pct", "mw": 100.0,
                           "start_year": 2030}],
        },
        "submission_ref": "ref-00000001",
    }


def test_encode_is_one_json_object(toy_delta):
    data, ctype = DeltaBundle.load(toy_delta).encode(resolve, submission_ref="ref-00000001")
    assert ctype == "application/json"
    assert json.loads(data)["tables"]["add_loads"][0]["bus_id"] == 3


def test_names_need_a_resolver(toy_delta):
    with pytest.raises(LocalValidationError) as exc:
        DeltaBundle.load(toy_delta).to_wire()
    assert set(codes(exc.value.refusals)) == {"unknown_bus_name"}


def test_unknown_bus_name_is_located(toy_delta):
    (toy_delta / "add_loads.csv").write_text("load_id,bus,archetype,mw\nl1,nowhere,flat_95pct,5\n")

    def strict(name):
        if name not in NAMES:
            raise KeyError(name)
        return NAMES[name]

    with pytest.raises(LocalValidationError) as exc:
        DeltaBundle.load(toy_delta).to_wire(strict)
    (r,) = exc.value.refusals
    assert (r.code, r.table, r.row, r.column, r.id) == ("unknown_bus_name", "add_loads", 0, "bus", "nowhere")


def test_integer_ids_pass_through_and_warn_unless_stamped(tmp_path):
    root = write_delta(tmp_path / "d", delta_toml(buses="[1, 2]"), {
        "add_loads.csv": "load_id,bus_id,archetype,mw\nl1,2,flat_95pct,5\n"})
    b = DeltaBundle.load(root)
    assert b.uses_bus_ids()
    assert codes(b.check()) == ["warn_bus_ids_unstamped"] and b.errors() == []
    body = b.to_wire()
    assert body["scope"] == {"bus_set": [1, 2]} and body["tables"]["add_loads"][0]["bus_id"] == 2
    stamped = write_delta(tmp_path / "s", delta_toml(buses="[1, 2]", scope_extra='build = "b1"'))
    assert DeltaBundle.load(stamped).check() == [] and DeltaBundle.load(stamped).build == "b1"


def test_missing_and_unreadable_delta_toml(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "add_loads.csv").write_text("load_id\n")
    assert codes(DeltaBundle.load(tmp_path / "a").check()) == ["delta_toml_missing"]
    write_delta(tmp_path / "b", "[scenario\nname=")
    assert codes(DeltaBundle.load(tmp_path / "b").check()) == ["delta_toml_unreadable"]
    with pytest.raises(LocalValidationError):
        DeltaBundle.load(tmp_path / "does-not-exist")


def test_scope_is_required(tmp_path):
    b = DeltaBundle.load(write_delta(tmp_path / "a", delta_toml(buses="")))
    assert codes(b.check()) == ["scope_missing"]
    with pytest.raises(LocalValidationError) as exc:
        b.raise_for_errors()
    assert codes(exc.value.refusals) == ["scope_missing"]
    both = delta_toml(scope_extra="bus_set = [1]")
    assert codes(DeltaBundle.load(write_delta(tmp_path / "b", both)).check()) == ["bad_scope"]
    no_region = delta_toml().replace('scope_id = "toy_region"\n', "")
    assert codes(DeltaBundle.load(write_delta(tmp_path / "c", no_region)).check()) == ["scope_id_missing"]


@pytest.mark.parametrize("run,code", [
    ('rigor_tier = "fast"', "bad_rigor_tier"),
    ("gap_tolerance = 0", "bad_gap_tolerance"),
    ("gap_tolerance = 1.5", "bad_gap_tolerance"),
    ("gap_tolerance = true", "bad_gap_tolerance"),
    ('gap_tolerance = "0.02"', "bad_gap_tolerance"),
    ('run_qa = "report"', "bad_run_qa"),
    ('assumptions_register = "x"', "bad_run_qa"),
])
def test_run_settings(tmp_path, run, code):
    b = DeltaBundle.load(write_delta(tmp_path / "d", delta_toml(run=run)))
    assert codes(b.check()) == [code]


def test_good_gap_tolerance_passes_and_is_sent(tmp_path):
    b = DeltaBundle.load(write_delta(tmp_path / "d", delta_toml(run="gap_tolerance = 0.05")))
    assert b.check() == []
    assert b.to_wire(resolve)["gap_tolerance"] == 0.05


def test_pass_through_sections_and_unknown_sections(tmp_path):
    text = delta_toml() + '\n[costs]\ncarbon_price_per_t = 50.0\n\n[[policies]]\nname = "p1"\nkind = "x"\n'
    b = DeltaBundle.load(write_delta(tmp_path / "d", text))
    assert b.check() == []
    body = b.to_wire(resolve)
    assert body["costs"] == {"carbon_price_per_t": 50.0} and body["policies"] == [{"name": "p1", "kind": "x"}]
    odd = DeltaBundle.load(write_delta(tmp_path / "e", delta_toml() + "\n[tuning]\nx = 1\n"))
    assert codes(odd.check()) == ["unknown_section"]


def test_file_vocabulary(tmp_path):
    root = write_delta(tmp_path / "d", delta_toml(), {
        "add_lines.json": "{}",
        "my_extra_table.csv": "a\n1\n",
        "cf/site-1.parquet": b"PAR1",
        ".DS_Store": b"\0",
        "cf/.hidden.csv": "x",
    })
    b = DeltaBundle.load(root)
    assert ".DS_Store" not in b.files and "cf/.hidden.csv" not in b.files
    found = b.check()
    assert codes(found) == ["unsupported_file", "unsupported_file", "warn_unknown_table"]
    assert {r.table for r in found} == {"add_lines.json", "cf/site-1.parquet", "my_extra_table.csv"}
    assert "profiles are not uploaded" in found[1].message
    assert "my_extra_table" not in b.to_wire(resolve).get("tables", {})


def test_column_checks(tmp_path):
    root = write_delta(tmp_path / "d", delta_toml(), {
        "add_candidates.csv": ("site_id,candidate_id,technology,bus,max_mw,capex_per_mw,colour\n"
                               "a,a,solar_pv,north,ten,5,red\n"),
        "add_loads.csv": "load_id,bus,mw\nl1,north,5\n",
    })
    found = DeltaBundle.load(root).check()
    by = {(r.code, r.table, r.column) for r in found}
    assert ("duplicate_column", "add_candidates", "candidate_id") in by
    assert ("ambiguous_cost_column", "add_candidates", "capex_per_mw") in by
    assert ("warn_unknown_column", "add_candidates", "colour") in by
    assert ("bad_value", "add_candidates", "max_mw") in by
    assert ("missing_column", "add_loads", "archetype") in by
    amb = next(r for r in found if r.code == "ambiguous_cost_column")
    assert "capex_overnight_per_mw" in amb.message


def test_wire_spellings_are_accepted_except_ambiguous_costs(tmp_path):
    root = write_delta(tmp_path / "d", delta_toml(), {
        "add_candidates.csv": ("site_id,technology,bus_id,max_mw,capex_per_mw_annualised,fom_per_mw\n"
                               "s1,solar,north,10,60000,15000\n")})
    b = DeltaBundle.load(root)
    assert b.check() == []
    row = b.to_wire(resolve)["tables"]["add_candidates"][0]
    assert row == {"site_id": "s1", "technology": "solar", "bus_id": 1, "max_mw": 10.0,
                   "capex_per_mw_annualised": 60000.0, "fom_per_mw": 15000.0}


def test_overnight_cost_travels_under_the_service_name(tmp_path):
    root = write_delta(tmp_path / "d", delta_toml(), {
        "add_candidates.csv": ("candidate_id,technology,bus,max_mw,capex_overnight_per_mw,annualisation_rate,"
                               "lifetime_years\nw1,wind_onshore,south,100,1400000,0.07,25\n")})
    row = DeltaBundle.load(root).to_wire(resolve)["tables"]["add_candidates"][0]
    assert row["capex_per_mw"] == 1400000.0 and row["discount_rate"] == 0.07 and row["technology"] == "wind_on"


def test_body_size_limit(tmp_path):
    rows = "".join(f"l{i},north,flat_95pct,1\n" for i in range(12000))
    root = write_delta(tmp_path / "d", delta_toml(), {"add_loads.csv": "load_id,bus,archetype,mw\n" + rows})
    with pytest.raises(LocalValidationError) as exc:
        DeltaBundle.load(root).encode(resolve, submission_ref="ref-00000001")
    assert codes(exc.value.refusals) == ["payload_too_large"]
    assert MAX_BODY_BYTES == 262144


def test_parquet_tables(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    root = write_delta(tmp_path / "d", delta_toml())
    pq.write_table(pa.table({"load_id": ["l1"], "bus": ["coast"], "archetype": ["flat_95pct"], "mw": [7.5],
                             "start_year": [2030]}), root / "add_loads.parquet")
    b = DeltaBundle.load(root)
    assert b.check() == []
    assert b.to_wire(resolve)["tables"]["add_loads"] == [
        {"load_id": "l1", "bus_id": 3, "archetype": "flat_95pct", "mw": 7.5, "start_year": 2030}]


@pytest.mark.parametrize("value,kind,expected", [
    ("", "float", None), ("2.5", "float", 2.5), (3, "float", 3.0), ("2030", "int", 2030), ("2030.0", "int", 2030),
    ("TRUE", "bool", True), ("north", "bus", "north"), ("7", "bus", 7), (float("nan"), "float", None),
])
def test_coerce_cell(value, kind, expected):
    assert coerce_cell(value, kind) == expected


@pytest.mark.parametrize("value,kind", [("x", "float"), ("inf", "float"), ("2.5", "int"), ("yes", "bool"),
                                        (True, "int")])
def test_coerce_cell_refuses(value, kind):
    with pytest.raises(ValueError):
        coerce_cell(value, kind)


def test_reference_is_complete():
    tables = delta_tables()
    assert set(tables) == {"add_candidates", "add_loads", "add_lines", "add_substations", "force_builds",
                           "remove_assets"}
    for t in tables.values():
        names = [c.name for c in t.columns]
        assert len(names) == len(set(names)), t.name
        wires = [c.wire for c in t.columns]
        assert len(wires) == len(set(wires)), t.name
        for c in t.columns:
            assert c.type in ("string", "int", "float", "bool", "bus") and c.description, (t.name, c.name)
            if "cost" in c.name or "capex" in c.name or "fom" in c.name:
                assert c.unit.startswith("currency/"), (t.name, c.name)
        for refused in t.refused:
            assert t.lookup(refused) is None or t.lookup(refused).name != refused
    assert technology_to_wire("gas_cc") == "ccgt" and technology_to_wire("ccgt") == "ccgt"


def test_delta_reference_document_is_current():
    from pathlib import Path

    from ce_studio.bundle import render_delta_reference

    doc = Path(__file__).resolve().parents[1] / "DELTA_TABLES.md"
    assert doc.read_text(encoding="utf-8") == render_delta_reference()
