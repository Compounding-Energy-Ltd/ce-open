# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import pytest

from ce_studio import Refusal, describe, parse_detail
from ce_studio.refusals import catalog, from_row, is_known_code, looks_like_code

CATEGORIES = {
    "scope", "delta", "placement", "cost", "storage", "sizing", "cf", "lines", "ties",
    "force_builds", "remove_assets", "loads", "policy", "regulation", "hosting", "run_setting",
    "run", "qa", "warning", "client",
}


def test_catalog_entries_are_complete():
    cat = catalog()
    assert len(cat) > 60
    for code, info in cat.items():
        assert looks_like_code(code), code
        assert info.category in CATEGORIES, (code, info.category)
        assert info.summary.strip() and info.fix.strip(), code
        assert info.known
        assert info.is_warning == code.startswith("warn_"), code


def test_describe_known_family_and_unknown():
    assert describe("out_of_scope").category == "placement"
    assert describe("out_of_scope").known
    fam = describe("bad_reinforce_hops")
    assert (fam.category, fam.known, fam.code) == ("run_setting", False, "bad_reinforce_hops")
    assert describe("warn_something_new").is_warning
    assert describe("policy_period_missing").category == "policy"
    assert describe("regulation_needs_states").category == "regulation"
    assert describe("bus_limit_boundary").category == "hosting"
    assert describe("bad_new_knob").category == "run_setting"
    unk = describe("brand_new_code")
    assert unk.category == "unknown" and not unk.known


def test_parse_failure_detail_rows_and_more_marker():
    detail = (
        "add_candidates[3] bus_id `site-7` out_of_scope: bus 912 is outside the scope; "
        "add_loads[1] flex_fraction `dc-1` bad_flex_fraction: flex_fraction 1.4 outside [0, 1]; "
        "delta.toml[0]  scope_missing: no bus set; (+4 more)"
    )
    parsed = parse_detail(detail)
    assert parsed.codes == ["out_of_scope", "bad_flex_fraction", "scope_missing"]
    assert parsed.omitted == 4
    first = parsed.refusals[0]
    assert (first.table, first.row, first.column, first.id) == ("add_candidates", 3, "bus_id", "site-7")
    assert first.message == "bus 912 is outside the scope"
    assert first.category == "placement"
    last = parsed.refusals[-1]
    assert (last.table, last.row, last.column, last.id) == ("delta.toml", 0, "", "")
    assert last.message == "no bus set"


def test_parse_row_without_id_and_message_with_semicolon():
    detail = ("remove_assets[2] technology unknown_asset: no fleet row; check the bus; "
              "force_builds[1] mw `p1` nonpositive_mw: mw must be > 0")
    parsed = parse_detail(detail)
    assert parsed.codes == ["unknown_asset", "nonpositive_mw"]
    assert parsed.refusals[0].id == ""
    assert parsed.refusals[0].message == "no fleet row; check the bus"
    assert parsed.refusals[1].id == "p1"


def test_parse_clipped_detail_keeps_what_it_can():
    detail = "add_lines[1] to_bus `l1` self_loop: from_bus == to_bus; add_lines[2] mw `l2` nonpositive_mw: mw is ze…"
    parsed = parse_detail(detail)
    assert parsed.codes == ["self_loop", "nonpositive_mw"]
    assert parsed.refusals[1].message.endswith("…")


@pytest.mark.parametrize("detail,code", [
    ("refused (scope_missing): scenario `scn-1` carries no [scope] bus_set", "scope_missing"),
    ("qa_failed: balance_residual,cost_ub_reconciliation", "qa_failed"),
    ("withdrawn: owner request", "withdrawn"),
    ("some_new_stop_required: the pool needs a setting", "some_new_stop_required"),
    ("the run hit capex_overnight_only somewhere", "capex_overnight_only"),
    ("ingest failed: could not fetch the bundle (HTTP 000)", "unclassified"),
])
def test_parse_run_level_details(detail, code):
    parsed = parse_detail(detail)
    assert parsed.codes == [code]
    assert parsed.refusals[0].message


def test_parse_empty_detail():
    for d in (None, "", "   "):
        p = parse_detail(d)
        assert not p and p.codes == [] and p.omitted == 0


def test_from_row_and_default_code():
    r = from_row({"table": "scope", "row": "0", "column": "scope_id", "id": "x", "message": "m"},
                 default_code="base_run_missing")
    assert (r.code, r.row, r.category) == ("base_run_missing", 0, "scope")
    r2 = from_row({"code": "self_loop", "row": "n/a"})
    assert r2.row is None and r2.code == "self_loop"
    assert from_row({}).code == "unclassified"


def test_refusal_str_and_dict():
    r = Refusal.of("out_of_scope", "bus 9 outside", table="add_loads", row=2, column="bus_id", id="dc")
    assert str(r) == "add_loads[2] bus_id `dc` out_of_scope: bus 9 outside"
    d = r.as_dict()
    assert d["code"] == "out_of_scope" and d["fix"] and d["warning"] is False
    bare = Refusal.of("self_loop")
    assert str(bare).startswith("self_loop: ")  # falls back to the catalog summary


def test_code_predicates():
    assert is_known_code("unmappable_bus")
    assert not is_known_code("Region x is not served")
    assert looks_like_code("base_run_missing")
    assert not looks_like_code("Region x is not served")
