# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import math
import tomllib

from ce_studio._toml import dumps


def test_round_trip_through_tomllib():
    doc = {
        "scenario_id": "scn-1",
        "n": 3,
        "gap": 0.0123,
        "ok": True,
        "missing": None,
        "text": 'quote " back\\slash\nnewline\ttab\x01',
        "odd key": "x",
        "codes": ["a", "b", None],
        "summary": {"currency": "USD", "wall_clock": {"solve_s": 61.0}, "none": None},
        "refusal": [{"code": "self_loop", "row": 1}, {"code": "out_of_scope", "row": None}],
        "empty": [],
    }
    back = tomllib.loads(dumps(doc))
    assert back["text"] == doc["text"]
    assert back["codes"] == ["a", "b"]
    assert "missing" not in back and "none" not in back["summary"]
    assert back["summary"]["wall_clock"]["solve_s"] == 61.0
    assert back["refusal"] == [{"code": "self_loop", "row": 1}, {"code": "out_of_scope"}]
    assert back["odd key"] == "x" and back["empty"] == [] and back["ok"] is True


def test_non_finite_floats():
    back = tomllib.loads(dumps({"a": float("nan"), "b": float("inf"), "c": -float("inf")}))
    assert math.isnan(back["a"]) and back["b"] == math.inf and back["c"] == -math.inf
