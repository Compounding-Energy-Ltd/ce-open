# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The submit wire contract, pinned.

The Studio reads a submit as ONE JSON object (at most 256 KiB). These tests pin
the request the client builds for the example bundle, the keys and table
columns the service reads, and the shape of its reply — so a change on either
side shows up here rather than as a refused submission.
"""

from __future__ import annotations

import json

from ce_studio import DeltaBundle, SubmitReceipt, delta_tables
from studio_helpers import EXAMPLES

#: top-level keys of the submit body the service reads
SERVICE_BODY_KEYS = {
    "scope_id", "name", "scope", "rigor_tier", "gap_tolerance", "notify", "sweep_id", "variant_of",
    "submission_ref", "tables", "costs", "policies", "hosting", "reinforce", "bus_limits", "financing",
    "screening",
}

#: columns the service reads from each table's row objects (its own spellings; the cost-provenance
#: columns it merely carries are left out)
SERVICE_TABLE_COLUMNS = {
    "add_candidates": {
        "site_id", "technology", "max_mw", "bus_id", "lat", "lon", "in_service_year", "duration_h", "solar_mw",
        "storage_mw", "storage_mwh", "hybrid_grid_charging", "nameplate_mw", "dcac_ratio",
        "curtailment_liquidation", "sizing_mode", "tie_line_id", "interconnection_cost_per_kw", "capex_per_mw",
        "capex_per_mw_annualised", "fom_per_mw", "solar_capex_per_mw", "storage_capex_per_mw",
        "storage_capex_per_mwh", "capex_per_mwh", "capex_per_mwh_annualised", "discount_rate",
        "lifetime_years", "dollar_year", "solar_fom_per_mw", "storage_fom_per_mw",
        "solar_capex_per_mw_annualised", "storage_capex_per_mw_annualised",
        "storage_capex_per_mwh_annualised", "provenance", "name", "cf_ref",
    },
    "add_loads": {"load_id", "bus_id", "lat", "lon", "mw", "archetype", "profile_ref", "start_year", "name",
                  "flex_fraction", "flex_cost_per_mwh", "tie_line_id"},
    "add_lines": {"line_id", "from_bus", "to_bus", "mw", "length_km", "voltage_kv", "susceptance", "from_site_id",
                  "to_site_id", "cost_per_mw_km", "capex", "cost_per_mw_km_overnight", "discount_rate",
                  "lifetime_years", "in_service_year", "name"},
    "add_substations": {"substation_id", "lat", "lon", "voltage_kv", "bus_id", "name"},
    "force_builds": {"asset_id", "mw", "mwh", "in_service_year"},
    "remove_assets": {"asset_kind", "asset_id", "bus_id", "retire_year", "name", "provenance"},
}

PINNED_REQUEST = {
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
    "submission_ref": "contract-ref-01",
}

PINNED_REPLY = {
    "scenario_id": "scn-20261001T120000Z-ab12", "status": "queued", "policies": [], "policies_n": 0,
    "policy_stamps": [], "bus_limits": [], "bus_limits_n": 0, "bus_limit_stamps": [], "hosting": None,
    "hosting_written": None, "carbon_price_per_t": None, "financings": [], "screening": None,
    "warnings": ["a note the service attached"],
}


def test_request_for_the_example_is_pinned():
    names = {"north": 1, "south": 2, "coast": 3}
    body, ctype = DeltaBundle.load(EXAMPLES / "toy_delta").encode(names.__getitem__,
                                                                  submission_ref="contract-ref-01")
    assert ctype == "application/json"
    assert json.loads(body) == PINNED_REQUEST


def test_request_uses_only_keys_and_columns_the_service_reads():
    assert set(PINNED_REQUEST) <= SERVICE_BODY_KEYS
    for tname, table in delta_tables().items():
        wires = {c.wire for c in table.columns}
        assert wires <= SERVICE_TABLE_COLUMNS[tname], (tname, wires - SERVICE_TABLE_COLUMNS[tname])
        # profiles are built by the service: the client never sends a file reference
        assert not wires & {"cf_ref", "profile_ref"}


def test_reply_is_parsed():
    r = SubmitReceipt.from_wire(PINNED_REPLY, submission_ref="contract-ref-01")
    assert (r.scenario_id, r.status, r.duplicate, r.submission_ref) == (
        "scn-20261001T120000Z-ab12", "queued", False, "contract-ref-01")
    assert r.warnings == ("a note the service attached",)
    dup = SubmitReceipt.from_wire({**PINNED_REPLY, "duplicate": True, "submission_ref": "contract-ref-01"})
    assert dup.duplicate and dup.submission_ref == "contract-ref-01"
